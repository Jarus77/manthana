"""Update-available notice: the version check, its cache, and where it prints.

Engineers install the agent once (``install.sh``) and then never think about it
again. That is the whole problem: a pinned agent keeps capturing against an org
server that has moved on, and nothing in the CLI ever says so. This module is the
smallest thing that fixes it, modelled on npm's ``update-notifier`` and ``gh``.

Three design choices worth recording, because each had a plausible alternative:

**The cache is a JSON file, not a row in the SQLite store.** Every trivial command
(``manthana version``, ``manthana datahome``) would otherwise have to open the
store, run the migration ladder and take a lock that the ``watch`` daemon is
already holding. A version check must not be able to make ``manthana version``
slower or, worse, block. ``<data home>/update-check.json`` is read with one
``read_text`` and is human-inspectable when someone asks "why is it nagging me".

**"Latest" means *your org's server*, not the newest public tag.** The agent and
the server ship in lockstep, so the number an engineer should converge on is the
one their admin actually deployed — chasing a GitHub tag their org has not rolled
out yet is noise, and GitHub egress is blocked on plenty of corp networks anyway.
So ``/healthz`` (which ``doctor``/``login``/``setup``/``sync --check`` already
call) now carries ``latest_agent_version``, and the GitHub Releases API — the same
source ``install.sh`` reads — is only the fallback for an agent with no server
configured yet.

**Nothing interactive ever waits on the network.** The check is performed either
by the ``watch`` daemon inside its existing loop (most engineers run it under
launchd, so their cache is permanently warm and interactive commands do zero
network I/O), or, on a machine with no daemon, by a detached child process whose
result lands in the cache and is shown on the *next* invocation. That one-run lag
is exactly ``update-notifier``'s bargain and it is the right one: a notice is not
worth a single millisecond of latency on the command the engineer actually ran.

Everything here degrades to silence. A corrupt cache, an unreadable data home, a
dead server, a version string we cannot parse — each one means "no notice", never
an error, because a courtesy notice must never be able to fail a real command.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import IO

from .datahome import resolve_data_home

CACHE_FILENAME = "update-check.json"
CHECK_INTERVAL = timedelta(hours=24)
OPT_OUT_ENV = "MANTHANA_NO_UPDATE_NOTIFIER"
REPO_ENV = "MANTHANA_REPO"
DEFAULT_REPO = "Jarus77/manthana"
HIDDEN_COMMAND = "_update-check"

#: What ``importlib.metadata`` reports when the distribution is not installed (an
#: editable checkout run via ``python -m``, say). Note this string is *valid*
#: PEP 440 — ``Version("0+unknown")`` parses happily as 0 with a local segment —
#: so it has to be rejected by name, not by catching InvalidVersion. Comparing it
#: would tell every developer working from source that an update is available.
UNKNOWN_VERSION = "0+unknown"

#: Set by the ``watch`` daemon and by the hidden ``_update-check`` child. Both run
#: with non-tty streams in production (launchd redirects to a log file; the child's
#: streams are /dev/null) so the isatty gate already covers them — but a foreground
#: ``manthana watch`` in a real terminal would otherwise print the notice on every
#: refresh, so the contract is stated explicitly rather than inferred from a fd.
_suppressed = False

#: Environment variables that mean "nobody is reading this terminal". Same set gh
#: checks, minus its Codespaces special-case (Manthana has no Codespaces story).
_CI_ENV = (
    "CI",
    "CONTINUOUS_INTEGRATION",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "TEAMCITY_VERSION",
    "JENKINS_URL",
)


@dataclass(frozen=True)
class UpdateCache:
    """The parsed contents of ``update-check.json``."""

    checked_at: datetime
    latest_version: str
    source: str = "server"


def current_version() -> str:
    """The installed agent version, or ``UNKNOWN_VERSION`` when not installed."""
    try:
        return _pkg_version("manthana")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def cache_path() -> Path:
    return resolve_data_home() / CACHE_FILENAME


def read_cache(path: Path | None = None) -> UpdateCache | None:
    """Load the cache, or None if it is absent, unreadable or malformed.

    Deliberately total: a half-written file (the child process was killed
    mid-write), a file someone hand-edited, or a data home on a filesystem that
    just went away all mean "we don't know the latest version yet", which is the
    same state as a first run.
    """
    target = path or cache_path()
    try:
        data = json.loads(target.read_text())
        checked_at = datetime.fromisoformat(str(data["checked_at"]).replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        latest = str(data["latest_version"]).strip()
        if not latest:
            return None
        return UpdateCache(checked_at, latest, str(data.get("source", "server")))
    except Exception:  # noqa: BLE001 - any defect in the cache degrades to "unknown"
        return None


def write_cache(
    latest_version: str,
    *,
    source: str = "server",
    now: datetime | None = None,
    path: Path | None = None,
) -> bool:
    """Persist the check result. Returns False (never raises) if it could not be written."""
    target = path or cache_path()
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    payload = {
        "checked_at": stamp.isoformat().replace("+00:00", "Z"),
        "latest_version": latest_version,
        "source": source,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a reader must never see a truncated file, and two
        # daemons on one data home must not interleave.
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(target)
        return True
    except OSError:
        return False


def should_check(now: datetime | None = None, *, path: Path | None = None) -> bool:
    """True when the cache is missing or older than ``CHECK_INTERVAL``."""
    cache = read_cache(path)
    if cache is None:
        return True
    return (now or datetime.now(UTC)) - cache.checked_at >= CHECK_INTERVAL


def is_newer(current: str, latest: str) -> bool:
    """PEP 440 comparison — never a string compare (``"0.10.0" < "0.9.0"`` lexically).

    Returns False for anything we cannot confidently order, including the
    not-installed sentinel: silence is always the safe answer.
    """
    from packaging.version import InvalidVersion, Version

    if not latest or current == UNKNOWN_VERSION:
        return False
    try:
        return Version(latest.lstrip("vV")) > Version(current)
    except InvalidVersion:
        return False


def _repo() -> str:
    return os.environ.get(REPO_ENV) or DEFAULT_REPO


def install_command() -> str:
    """The one supported upgrade path.

    ``scripts/install.sh`` uses ``uv tool install --force``, so re-running it on an
    existing install genuinely upgrades. (It did not always: without ``--force``
    ``uv tool install`` prints "already installed" and exits 0, which silently
    pinned every engineer to whatever release they first installed. Pointing at
    anything other than this command — or at a version of it without ``--force`` —
    would produce a notice that can never be acted on.)
    """
    return (
        f"curl -LsSf https://github.com/{_repo()}"
        "/releases/latest/download/install.sh | sh"
    )


def notice(current: str, latest: str, source: str = "server") -> str | None:
    """The stderr notice, or None when no update is available.

    Leading blank line so it detaches from the command's own output (gh does the
    same). The server-sourced wording names the org server explicitly: "your org
    runs a newer build than you" is a different, more actionable fact than "a tag
    exists on GitHub", and the engineer should be able to tell which one they got.
    """
    if not is_newer(current, latest):
        return None
    if source == "server":
        headline = f"  Your org server runs Manthana {latest}; this agent is {current}."
    else:
        headline = f"  A new version of Manthana is available: {current} → {latest}"
    return (
        "\n"
        f"{headline}\n"
        f"  Upgrade:  {install_command()}\n"
        f"  (silence this with {OPT_OUT_ENV}=1)\n"
    )


def fetch_latest(
    server_url: str | None = None, token: str | None = None, *, timeout: float = 5.0
) -> tuple[str | None, str]:
    """Ask the org server, then GitHub. Returns ``(version_or_None, source)``.

    The token is accepted for symmetry with the other callers but ``/healthz`` is
    unauthenticated by design — sending it would be the only place the agent leaks
    a credential to a liveness endpoint.
    """
    import httpx

    if server_url:
        try:
            response = httpx.get(f"{server_url.rstrip('/')}/healthz", timeout=timeout)
            if response.status_code == 200:
                body = response.json()
                if isinstance(body, dict):
                    latest = body.get("latest_agent_version") or body.get("server_version")
                    if latest:
                        return str(latest), "server"
        except Exception:  # noqa: BLE001 - unreachable/old server/garbage body → fall through
            pass
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{_repo()}/releases/latest",
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )
        if response.status_code == 200:
            tag = response.json().get("tag_name")
            if tag:
                return str(tag).lstrip("vV"), "github"
    except Exception:  # noqa: BLE001 - no egress, rate limit, no releases yet → silence
        pass
    return None, "none"


def refresh(
    server_url: str | None = None,
    token: str | None = None,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> str | None:
    """Perform the check (if due) and update the cache; return a *newly seen* newer version.

    Returns None when the check was not due, failed, or reported nothing the caller
    did not already know — so the ``watch`` daemon can log exactly once per new
    release instead of every cycle.
    """
    try:
        if not force and not should_check(now):
            return None
        previous = read_cache()
        latest, source = fetch_latest(server_url, token)
        if latest is None:
            return None
        write_cache(latest, source=source, now=now)
        if previous is not None and previous.latest_version == latest:
            return None
        return latest if is_newer(current_version(), latest) else None
    except Exception:  # noqa: BLE001 - a background refresh must never surface anywhere
        return None


def suppress() -> None:
    """Mark this process as a daemon/child: it refreshes the cache but never notifies."""
    global _suppressed
    _suppressed = True


def _streams_are_tty() -> bool:
    """Both stdout and stderr must be terminals (gh checks both).

    stdout because a notice alongside piped output means the engineer is scripting
    and something downstream is parsing us; stderr because that is where we would
    actually print. Read through ``sys`` at call time, not captured at import, so a
    wrapper that reassigns the streams is honoured.
    """
    try:
        return bool(sys.stdout.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):  # detached or already-closed streams
        return False


def notifier_enabled() -> bool:
    """All the gates a human-facing notice has to pass.

    CI is excluded even when a tty is attached — build logs are read by nobody and
    "upgrade" is not advice a runner can act on.
    """
    if _suppressed or os.environ.get(OPT_OUT_ENV):
        return False
    if any(os.environ.get(name) for name in _CI_ENV):
        return False
    if not _streams_are_tty():
        return False
    from .config import load_config

    try:
        return load_config().update_notifier
    except Exception:  # noqa: BLE001 - an unparseable config must not silence a real command
        return True


def maybe_notify(stream: IO[str] | None = None) -> bool:
    """Print the cached notice to stderr if one is warranted. Pure cache read, no network."""
    try:
        if not notifier_enabled():
            return False
        cache = read_cache()
        if cache is None:
            return False
        text = notice(current_version(), cache.latest_version, cache.source)
        if text is None:
            return False
        (stream or sys.stderr).write(text)
        return True
    except Exception:  # noqa: BLE001 - the notice is a courtesy; it may not break anything
        return False


def maybe_spawn_check() -> bool:
    """Fork a detached ``manthana _update-check`` when the cache is stale.

    Returns immediately — the child's result is for the *next* invocation. Gated on
    the same conditions as the notice so a scripted or CI run spawns nothing at all.
    """
    try:
        if not notifier_enabled() or not should_check():
            return False
        argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
        command = (
            [argv0, HIDDEN_COMMAND]
            if argv0 and Path(argv0).exists()
            else [sys.executable, "-m", "manthana.agent.cli", HIDDEN_COMMAND]
        )
        subprocess.Popen(  # noqa: S603 - argv is ours; no shell
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survive the parent exiting; never inherit its tty
        )
        return True
    except Exception:  # noqa: BLE001 - no spawn is a fine outcome; a traceback is not
        return False


__all__ = [
    "CACHE_FILENAME",
    "CHECK_INTERVAL",
    "OPT_OUT_ENV",
    "UNKNOWN_VERSION",
    "UpdateCache",
    "cache_path",
    "current_version",
    "fetch_latest",
    "install_command",
    "is_newer",
    "maybe_notify",
    "maybe_spawn_check",
    "notice",
    "notifier_enabled",
    "read_cache",
    "refresh",
    "should_check",
    "suppress",
    "write_cache",
]
