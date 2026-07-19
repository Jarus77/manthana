"""Regressions for the pressure-test fixes (k-anon cell leak, auth crash, embedder
degrade, drill robustness, raw caps/validation, topics coverage).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.agent.insights import ask
from manthana.agent.llm import MockProvider as AgentMock
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_team_token
from manthana.server.founder import run_query, team_topics
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
JWT = "x" * 40


def _cfg(**kw):
    return ServerConfig(jwt_secret=JWT, admin_token="adm", **kw)


def _comp(cid, actor, *, project="demo", outcome=Outcome.success, released=True):
    return EngineeringCompaction(
        id=cid, session_id=cid, actor=actor, surface=Surface.claude_code, project=project,
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent=f"intent {cid}",
        approach="a", outcome=outcome, released=released,
    )


# ── FIX #1: (project, outcome) cell leak ─────────────────────────────────────
def test_kanon_lone_cell_never_leaks_into_founder_narrative() -> None:
    config = _cfg(k_anon_floor=4)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(4):  # P1/success: 4 contributors
        store.ingest_compaction(
            _comp(f"p1s{i}", f"e{i}@x", project="P1"), org_id="o1", team_id="t1"
        )
    store.ingest_compaction(  # P1/abandoned: ONE person (the lone k=1 cell)
        _comp("p1a", "e0@x", project="P1", outcome=Outcome.abandoned), org_id="o1", team_id="t1"
    )
    for i in range(4):  # P2/abandoned: 4 contributors → "abandoned" clears globally
        store.ingest_compaction(
            _comp(f"p2a{i}", f"e{i}@x", project="P2", outcome=Outcome.abandoned),
            org_id="o1", team_id="t1",
        )
    # Whole-org query (no project/outcome filter) → all rows visible to the gate.
    # The model tries to cite BOTH a safe success row and the lone abandoned row.
    provider = ScriptedProvider(["{}", "success [p1s0]; lone abandoned by one person [p1a]"])
    result = run_query(
        store, config, org_id="o1", query="what happened across the org?", provider=provider
    )
    assert "p1a" not in result.citations  # the k=1 (P1, abandoned) cell is suppressed
    assert "p1s0" in result.citations  # the >=4-contributor cell is still answerable


# ── FIX #2: non-ASCII token must 401, not 500 ────────────────────────────────
def _client(**kw):
    config = _cfg(**kw)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    return TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([]))), store


def test_non_ascii_login_token_returns_401_not_500() -> None:
    client, _ = _client()
    # Form bodies are UTF-8, so a non-ASCII token reaches compare_digest. Before the fix
    # that raised TypeError → 500; now it must be a clean failed-auth (401). (The header
    # token path can't carry non-ASCII over HTTP, so the login form is the real vector.)
    r1 = client.post("/ui/login", data={"token": "münchen✓"})
    r2 = client.post("/ui/login", data={"token": "tökën"})
    assert r1.status_code == 401 and r2.status_code == 401


# ── FIX #3: embedder failure degrades, never crashes ─────────────────────────
class _BoomEmbedder:
    dim = 8

    def embed(self, texts):
        raise RuntimeError("embedding model unavailable")


def test_embedder_failure_degrades_agent_and_server() -> None:
    store = Store.open_memory()
    for i in range(3):
        store.upsert_compaction(_comp(f"comp-{i}", "e@x"))
    # agent ask: degrades to unranked, still answers (no crash)
    r = ask(store, "anything", provider=AgentMock("answer [comp-0]"), embedder=_BoomEmbedder())
    assert r.citations == ["comp-0"] and r.coverage is not None

    # server run_query: degrades, still answers (no 500)
    config = _cfg(k_anon_floor=1)
    sstore = ServerStore.open("sqlite://")
    sstore.create_org("o1", "Acme")
    sstore.ingest_compaction(_comp("c0", "e@x"), org_id="o1", team_id="t1")
    res = run_query(sstore, config, org_id="o1", query="x",
                    provider=ScriptedProvider(["{}", "ans [c0]"]), embedder=_BoomEmbedder())
    assert res.insufficient_data is False and res.citations == ["c0"]


# ── FIX #4/#5: drill tolerates malformed raw; upload caps + validates ────────
def test_founder_drill_tolerates_malformed_blob() -> None:
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, ScriptedProvider([])))
    store.create_org("o1", "Acme")
    store.ingest_compaction(_comp("c0", "e@x"), org_id="o1", team_id="t1")
    # a corrupt/legacy blob: malformed first line + one valid line
    key = "o1/t1/c0.jsonl"
    obj.put(key, b'not valid json\n{"seq": 1, "content": "ok"}\n')
    store.record_raw("c0", "o1", key)
    r = client.post(
        "/v1/founder/drill", json={"org_id": "o1", "compaction_id": "c0"},
        headers={"X-Admin-Token": "adm"},
    )
    assert r.status_code == 200  # malformed line skipped, not a 500
    turns = r.json()["turns"]
    assert len(turns) == 1 and turns[0]["seq"] == 1


def test_raw_upload_caps_and_validates() -> None:
    config = _cfg(max_raw_bytes=40)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "T")
    store.ingest_compaction(_comp("c0", "e@x"), org_id="o1", team_id="t1")
    client = TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])))
    token = issue_team_token(JWT, org_id="o1", team_id="t1", actor="e@x")
    auth = {"Authorization": f"Bearer {token}"}
    # oversized → 413
    big = client.post("/v1/compactions/c0/raw", json={"content": "x" * 100}, headers=auth)
    assert big.status_code == 413
    # non-JSONL → 422
    bad = client.post("/v1/compactions/c0/raw", json={"content": "not json"}, headers=auth)
    assert bad.status_code == 422
    # valid JSONL within cap → 200
    ok = client.post("/v1/compactions/c0/raw", json={"content": '{"a":1}'}, headers=auth)
    assert ok.status_code == 200


# ── FIX #6: topics carry a coverage signal ───────────────────────────────────
def test_team_topics_returns_coverage() -> None:
    config = _cfg(k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(3):
        store.ingest_compaction(_comp(f"c{i}", f"e{i}@x"), org_id="o1", team_id="t1")
    tops, cov = team_topics(store, config, "o1")
    assert cov.matched == 3 and cov.truncated is False
    assert isinstance(tops, list)
