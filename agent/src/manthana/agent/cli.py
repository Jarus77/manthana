"""Manthana local-agent CLI (``manthana``).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from manthana.agent.actions import TriggerEvent, default_dispatcher, tag_all
from manthana.agent.capture import ingest_all
from manthana.agent.compact import compact_pending, compact_session
from manthana.agent.datahome import db_path, resolve_data_home
from manthana.agent.store import Store
from manthana.schemas import ActionOutcome, Mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from manthana.agent.sync_client import SyncClient


def _resolve_server() -> tuple[str | None, str | None]:
    """Server URL + team token: env wins over [server] in manthana.toml."""
    from manthana.agent.config import load_config

    config = load_config()
    base = os.environ.get("MANTHANA_SERVER_URL") or config.server_url
    token = os.environ.get("MANTHANA_TEAM_TOKEN") or config.team_token
    return base, token


def _verify_connection(base: str, token: str) -> tuple[bool, bool]:
    """(server_reachable, token_accepted) — never raises. Reachability = GET /healthz;
    token acceptance = an authed no-op push. Shared by `setup`, `login`, `sync --check`,
    and `doctor`."""
    import httpx
    from manthana.agent.sync_client import SyncClient

    try:
        reachable = httpx.get(f"{base}/healthz", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False, False
    client = SyncClient(base, token)
    try:
        client.push_compactions([])  # 200 = token accepted
        return reachable, True
    except Exception:  # noqa: BLE001 - SyncError OR a transport error → token not verified
        return reachable, False
    finally:
        client.close()


def _sync_pushed(client: SyncClient) -> Callable[[Store], int]:
    """Adapt a SyncClient into the watcher's `sync_fn` (returns #pushed)."""

    def _fn(store: Store) -> int:
        return client.sync(store).pushed

    return _fn


