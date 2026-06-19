"""Dashboard control-plane tests (FastAPI TestClient).

Hermetic: compact uses a MockProvider (no claude / no tokens), skills write to a
tmp dir (not real $HOME), and capture is monkeypatched (no real ~/.claude read).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
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
    store = Store.open_memory()
    _session(store)
    client = TestClient(create_app(store, provider=MockProvider(_GOOD), skills_dir=tmp_path))
    return client, store


# ── pages render ──────────────────────────────────────────────────────────
def test_index_lists_sessions_with_capture_and_compact(tmp_path: Path) -> None:
    client, _store = _build(tmp_path)
    body = client.get("/").text
    assert "s1" in body and "demo" in body
    assert "Capture transcripts" in body  # action button present
    assert "compact" in body  # per-session compact button


def test_compactions_and_skills_and_cost_and_actions_pages(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _compaction(store, "comp-s1", "s1")
    assert "comp-s1" in client.get("/compactions").text
    assert "Mine skills" in client.get("/skills").text
    assert "Total:" in client.get("/cost").text
    assert client.get("/actions").status_code == 200


# ── actions mutate state ───────────────────────────────────────────────────
def test_compact_button_creates_compaction(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    resp = client.post("/session/s1/compact", follow_redirects=False)
    assert resp.status_code == 303
    assert store.get_compaction("comp-s1") is not None  # compacted via MockProvider


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
