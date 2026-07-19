"""Dashboard control-plane tests (FastAPI TestClient).

Hermetic: compact uses a MockProvider (no claude / no tokens), skills write to a
tmp dir (not real $HOME), capture is monkeypatched (no real ~/.claude read), and
$MANTHANA_DATA_HOME is redirected per-test so agent config never resolves to the
operator's real ~/.manthana/manthana.toml.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from manthana.agent.dashboard import app as dash_app
from manthana.agent.dashboard import create_app
from manthana.agent.llm import MockProvider
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Outcome, Role, Session, Surface, Turn

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GOOD = json.dumps({"task_intent": "fix tests", "approach": "patch", "outcome": "success"})


@pytest.fixture(autouse=True)
def _isolated_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never resolve agent config from the operator's real ~/.manthana.

    Clearing MANTHANA_SERVER_URL/TEAM_TOKEN is not enough: config falls back to
    $MANTHANA_DATA_HOME/manthana.toml, so on a machine with a configured agent a
    test asserting the UNconfigured path would read live server credentials and
    see a redirect instead of the warning.
    """
    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path / "datahome"))


def _session(store: Store, sid: str = "s1") -> None:
    store.upsert_session(
        Session(
            id=sid,
            actor="eng@example.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            turn_count=1,
        )
    )
    store.add_turns(
        [Turn(id=f"{sid}-t0", session_id=sid, actor="e", seq=0, role=Role.user, content="hi")]
    )


def _compaction(
    store: Store, cid: str, sid: str, *, released: bool = False, intent: str = "x"
) -> None:
    store.upsert_compaction(
        EngineeringCompaction(
            id=cid,
            session_id=sid,
            actor="eng@example.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            ended_at=_T0,
            duration_seconds=1.0,
            task_intent=intent,
            approach="a",
            outcome=Outcome.success,
            est_cost_usd=0.5,
            tier_used="opus",
            released=released,
        )
    )


def _build(tmp_path: Path) -> tuple[TestClient, Store]:
    # File-based store (not open_memory) so it matches production: the dashboard
    # runs compaction in a background thread, and a file engine gives each thread
    # its own pooled connection (check_same_thread=False + WAL). open_memory uses a
    # single StaticPool connection shared across threads → "sqlite3.InterfaceError:
    # bad parameter or other API misuse" under the concurrent access.
    store = Store.open(tmp_path / "manthana.db")
    _session(store)
    client = TestClient(create_app(store, provider=MockProvider(_GOOD), skills_dir=tmp_path))
    return client, store


# ── pages render ──────────────────────────────────────────────────────────
def test_sessions_page_lists_sessions_with_capture_and_compact(tmp_path: Path) -> None:
    # The session list + its controls live at /sessions; / is the projects wiki.
    client, _store = _build(tmp_path)
    body = client.get("/sessions").text
    assert "s1" in body and "demo" in body
    assert "Capture transcripts" in body  # action button present
    assert "compact" in body  # per-session compact button


def test_ask_page_shows_structural_panel_and_answers(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _compaction(store, "comp-s1", "s1", intent="fix the parser")
    # structural panel renders with no question (token-free)
    body = client.get("/ask").text
    assert "Your work" in body and "demo" in body  # project from the seeded session
    assert "ask about your sessions" in body  # the question form
    # a question runs the grounded ask (MockProvider returns the canned compaction JSON)
    answered = client.get("/ask", params={"question": "what did I do?"}).text
    assert "Answer" in answered


def test_compactions_and_skills_and_cost_and_actions_pages(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _compaction(store, "comp-s1", "s1")
    assert "comp-s1" in client.get("/compactions").text
    assert "Mine skills" in client.get("/skills").text
    assert "Total:" in client.get("/cost").text
    assert client.get("/actions").status_code == 200


def _wait_for(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ── actions mutate state ───────────────────────────────────────────────────
def test_compact_button_runs_async_and_creates_compaction(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    resp = client.post("/session/s1/compact", follow_redirects=False)
    assert resp.status_code == 303  # returns immediately; compaction runs off-thread
    assert _wait_for(lambda: store.get_compaction("comp-s1") is not None)


def test_compact_shows_in_progress_then_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _build(tmp_path)
    gate = threading.Event()
    real = dash_app.compact_session

    def slow(*args: object, **kwargs: object) -> object:
        gate.wait(2)  # hold the worker so the in-progress state is observable
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dash_app, "compact_session", slow)
    client.post("/session/s1/compact", follow_redirects=False)
    # While the worker is gated, the Sessions page shows the in-progress state.
    assert _wait_for(lambda: "compacting" in client.get("/sessions").text)
    gate.set()
    assert _wait_for(lambda: store.get_compaction("comp-s1") is not None)
    assert "✓ compacted" in client.get("/sessions").text


def test_failed_compaction_logs_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    client, store = _build(tmp_path)

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("llm down")

    monkeypatch.setattr(dash_app, "compact_session", boom)
    with caplog.at_level(logging.ERROR):
        client.post("/session/s1/compact", follow_redirects=False)
        assert _wait_for(lambda: "compacting" not in client.get("/sessions").text)
    assert any("background compaction failed" in r.getMessage() for r in caplog.records)
    assert store.get_compaction("comp-s1") is None  # nothing written on failure
    assert "compact" in client.get("/sessions").text  # in-progress set cleaned up → button back


def test_double_compact_does_not_double_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _build(tmp_path)
    gate = threading.Event()
    starts = {"n": 0}
    real = dash_app.compact_session

    def counting(*args: object, **kwargs: object) -> object:
        starts["n"] += 1
        gate.wait(2)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dash_app, "compact_session", counting)
    client.post("/session/s1/compact", follow_redirects=False)
    assert _wait_for(lambda: starts["n"] == 1)
    client.post("/session/s1/compact", follow_redirects=False)  # ignored while running
    gate.set()
    assert _wait_for(lambda: store.get_compaction("comp-s1") is not None)
    assert starts["n"] == 1  # the second click did not start a second compaction


def test_release_toggle(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _compaction(store, "comp-s1", "s1", released=False)
    client.post("/compaction/comp-s1/release", follow_redirects=False)
    assert store.get_compaction("comp-s1").released is True  # type: ignore[union-attr]
    client.post("/compaction/comp-s1/release", follow_redirects=False)
    assert store.get_compaction("comp-s1").released is False  # type: ignore[union-attr]


def test_mine_button_writes_and_lists_skill(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    for i in range(3):  # 3 sessions, same intent -> one cluster at threshold 0.6
        _compaction(store, f"comp-s{i}", f"s{i}", intent="fix flaky pytest timeout")
    resp = client.post("/skills/mine?threshold=0.6", follow_redirects=False)
    assert resp.status_code == 303
    assert list(tmp_path.glob("*/SKILL.md"))  # a skill was written
    assert "SKILL.md" in client.get("/skills").text  # and it renders


def test_capture_button_invokes_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = _build(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(dash_app, "ingest_all", lambda _store: called.__setitem__("n", 1))
    resp = client.post("/capture", follow_redirects=False)
    assert resp.status_code == 303
    assert called["n"] == 1


def test_sync_button_warns_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MANTHANA_SERVER_URL", raising=False)
    monkeypatch.delenv("MANTHANA_TEAM_TOKEN", raising=False)
    client, _store = _build(tmp_path)
    resp = client.post("/sync", follow_redirects=False)
    assert resp.status_code == 200
    assert "not configured" in resp.text