app = typer.Typer(
    help="Manthana — local-first capture of AI coding interactions.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed Manthana version."""
    try:
        typer.echo(_pkg_version("manthana"))
    except PackageNotFoundError:
        typer.echo("0+unknown")


@app.command()
def datahome() -> None:
    """Show the resolved MANTHANA_DATA_HOME and database path."""
    typer.echo(f"data_home: {resolve_data_home()}")
    typer.echo(f"db_path:   {db_path()}")


@app.command()
def login(
    server: str = typer.Option(..., help="org server URL, e.g. https://manthana.yourco.com"),
    token: str = typer.Option(..., help="the team token from your admin (manthana-server onboard)"),
    actor: str = typer.Option("", help="your contributor identity, e.g. you@yourco.com"),
    optimize: bool = typer.Option(True, help="also wire Claude Code through headroom if installed"),
) -> None:
    """One-time: connect this agent to the org server (writes manthana.toml + verifies).

    By default also proactively wires Claude Code through headroom (token reduction)
    when it's installed — --no-optimize to skip.
    """
    import httpx
    from manthana.agent.config import load_config, save_config

    config = load_config()
    config.server_url = server.rstrip("/")
    config.team_token = token
    if actor:
        config.actor = actor
    path = save_config(config)
    typer.echo(f"wrote {path}")
    if optimize:
        from manthana.agent import optimize as opt

        if opt.available():
            result = opt.setup()
            status = result.get("output") or ("wired" if result.get("ok") else "skip")
            typer.echo(f"headroom: {status}")
        else:
            typer.echo(f"optimize: {opt.INSTALL_HINT}")
    try:
        ok = httpx.get(f"{config.server_url}/healthz", timeout=5.0).status_code == 200
    except httpx.HTTPError as exc:
        typer.echo(f"saved, but {config.server_url} is not reachable yet: {exc}")
        return
    typer.echo(f"connected to {config.server_url} {'✓' if ok else '(unexpected response)'}")


@app.command()
def setup(
    invite: str = typer.Argument("", help="the `mia_…` invite from your admin"),
    actor: str = typer.Option("", help="your email (needed only for an open team invite)"),
    service_install: bool = typer.Option(
        True, "--service/--no-service", help="install the auto-capture daemon at login"
    ),
) -> None:
    """One command to onboard: redeem the invite → connect → install auto-capture → first
    capture → confirm. Everything `login` + `service install` + `capture` do, in one step."""
    import httpx
    from manthana.agent.config import Config, save_config
    from manthana.collectors import resolve_actor
    from manthana.schemas import decode_invite

    if not invite:
        invite = typer.prompt("paste your `manthana setup` invite (mia_…)").strip()
    try:
        server_url, code = decode_invite(invite)
    except ValueError as exc:
        typer.echo(f"invalid invite: {exc}")
        raise typer.Exit(code=1) from exc

    who = actor or os.environ.get("MANTHANA_ACTOR") or resolve_actor()
    # Redeem the invite for a team token (the token is never in the invite itself).
    try:
        resp = httpx.post(
            f"{server_url}/v1/enroll", json={"code": code, "actor": who}, timeout=10.0
        )
    except httpx.HTTPError as exc:
        typer.echo(f"could not reach {server_url}: {exc}")
        raise typer.Exit(code=1) from exc
    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text) if resp.content else resp.text
        typer.echo(f"enrollment failed ({resp.status_code}): {detail}")
        raise typer.Exit(code=1)
    data = resp.json()
    token, who = data["token"], data.get("actor", who)

    save_config(Config(server_url=server_url, team_token=token, actor=who))  # chmod 0600
    os.environ["MANTHANA_ACTOR"] = who  # so this process's capture attributes correctly
    reachable, token_ok = _verify_connection(server_url, token)

    from manthana.agent import optimize as opt

    if opt.available():
        opt.setup()

    # Install auto-capture at login (macOS launchd / Linux systemd / Windows task).
    if not service_install:
        daemon = "skipped (--no-service) — run `manthana watch` yourself"
    else:
        try:
            service("install")
            daemon = "installed (runs at login)"
        except typer.Exit:
            daemon = "install failed — run `manthana service install`"

    store = Store.open()
    ingest_all(store)  # first capture
    n_sessions = len(store.list_sessions(limit=1_000_000))

    mark = "✓" if (reachable and token_ok) else "⚠"
    typer.echo("")
    typer.echo(f"{mark} connected as {who} → {server_url}")
    if not (reachable and token_ok):
        typer.echo(f"  (server reachable={reachable}, token accepted={token_ok})")
    typer.echo(f"  captured {n_sessions} session(s) · auto-capture: {daemon}")
    typer.echo("  dashboard: http://127.0.0.1:8765  ·  health check: manthana doctor")


@app.command()
def config() -> None:
    """Show the resolved agent config (token masked)."""
    from manthana.agent.config import config_path, load_config

    cfg = load_config()
    masked = (cfg.team_token[:8] + "…") if cfg.team_token else "(unset)"
    typer.echo(f"config:     {config_path()}")
    typer.echo(f"server_url: {cfg.server_url or '(unset)'}")
    typer.echo(f"team_token: {masked}")
    typer.echo(f"actor:      {cfg.actor or '(from MANTHANA_ACTOR / git / user)'}")
    typer.echo(f"redact:     secrets={cfg.redact_secrets} pii={cfg.redact_pii}")


