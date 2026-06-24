"""Stage-3: raw drill-down (two trust tiers), person-relational, MCP tools.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.agent import mcp_server
from manthana.agent.insights import drill_raw
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Outcome, Role, Surface, Turn
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}
MGR = {"X-Manager-Token": "mgr"}


def _comp(
    cid: str, sid: str, *, actor: str = "e@x", released: bool = False
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=sid, actor=actor, surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0,
        task_intent=f"intent {cid}", approach="a", outcome=Outcome.success, released=released,
    )


def _turn(sid: str, seq: int, text: str) -> Turn:
    return Turn(
        id=f"{sid}-{seq}", session_id=sid, actor="e@x", seq=seq,
        role=Role.assistant, content=text,
    )


# ── engineer-self drill: own turns, unredacted ───────────────────────────────
def test_engineer_drill_returns_own_turns() -> None:
    store = Store.open_memory()
    store.upsert_compaction(_comp("comp-s1", "s1"))
    store.add_turns([_turn("s1", 0, "first"), _turn("s1", 1, "second"), _turn("s1", 2, "third")])
    turns = drill_raw(store, "comp-s1")
    assert [t.content for t in turns] == ["first", "second", "third"]
    assert [t.content for t in drill_raw(store, "comp-s1", start=1, end=2)] == ["second"]
    assert drill_raw(store, "ghost") == []


# ── org drill: manager-only, audited, redacted raw; founder has no path ──────
def _server() -> tuple[TestClient, ServerStore, InMemoryObjectStore]:
    config = ServerConfig(
        jwt_secret="x" * 40, admin_token="adm", manager_token="mgr", k_anon_floor=1
    )
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    store.create_org("o1", "Acme")
    return TestClient(create_app(config, store, obj, ScriptedProvider([]))), store, obj


def test_manager_drill_requires_token_returns_redacted_raw_and_audits() -> None:
    client, store, obj = _server()
    store.ingest_compaction(_comp("c0", "c0", released=True), org_id="o1", team_id="t1")
    # seed a released raw transcript (already redacted at sync) into the object store
    key = "o1/t1/c0.jsonl"
    obj.put(key, b'{"seq":0,"role":"assistant","content":"used [REDACTED:aws_key]"}\n')
    store.record_raw("c0", "o1", key)

    body = {"org_id": "o1", "compaction_id": "c0"}
    assert client.post("/v1/manager/drill", json=body).status_code == 401  # no manager token
    r = client.post("/v1/manager/drill", json=body, headers=MGR)
    assert r.status_code == 200
    turns = r.json()["turns"]
    assert turns and "[REDACTED:aws_key]" in turns[0]["content"]
    # audited as an individual lookup
    audit = client.get("/v1/admin/audit", params={"org_id": "o1"}, headers=ADMIN).json()["entries"]
    assert any(e["individual"] and "drill" in e["query"] for e in audit)


def test_founder_has_no_drill_path() -> None:
    client, *_ = _server()
    r = client.post("/v1/founder/drill", json={"org_id": "o1", "compaction_id": "c0"})
    assert r.status_code == 404  # no founder drill path exists


# ── person-relational: "A vs B" doesn't collapse to one actor ────────────────
def test_manager_comparison_does_not_collapse_actor() -> None:
    from manthana.server.founder import run_query

    config = ServerConfig(
        jwt_secret="x" * 40, admin_token="adm", manager_token="mgr", k_anon_floor=1
    )
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.ingest_compaction(
        _comp("a0", "a0", actor="suraj@x", released=True), org_id="o1", team_id="t1"
    )
    store.ingest_compaction(
        _comp("b0", "b0", actor="tarun@x", released=True), org_id="o1", team_id="t1"
    )
    # parse returns an unresolved multi-name actor; narrative cites both people
    provider = ScriptedProvider(
        ['{"actor": "Suraj and Tarun"}', "A did a0 [a0]; B did b0 [b0]"]
    )
    result = run_query(
        store, config, org_id="o1", query="compare Suraj and Tarun", provider=provider,
        allow_individual=True,
    )
    assert result.filter.actor is None  # not collapsed to a bogus single actor
    assert set(result.citations) == {"a0", "b0"}  # both people compared


# ── MCP tools (delegate to the tested query layer) ───────────────────────────
def test_mcp_tools_and_install_hint() -> None:
    store = Store.open_memory()
    store.upsert_compaction(_comp("comp-s1", "s1"))
    store.add_turns([_turn("s1", 0, "did the postgres migration")])
    assert mcp_server.tool_insights(store)["compaction_count"] == 1
    assert mcp_server.tool_drill_raw(store, "comp-s1")[0]["text"] == "did the postgres migration"
    assert isinstance(mcp_server.available(), bool)
    assert "extra mcp" in mcp_server.INSTALL_HINT and mcp_server.TOOLS
