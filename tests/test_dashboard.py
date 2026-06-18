"""Dashboard smoke tests (FastAPI TestClient).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.agent.dashboard import create_app
from manthana.agent.store import Store
from manthana.schemas import Role, Session, Surface, Turn

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _client() -> TestClient:
    store = Store.open_memory()
    store.upsert_session(
        Session(
            id="s1",
            actor="eng@example.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            turn_count=1,
        )
    )
    store.add_turns(
        [
            Turn(
                id="t0",
                session_id="s1",
                actor="e",
                seq=0,
                role=Role.assistant,
                model="claude-opus-4-8",
                tokens_in=1_000_000,
            )
        ]
    )
    return TestClient(create_app(store))


def test_index_lists_sessions() -> None:
    resp = _client().get("/")
    assert resp.status_code == 200
    assert "s1" in resp.text
    assert "demo" in resp.text


def test_mode_toggle_endpoint_switches_mode() -> None:
    client = _client()
    resp = client.post("/session/s1/mode/personal")
    assert resp.status_code == 200
    assert "personal" in resp.text
    # the index now reflects personal mode
    assert "personal" in client.get("/").text


def test_cost_page_shows_total() -> None:
    resp = _client().get("/cost")
    assert resp.status_code == 200
    assert "Total:" in resp.text
    assert "opus" in resp.text  # tier from the seeded turn


def test_actions_page_renders() -> None:
    resp = _client().get("/actions")
    assert resp.status_code == 200
    assert "no actions fired yet" in resp.text