@app.command()
def doctor() -> None:
    """Health check — is Manthana set up and flowing? Exits non-zero if a critical check fails."""
    import platform

    import httpx
    from manthana.agent.config import load_config
    from manthana.agent.llm import default_provider

    cfg = load_config()
    base, token = _resolve_server()
    failed = False

    def check(ok: bool, label: str, detail: str = "", *, critical: bool = True) -> None:
        nonlocal failed
        mark = "✓" if ok else ("✗" if critical else "•")
        typer.echo(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
        if not ok and critical:
            failed = True

    typer.echo("Manthana doctor")
    check(
        bool(cfg.server_url and cfg.team_token),
        "configured",
        f"server={cfg.server_url or '(unset)'} · actor={cfg.actor or '(auto)'}",
    )
    if base and token:
        reachable, token_ok = _verify_connection(base, token)
        check(reachable, "server reachable", base)
        check(token_ok, "token accepted")
        try:
            ready = httpx.get(f"{base}/readyz", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            ready = False
        check(ready, "server DB ready", critical=False)
    else:
        check(False, "server reachable", "not configured — run `manthana setup <invite>`")

    prov = default_provider().name
    check(
        prov != "mock", "model available (can compact)",
        "" if prov != "mock" else "no claude/codex CLI on PATH", critical=False,
    )
    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{_SERVICE_LABEL}.plist"
        check(plist.exists(), "auto-capture daemon installed", critical=False)

    store = Store.open()
    n_sess = len(store.list_sessions(limit=1_000_000))
    n_comp = len(store.list_compactions(limit=1_000_000))
    last = store.last_sync_at()
    when = f"{last:%Y-%m-%d %H:%M}Z" if last else "never"
    typer.echo(
        f"  • data: {n_sess} sessions · {n_comp} compactions "
        f"({store.count_pending()} pending) · {len(store.synced_ids())} synced · last sync {when}"
    )
    if failed:
        raise typer.Exit(code=1)


@app.command()
def capture() -> None:
    """Ingest all local Claude Code transcripts into the store."""
    store = Store.open()
    results = ingest_all(store)
    sessions = sum(r.session_count for r in results)
    turns = sum(r.turn_count for r in results)
    typer.echo(f"ingested {len(results)} files -> {sessions} sessions, {turns} turns")


@app.command()
def watch(
    interval: float = 5.0,
    auto_compact: bool = True,
    summarized_only: bool = False,
    settle_min: float = 10.0,
    max_per_cycle: int = 5,
    auto_release: bool = True,
    release_min: float = 10.0,
    sync: bool = True,
) -> None:
    """Continuously ingest new/changed Claude Code transcripts (Ctrl-C to stop).

    Auto-compacts Work sessions whose transcript has been quiet for --settle-min minutes
    (re-compacting after a resume); --summarized-only restricts to sessions Claude already
    summarized; --no-auto-compact disables. Auto-releases each compaction --release-min
    minutes after it's built UNLESS you marked the session personal or held it (personal
    never leaves); --no-auto-release disables. With a server configured, auto-syncs
    released compactions (--no-sync to disable).
    """
    from manthana.agent.llm import default_provider
    from manthana.agent.sync_client import SyncClient
    from manthana.agent.watcher import watch as run_watch

    store = Store.open()
    base, token = _resolve_server()
    client: SyncClient | None = None
    sync_fn: Callable[[Store], int] | None = None
    if sync and base and token:
        client = SyncClient(base, token)
        sync_fn = _sync_pushed(client)
        sync_state = "auto-sync on"
    else:
        sync_state = "auto-sync off (no server)" if sync else "auto-sync disabled"
    # Don't auto-compact in the background without a real model (a Mock would write
    # empty compactions).
    if auto_compact and default_provider().name == "mock":
        typer.echo("no claude/codex CLI found — disabling auto-compaction")
        auto_compact = False
    if not auto_compact:
        compact_state = "compact off"
    else:
        scope = "summarized-only" if summarized_only else "all settled (raw too)"
        compact_state = (
            f"auto-compact {scope} after {settle_min}m quiet, up to {max_per_cycle}/cycle"
        )
    release_state = (
        f"auto-release after {release_min}m (opt-out)" if auto_release else "release manual"
    )
    typer.echo(
        f"watching ~/.claude/projects every {interval}s "
        f"({compact_state}; {release_state}; {sync_state}) — Ctrl-C to stop"
    )
    try:
        run_watch(
            store,
            interval=interval,
            auto_compact=auto_compact,
            summarized_only=summarized_only,
            settle_seconds=settle_min * 60.0,
            max_per_cycle=max_per_cycle,
            auto_release=auto_release,
            release_window=release_min * 60.0,
            sync_fn=sync_fn,
            log=typer.echo,
        )
    except KeyboardInterrupt:
        typer.echo("\nstopped")
    finally:
        if client is not None:
            client.close()
        store.close()  # dispose the SQLite engine pool on exit


@app.command()
def sessions(limit: int = 20) -> None:
    """List captured sessions (most recent first)."""
    store = Store.open()
    for s in store.list_sessions(limit=limit):
        started = s.started_at.strftime("%Y-%m-%d %H:%M")
        typer.echo(
            f"{s.id}  [{s.mode}]  {s.surface}  {s.project}  turns={s.turn_count}  {started}"
        )


@app.command()
def insights(since: str = "") -> None:
    """Token-free rollups of your captured work (projects, outcomes, cost).

    --since accepts 7d / 2w / 12h / an ISO date.
    """
    from manthana.agent.insights import structural_insights

    store = Store.open()
    s = structural_insights(store, since=since or None)
    cap = " (recent 300 sessions)" if s.cost_capped else ""
    typer.echo(
        f"sessions={s.session_count} compactions={s.compaction_count} "
        f"est. API-equivalent cost ~${s.est_cost_usd}{cap}"
    )
    projects = ", ".join(f"{p}={n}" for p, n in list(s.by_project.items())[:10]) or "—"
    typer.echo(f"by project: {projects}")
    if s.by_outcome:
        typer.echo("by outcome: " + ", ".join(f"{o}={n}" for o, n in s.by_outcome.items()))
    if s.top_friction:
        typer.echo("recent friction: " + " · ".join(s.top_friction[:3]))
    looped = {
        e.details.get("session_id")
        for e in store.list_audit(action_id="loop_warning", limit=500)
        if e.outcome is ActionOutcome.fired and e.details.get("session_id")
    }
    if looped:
        typer.echo(f"⚠ loop warnings: {len(looped)} session(s) flagged for repeated failures")


@app.command()
def ask(question: str, source: str = "") -> None:
    """Ask a grounded, cited question about your own compactions (uses your model).

    --source full restricts to full compactions (default includes the cheap
    Claude-summary-derived ones).
    """
    from manthana.agent.insights import ask as run_ask

    result = run_ask(Store.open(), question, source=source or None)
    typer.echo(result.narrative)
    if result.coverage and result.coverage.truncated:
        typer.echo(f"({result.coverage.note()})")
    if result.citations:
        typer.echo("sources: " + ", ".join(result.citations))
    elif result.grounded is False and result.narrative and "compact" not in result.narrative:
        typer.echo("(ungrounded — no compaction matched a citation)")


@app.command()
def related(session_id: str) -> None:
    """Surface your most relevant PRIOR compactions for a session (local embeddings)."""
    from manthana.agent.actions.prior_work import find_prior_work

    hits = find_prior_work(Store.open(), session_id)
    if not hits:
        typer.echo("no related prior work found (or no compaction for that session yet)")
        return
    typer.echo(f"related prior work ({len(hits)}):")
    for score, c in hits:
        typer.echo(f"  {score:.2f}  [{c.project}] {c.task_intent[:90]}")  # type: ignore[attr-defined]


@app.command()
def mode(session_id: str, value: str) -> None:
    """Set a session's mode: work | personal. Personal-mode sessions never sync."""
    try:
        new_mode = Mode(value)
    except ValueError as exc:
        raise typer.BadParameter("mode must be 'work' or 'personal'") from exc
    store = Store.open()
    ok = store.set_session_mode(session_id, new_mode)
    typer.echo(f"{session_id} -> {new_mode}" if ok else f"no such session: {session_id}")


@app.command()
def compact(session_id: str = typer.Argument(default="")) -> None:
    """Compact a session (or all pending Work sessions if no id is given).

    Uses the engineer's own model access (claude -p / codex exec).
    """
    store = Store.open()
    dispatcher = default_dispatcher(store)
    if session_id:
        result = compact_session(store, session_id)
        if result is not None:
            dispatcher.dispatch(
                TriggerEvent("session_closed", actor=result.actor, session_id=result.session_id)
            )
        typer.echo(
            f"{result.id}: {result.outcome} (${result.est_cost_usd}, {result.tier_used})"
            if result
            else f"no such session: {session_id}"
        )
        return
    results = compact_pending(store)
    for c in results:
        dispatcher.dispatch(
            TriggerEvent("session_closed", actor=c.actor, session_id=c.session_id)
        )
    typer.echo(f"compacted {len(results)} pending session(s)")


@app.command()
def release(compaction_id: str = typer.Argument(default=...)) -> None:
    """Mark a compaction released — eligible to sync to the org server."""
    from datetime import UTC, datetime

    store = Store.open()
    ok = store.mark_released(compaction_id, released=True, released_at=datetime.now(UTC))
    typer.echo(f"released {compaction_id}" if ok else f"no such compaction: {compaction_id}")


@app.command()
def retag() -> None:
    """Run the auto-tag action over all sessions (writes tags to the store)."""
    store = Store.open()
    count = tag_all(store)
    typer.echo(f"dispatched auto-tag over sessions; {count} audit entries logged")


@app.command()
def dashboard(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the local dashboard (sessions, cost, action audit)."""
    import uvicorn
    from manthana.agent.dashboard import create_app

    uvicorn.run(create_app(Store.open()), host=host, port=port)


@app.command()
def sync(raw: bool = False, check: bool = False) -> None:
    """Push released, non-personal compactions to the org server.

    Reads server URL + team token from MANTHANA_SERVER_URL / MANTHANA_TEAM_TOKEN
    (or the [server] section of manthana.toml). --raw also releases transcripts.
    --check only verifies the server is reachable and the token is accepted (no push).
    """
    from manthana.agent.sync_client import SyncClient

    base, token = _resolve_server()
    if not base or not token:
        typer.echo("not configured — run `manthana login --server <url> --token <jwt>` first")
        raise typer.Exit(code=1)

    if check:
        reachable, token_ok = _verify_connection(base, token)
        if not reachable:
            typer.echo(f"server unreachable: {base}")
            raise typer.Exit(code=1)
        if not token_ok:
            typer.echo(f"token rejected by {base}")
            raise typer.Exit(code=1)
        typer.echo(f"ok — {base} reachable and token accepted")
        return

    client = SyncClient(base, token)
    try:
        result = client.sync(Store.open(), include_raw=raw)
    finally:
        client.close()
    typer.echo(
        f"synced {result.pushed} compaction(s); {result.skipped} already synced; "
        f"raw uploaded {result.raw_uploaded}"
    )


@app.command(name="mine-skills")
def mine_skills(min_sessions: int = 3, threshold: float = 0.75, write: bool = False) -> None:
    """Mine recurring patterns in your own compactions into proposed SKILL.md files.

    Drafts are deterministic by default (no token spend / works offline). Pass
    --write to draft them under ~/.claude/skills/personal/. Lower --threshold
    (e.g. 0.6) to cluster more loosely when using the offline embedder.
    """
    from manthana.agent.skillminer import mine_personal, write_proposal

    proposals = mine_personal(Store.open(), min_sessions=min_sessions, threshold=threshold)
    for p in proposals:
        prov = p.provenance
        typer.echo(
            f"{p.draft.name}  (sessions={prov.session_count}, cohesion={prov.confidence})"
        )
    if write and proposals:
        dest = Path.home() / ".claude" / "skills" / "personal"
        for p in proposals:
            write_proposal(p, dest)
        typer.echo(f"wrote {len(proposals)} skill(s) to {dest}")
    else:
        typer.echo(f"{len(proposals)} proposal(s); pass --write to draft them")


@app.command()
def optimize(action: str = typer.Argument("status"), port: int = 8787) -> None:
    """Run Claude Code more efficiently via headroom (context compression).

    actions: status | setup | proxy | mcp | stats | tune. Needs the optional
    extra: pip install "headroom-ai[proxy,mcp]".
    """
    import json as _json

    from manthana.agent import optimize as opt

    if not 1 <= port <= 65535:
        raise typer.BadParameter(f"port must be 1-65535, got {port}")
    if not opt.available():
        typer.echo(opt.INSTALL_HINT)
        raise typer.Exit(code=0 if action == "status" else 1)

    if action == "status":
        typer.echo("headroom installed ✓ — `manthana optimize setup` wires Claude Code")
    elif action == "setup":
        result = opt.setup()
        typer.echo(result.get("output") or ("done" if result.get("ok") else "failed"))
    elif action == "mcp":
        result = opt.mcp_install()
        typer.echo(result.get("output") or ("done" if result.get("ok") else "failed"))
    elif action == "proxy":
        typer.echo("start the proxy in a dedicated terminal:")
        typer.echo("  " + " ".join(opt.proxy_cmd(port)))
        env = " ".join(f"{k}={v}" for k, v in opt.claude_env(port).items())
        typer.echo(f"then run Claude Code through it:\n  {env} claude")
    elif action == "stats":
        s = opt.stats()
        typer.echo(
            _json.dumps(s["data"], indent=2) if s.get("data") else (s.get("error") or str(s))
        )
    elif action == "tune":
        result = opt.tune()
        typer.echo(result.get("output") or ("tuned CLAUDE.md" if result.get("ok") else "failed"))
    else:
        raise typer.BadParameter("action: status | setup | proxy | mcp | stats | tune")


_SERVICE_LABEL = "com.manthana.watch"


def _watch_plist(manthana_bin: str, actor: str | None) -> dict[str, object]:
    """launchd plist for the capture daemon (factored out for testability)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if actor:
        env["MANTHANA_ACTOR"] = actor
    log = str(Path.home() / "Library" / "Logs" / "manthana-watch.log")
    return {
        "Label": _SERVICE_LABEL,
        "ProgramArguments": [manthana_bin, "watch", "--interval", "5"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": env,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


_SYSTEMD_UNIT = "manthana-watch.service"
_WIN_TASK = "ManthanaWatch"


def _watch_bin_and_actor() -> tuple[str, str | None]:
    """The `manthana` executable path + the configured actor (for the daemon env/identity)."""
    import shutil

    from manthana.agent.config import load_config

    manthana_bin = shutil.which("manthana")
    if not manthana_bin:
        typer.echo("could not find the `manthana` executable on PATH")
        raise typer.Exit(code=1)
    return manthana_bin, (load_config().actor or os.environ.get("MANTHANA_ACTOR"))


def _service_darwin(action: str) -> None:
    import plistlib
    import subprocess

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{_SERVICE_LABEL}.plist"

    def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 1, "", "launchctl not found")

    if action == "status":
        if not plist_path.exists():
            typer.echo("not installed")
            return
        state = "running" if _SERVICE_LABEL in _launchctl("list").stdout else "loaded (not running)"
        typer.echo(f"installed at {plist_path} — {state}")
    elif action == "install":
        manthana_bin, actor = _watch_bin_and_actor()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        (Path.home() / "Library" / "Logs").mkdir(parents=True, exist_ok=True)
        with plist_path.open("wb") as fh:
            plistlib.dump(_watch_plist(manthana_bin, actor), fh)
        _launchctl("unload", str(plist_path))
        loaded = _launchctl("load", "-w", str(plist_path))
        if loaded.returncode != 0:
            typer.echo(f"wrote {plist_path} but `launchctl load` failed: {loaded.stderr.strip()}")
            raise typer.Exit(code=1)
        typer.echo(f"installed + loaded {_SERVICE_LABEL}; logs: ~/Library/Logs/manthana-watch.log")
    elif action == "uninstall":
        if not plist_path.exists():
            typer.echo("not installed")
            return
        _launchctl("unload", str(plist_path))
        plist_path.unlink(missing_ok=True)
        typer.echo(f"uninstalled {_SERVICE_LABEL}")


def _systemd_unit_text(manthana_bin: str, actor: str | None) -> str:
    env = f"Environment=MANTHANA_ACTOR={actor}\n" if actor else ""
    return (
        "[Unit]\nDescription=Manthana capture daemon\n\n"
        f"[Service]\nExecStart={manthana_bin} watch --interval 5\n{env}Restart=always\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _service_linux(action: str) -> None:
    import subprocess

    unit = Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_UNIT

    def _sc(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                                  check=False)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 1, "", "systemctl not found")

    if action == "status":
        if not unit.exists():
            typer.echo("not installed")
            return
        typer.echo(f"installed at {unit} — {_sc('is-active', _SYSTEMD_UNIT).stdout.strip()}")
    elif action == "install":
        manthana_bin, actor = _watch_bin_and_actor()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_systemd_unit_text(manthana_bin, actor))
        _sc("daemon-reload")
        r = _sc("enable", "--now", _SYSTEMD_UNIT)
        if r.returncode != 0:
            typer.echo(f"wrote {unit} but `systemctl --user enable --now` failed: {r.stderr.strip()}")  # noqa: E501
            typer.echo("  (headless box? `loginctl enable-linger $USER` for a user session bus)")
            raise typer.Exit(code=1)
        typer.echo(f"installed + started {_SYSTEMD_UNIT} (logs: journalctl --user -u {_SYSTEMD_UNIT})")  # noqa: E501
    elif action == "uninstall":
        if not unit.exists():
            typer.echo("not installed")
            return
        _sc("disable", "--now", _SYSTEMD_UNIT)
        unit.unlink(missing_ok=True)
        _sc("daemon-reload")
        typer.echo(f"uninstalled {_SYSTEMD_UNIT}")


def _service_windows(action: str) -> None:
    import subprocess

    def _st(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(["schtasks", *args], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 1, "", "schtasks not found")

    if action == "status":
        installed = _st("/query", "/tn", _WIN_TASK).returncode == 0
        typer.echo("installed" if installed else "not installed")
    elif action == "install":
        manthana_bin, _actor = _watch_bin_and_actor()  # actor read from config by the daemon
        r = _st("/create", "/tn", _WIN_TASK, "/sc", "onlogon", "/tr",
                f'"{manthana_bin}" watch --interval 5', "/f")
        if r.returncode != 0:
            typer.echo(f"schtasks create failed: {r.stderr.strip()}")
            raise typer.Exit(code=1)
        typer.echo(f"installed scheduled task {_WIN_TASK} (runs at logon)")
    elif action == "uninstall":
        r = _st("/delete", "/tn", _WIN_TASK, "/f")
        typer.echo(f"uninstalled {_WIN_TASK}" if r.returncode == 0 else "not installed")


@app.command()
def service(action: str = typer.Argument("status")) -> None:
    """Run the capture daemon at login: install | uninstall | status.

    macOS = launchd · Linux = `systemd --user` · Windows = Scheduled Task."""
    import platform

    if action not in {"install", "uninstall", "status"}:
        raise typer.BadParameter("action must be install | uninstall | status")
    system = platform.system()
    if system == "Darwin":
        _service_darwin(action)
    elif system == "Linux":
        _service_linux(action)
    elif system == "Windows":
        _service_windows(action)
    else:
        typer.echo(f"unsupported OS ({system}) — run `manthana watch` yourself")
        raise typer.Exit(code=1)


def _apply_identity_from_config() -> None:
    """Honor the configured contributor identity for every command (resolve_actor
    checks MANTHANA_ACTOR first), so capture/compact/sync attribute work correctly."""
    if not os.environ.get("MANTHANA_ACTOR"):
        from manthana.agent.config import load_config

        actor = load_config().actor
        if actor:
            os.environ["MANTHANA_ACTOR"] = actor


@app.command()
def mcp() -> None:
    """Serve Manthana's read-only query tools to Claude Code over MCP (your local data)."""
    from manthana.agent import mcp_server

    if not mcp_server.available():
        typer.echo(mcp_server.INSTALL_HINT)
        raise typer.Exit(code=1)
    typer.echo("Manthana MCP server (stdio) — tools: " + ", ".join(mcp_server.TOOLS))
    mcp_server.run()


def main() -> None:
    """Console-script entry point."""
    _apply_identity_from_config()
    app()


if __name__ == "__main__":
    main()
