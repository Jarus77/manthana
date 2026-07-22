"""Update-available notice: version ordering, cache TTL, and the silence gates.

The gates matter more than the happy path here — a notice that leaks into piped
output, a CI log, or a command's exit status is a regression that would be found
by users rather than by us. So most of this file asserts that nothing is printed.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from manthana.agent import updatecheck
from manthana.agent.config import Config, load_config, save_config

_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the cache at a tmp dir and start from a clean gate state."""
    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    monkeypatch.delenv(updatecheck.OPT_OUT_ENV, raising=False)
    for name in updatecheck._CI_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(updatecheck, "_suppressed", False)
    return tmp_path


class _Tty(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def _interactive(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """An engineer at a real terminal: the tty gate passes, output lands in a buffer.

    The tty gate is stubbed rather than achieved by swapping ``sys.stdout``, because
    pytest's capture re-binds ``sys.stdout``/``sys.stderr`` at the start of every
    test phase and would undo a fixture-time patch. ``_streams_are_tty`` itself is
    exercised directly by the tests below.
    """
    monkeypatch.setattr(updatecheck, "_streams_are_tty", lambda: True)
    return io.StringIO()


# --- version ordering -------------------------------------------------------


def test_is_newer_detects_a_newer_release() -> None:
    assert updatecheck.is_newer("0.6.3", "0.7.0") is True


def test_is_newer_is_false_for_equal_and_older() -> None:
    assert updatecheck.is_newer("0.6.3", "0.6.3") is False
    assert updatecheck.is_newer("0.7.0", "0.6.3") is False


def test_is_newer_is_numeric_not_lexical() -> None:
    # The whole reason `packaging` is a dependency: "0.10.0" < "0.9.0" as strings.
    assert updatecheck.is_newer("0.9.0", "0.10.0") is True
    assert updatecheck.is_newer("0.10.0", "0.9.0") is False


def test_is_newer_tolerates_a_v_prefixed_git_tag() -> None:
    assert updatecheck.is_newer("0.6.3", "v0.7.0") is True


def test_is_newer_is_false_when_a_version_is_unparseable() -> None:
    assert updatecheck.is_newer("0.6.3", "not-a-version") is False
    assert updatecheck.is_newer("0.6.3", "") is False


def test_is_newer_is_false_when_the_agent_is_not_installed() -> None:
    # "0+unknown" IS valid PEP 440, so it must be rejected by name — otherwise
    # everyone running from a source checkout is told to upgrade forever.
    assert updatecheck.is_newer(updatecheck.UNKNOWN_VERSION, "0.7.0") is False


# --- cache ------------------------------------------------------------------


def test_cache_roundtrip(tmp_path: Path) -> None:
    assert updatecheck.write_cache("0.7.0", source="server", now=_NOW) is True
    cache = updatecheck.read_cache()
    assert cache is not None
    assert cache.latest_version == "0.7.0"
    assert cache.source == "server"
    assert cache.checked_at == _NOW
    assert (tmp_path / updatecheck.CACHE_FILENAME).exists()


def test_should_check_honours_the_24h_ttl() -> None:
    updatecheck.write_cache("0.7.0", now=_NOW)
    assert updatecheck.should_check(_NOW + timedelta(hours=23)) is False
    assert updatecheck.should_check(_NOW + timedelta(hours=24, minutes=1)) is True


def test_should_check_is_true_with_no_cache() -> None:
    assert updatecheck.should_check(_NOW) is True


@pytest.mark.parametrize(
    "body",
    ["", "{", "null", "[]", '{"latest_version": "0.7.0"}', '{"checked_at": "nonsense"}'],
)
def test_corrupt_cache_reads_as_unknown_and_never_raises(tmp_path: Path, body: str) -> None:
    (tmp_path / updatecheck.CACHE_FILENAME).write_text(body)
    assert updatecheck.read_cache() is None
    assert updatecheck.should_check(_NOW) is True


def test_corrupt_cache_does_not_break_the_notifier(
    tmp_path: Path, _interactive: io.StringIO
) -> None:
    (tmp_path / updatecheck.CACHE_FILENAME).write_text("{ truncated")
    assert updatecheck.maybe_notify(_interactive) is False
    assert _interactive.getvalue() == ""


def test_unwritable_data_home_is_survivable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANTHANA_DATA_HOME", "/proc/definitely/not/writable")
    assert updatecheck.write_cache("0.7.0", now=_NOW) is False


# --- the notice -------------------------------------------------------------


def test_notice_is_none_when_up_to_date() -> None:
    assert updatecheck.notice("0.7.0", "0.7.0") is None


def test_notice_names_the_org_server_and_the_one_upgrade_path() -> None:
    text = updatecheck.notice("0.6.3", "0.7.0", "server")
    assert text is not None
    assert text.startswith("\n")  # detaches from the command's own output
    assert "0.7.0" in text and "0.6.3" in text
    assert "org server" in text
    assert "install.sh | sh" in text
    assert updatecheck.OPT_OUT_ENV in text  # the opt-out documents itself


def test_notice_from_github_uses_the_neutral_wording() -> None:
    text = updatecheck.notice("0.6.3", "0.7.0", "github")
    assert text is not None
    assert "org server" not in text
    assert "0.6.3 → 0.7.0" in text


def test_install_command_honours_MANTHANA_REPO(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANTHANA_REPO", "acme/fork")
    assert "acme/fork" in updatecheck.install_command()


# --- the gates --------------------------------------------------------------


def _stale_cache_with_update(monkeypatch: pytest.MonkeyPatch) -> None:
    updatecheck.write_cache("9999.0.0", source="server", now=_NOW)
    monkeypatch.setattr(updatecheck, "current_version", lambda: "0.6.3")


def test_notifies_on_an_interactive_tty(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO
) -> None:
    _stale_cache_with_update(monkeypatch)
    assert updatecheck.maybe_notify(_interactive) is True
    assert "9999.0.0" in _interactive.getvalue()


@pytest.mark.parametrize(
    ("out_is_tty", "err_is_tty", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_streams_are_tty_requires_both(
    monkeypatch: pytest.MonkeyPatch, out_is_tty: bool, err_is_tty: bool, expected: bool
) -> None:
    # Patched inside the test body: pytest's capture rebinds these between phases.
    monkeypatch.setattr(updatecheck.sys, "stdout", _Tty() if out_is_tty else io.StringIO())
    monkeypatch.setattr(updatecheck.sys, "stderr", _Tty() if err_is_tty else io.StringIO())
    assert updatecheck._streams_are_tty() is expected


def test_streams_are_tty_survives_a_closed_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = io.StringIO()
    closed.close()  # isatty() on a closed file raises ValueError
    monkeypatch.setattr(updatecheck.sys, "stdout", closed)
    assert updatecheck._streams_are_tty() is False


def test_silent_when_stdout_is_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stale_cache_with_update(monkeypatch)
    monkeypatch.setattr(updatecheck, "_streams_are_tty", lambda: False)
    sink = io.StringIO()
    assert updatecheck.maybe_notify(sink) is False
    assert sink.getvalue() == ""


@pytest.mark.parametrize("var", ["CI", "GITHUB_ACTIONS", "BUILDKITE", "TEAMCITY_VERSION"])
def test_silent_in_ci(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO, var: str
) -> None:
    _stale_cache_with_update(monkeypatch)
    monkeypatch.setenv(var, "1")
    assert updatecheck.maybe_notify(_interactive) is False
    assert _interactive.getvalue() == ""


def test_silent_when_opted_out_by_env(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO
) -> None:
    _stale_cache_with_update(monkeypatch)
    monkeypatch.setenv(updatecheck.OPT_OUT_ENV, "1")
    assert updatecheck.maybe_notify(_interactive) is False
    assert _interactive.getvalue() == ""


def test_silent_when_opted_out_by_config(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO, tmp_path: Path
) -> None:
    _stale_cache_with_update(monkeypatch)
    save_config(Config(update_notifier=False), tmp_path / "manthana.toml")
    assert load_config(tmp_path / "manthana.toml").update_notifier is False
    assert updatecheck.maybe_notify(_interactive) is False


def test_update_notifier_defaults_to_on(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml").update_notifier is True
    save_config(Config(), tmp_path / "m.toml")
    assert "[update]" not in (tmp_path / "m.toml").read_text()  # no knob unless silenced


def test_silent_in_the_daemon_even_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO
) -> None:
    _stale_cache_with_update(monkeypatch)
    updatecheck.suppress()
    assert updatecheck.maybe_notify(_interactive) is False
    assert _interactive.getvalue() == ""


def test_no_child_process_is_spawned_when_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(
        updatecheck.subprocess, "Popen", lambda *a, **k: spawned.append(a)  # type: ignore[misc]
    )
    monkeypatch.setenv(updatecheck.OPT_OUT_ENV, "1")
    assert updatecheck.maybe_spawn_check() is False
    assert spawned == []


def test_no_child_process_when_the_cache_is_fresh(
    monkeypatch: pytest.MonkeyPatch, _interactive: io.StringIO
) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(
        updatecheck.subprocess, "Popen", lambda *a, **k: spawned.append(a)  # type: ignore[misc]
    )
    updatecheck.write_cache("0.7.0", now=datetime.now(UTC))
    assert updatecheck.maybe_spawn_check() is False
    assert spawned == []


# --- fetching + refresh -----------------------------------------------------


def test_fetch_latest_prefers_the_org_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _get(url: str, **_kw: object) -> httpx.Response:
        assert url == "https://srv.example/healthz"
        return httpx.Response(
            200,
            json={"status": "ok", "server_version": "0.8.1", "latest_agent_version": "0.8.1"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", _get)
    assert updatecheck.fetch_latest("https://srv.example/") == ("0.8.1", "server")


def test_fetch_latest_falls_back_to_github(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    seen: list[str] = []

    def _get(url: str, **_kw: object) -> httpx.Response:
        seen.append(url)
        request = httpx.Request("GET", url)
        if "healthz" in url:
            return httpx.Response(200, json={"status": "ok"}, request=request)  # old server
        return httpx.Response(200, json={"tag_name": "v0.9.0"}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    assert updatecheck.fetch_latest("https://srv.example") == ("0.9.0", "github")
    assert any("healthz" in u for u in seen) and any("api.github.com" in u for u in seen)


def test_fetch_latest_swallows_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _boom(url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", _boom)
    assert updatecheck.fetch_latest("https://srv.example") == (None, "none")


def test_refresh_writes_the_cache_and_reports_only_new_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updatecheck, "current_version", lambda: "0.6.3")
    monkeypatch.setattr(updatecheck, "fetch_latest", lambda *a, **k: ("0.7.0", "server"))
    assert updatecheck.refresh("https://srv.example", force=True) == "0.7.0"
    cache = updatecheck.read_cache()
    assert cache is not None and cache.latest_version == "0.7.0"
    # Second pass sees the same release: the daemon must log once, not every cycle.
    assert updatecheck.refresh("https://srv.example", force=True) is None


def test_refresh_respects_the_ttl_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        updatecheck, "fetch_latest", lambda *a, **k: (calls.append(a), ("0.7.0", "server"))[1]
    )
    updatecheck.write_cache("0.6.3", now=datetime.now(UTC))
    assert updatecheck.refresh("https://srv.example") is None
    assert calls == []


def test_refresh_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> tuple[str | None, str]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(updatecheck, "fetch_latest", _boom)
    assert updatecheck.refresh("https://srv.example", force=True) is None


def test_cache_file_is_json_and_hand_inspectable(tmp_path: Path) -> None:
    updatecheck.write_cache("0.7.0", source="server", now=_NOW)
    data = json.loads((tmp_path / updatecheck.CACHE_FILENAME).read_text())
    assert data == {
        "checked_at": "2026-07-22T12:00:00Z",
        "latest_version": "0.7.0",
        "source": "server",
    }
    # Never SQLite: a trivial command must not have to open (and lock) the store.
    assert not (tmp_path / "manthana.db").exists()
