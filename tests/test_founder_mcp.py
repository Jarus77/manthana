"""Founder MCP gateway tool bodies — navigable, org-scoped, read-only.

Pure-function tests over ServerStore + InMemoryObjectStore (no MCP SDK, no network).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, FrictionPoint, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_founder_token
from manthana.server.founder_mcp import (
    available,
    tool_grep,
    tool_list_engineers,
    tool_list_projects,
    tool_list_sessions,
    tool_read_raw,
    tool_read_session,
    tool_search,
    tool_thread,
)
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _comp(cid: str, actor: str, project: str, intent: str, sid: str | None = None):
    return EngineeringCompaction(
        id=cid,
        session_id=sid or cid,
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=intent,
        approach="a",
        outcome=Outcome.success,
        friction_points=[FrictionPoint(category="tool_error", description="flaky webhook")],
        est_cost_usd=0.4,
        tier_used="sonnet",
        released=True,
    )


def _store():
    store = ServerStore.open("sqlite://")
    store.create_org("acme", "Acme")
    store.ingest_compaction(
        _comp("c1", "a@acme.dev", "checkout", "fix payment webhook retries"),
        org_id="acme", team_id="t1",
    )
    store.ingest_compaction(
        _comp("c2", "b@acme.dev", "pipeline", "backfill events partitioning"),
        org_id="acme", team_id="t1",
    )
    # a second slice of c1's session → a thread
    store.ingest_compaction(
        _comp("c1b", "a@acme.dev", "checkout", "retry webhook again", sid="c1"),
        org_id="acme", team_id="t1",
    )
    # another org's data — must never appear
    store.create_org("other", "Other")
    store.ingest_compaction(
        _comp("x1", "z@other.dev", "secret", "other org work"),
        org_id="other", team_id="t9",
    )
    return store


def test_list_sessions_scoped_and_filtered():
    store = _store()
    all_acme = tool_list_sessions(store, "acme")
    ids = {r["id"] for r in all_acme}
    assert ids == {"c1", "c2", "c1b"}
    assert "x1" not in ids  # cross-org invisible
    only_pipeline = tool_list_sessions(store, "acme", project="pipeline")
    assert {r["id"] for r in only_pipeline} == {"c2"}
    only_a = tool_list_sessions(store, "acme", engineer="a@acme.dev")
    assert {r["id"] for r in only_a} == {"c1", "c1b"}


def test_read_session_returns_full_digest_org_scoped():
    store = _store()
    doc = tool_read_session(store, "acme", "c1")
    assert doc is not None
    assert doc["task_intent"] == "fix payment webhook retries"
    assert doc["friction_points"][0]["description"] == "flaky webhook"
    # cross-org id is not readable through an acme scope
    assert tool_read_session(store, "acme", "x1") is None


def test_search_returns_only_org_results():
    store = _store()
    out = tool_search(store, "acme", "payment webhook problems", k=5)
    ids = {r["id"] for r in out["results"]}
    assert ids and ids <= {"c1", "c2", "c1b"}
    assert "x1" not in ids
    assert "coverage" in out


def test_read_raw_paginates_and_is_scoped():
    store = _store()
    obj = InMemoryObjectStore()
    raw = "\n".join(f'{{"seq": {i}, "role": "user", "content": "line {i}"}}' for i in range(5))
    obj.put("acme/t1/c1.jsonl", raw.encode())
    store.record_raw("c1", "acme", "acme/t1/c1.jsonl")
    turns = tool_read_raw(store, obj, "acme", "c1")
    assert len(turns) == 5 and turns[0]["seq"] == 0
    page = tool_read_raw(store, obj, "acme", "c1", start=1, end=3)
    assert [t["seq"] for t in page] == [1, 2]
    # no raw for c2 → empty, not an error
    assert tool_read_raw(store, obj, "acme", "c2") == []


def test_grep_scans_raw_and_reports_truncation():
    store = _store()
    obj = InMemoryObjectStore()
    obj.put(
        "acme/t1/c1.jsonl",
        b'{"seq":0,"role":"user","content":"the STRIPE_KEY handling"}\n'
        b'{"seq":1,"role":"assistant","content":"unrelated"}',
    )
    store.record_raw("c1", "acme", "acme/t1/c1.jsonl")
    out = tool_grep(store, obj, "acme", "stripe_key")
    assert len(out["hits"]) == 1
    assert out["hits"][0]["compaction_id"] == "c1"
    assert out["truncated"] is False
    # hit cap is honored
    capped = tool_grep(store, obj, "acme", "seq", max_hits=1)
    assert len(capped["hits"]) == 1 and capped["truncated"] is True


def test_grep_bad_pattern_returns_error_not_crash():
    store = _store()
    out = tool_grep(store, InMemoryObjectStore(), "acme", "([unclosed")
    assert "error" in out and out["hits"] == []


def test_thread_stitches_a_sessions_slices():
    store = _store()
    arc = tool_thread(store, "acme", "c1")
    assert {r["id"] for r in arc} == {"c1", "c1b"}


def test_list_projects_and_engineers_scoped():
    store = _store()
    assert set(tool_list_projects(store, "acme")) == {"checkout", "pipeline"}
    engineers = {e["id"] for e in tool_list_engineers(store, "acme")}
    assert engineers == {"a@acme.dev", "b@acme.dev"}
    assert "z@other.dev" not in engineers


# ── transport: auth boundary + mount gating (needs the mcp extra) ────────────
_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "t", "version": "1"}},
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _app(enable: bool):
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", enable_founder_mcp=enable)
    store = _store()
    return create_app(cfg, store, InMemoryObjectStore(), ScriptedProvider([]))


@pytest.mark.skipif(not available(), reason="mcp extra not installed")
def test_mcp_endpoint_requires_founder_token():
    with TestClient(_app(True)) as c:
        assert c.post("/mcp/", json=_INIT, headers=_MCP_HEADERS).status_code == 401
        bad = issue_founder_token("WRONG" * 8, org_id="acme")
        assert c.post(
            "/mcp/", json=_INIT, headers={**_MCP_HEADERS, "Authorization": f"Bearer {bad}"}
        ).status_code == 401
        good = issue_founder_token("x" * 40, org_id="acme")
        r = c.post("/mcp/", json=_INIT, headers={**_MCP_HEADERS, "Authorization": f"Bearer {good}"})
        assert r.status_code != 401  # valid founder token passes the tenant boundary


@pytest.mark.skipif(not available(), reason="mcp extra not installed")
def test_mcp_endpoint_absent_when_flag_off():
    with TestClient(_app(False)) as c:
        assert c.post("/mcp/", json=_INIT, headers=_MCP_HEADERS).status_code == 404
