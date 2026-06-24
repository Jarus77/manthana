"""Stage-2 assembly: threads + topics (k-anon split: founder de-identified, manager named).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore
from manthana.skills.assembly import group_threads, thread_key, topics
from manthana.skills.embed import HashingEmbedder

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}
MGR = {"X-Manager-Token": "mgr"}


def _c(
    cid: str, actor: str, sid: str, intent: str, *, released: bool = False
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=sid, actor=actor, surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0,
        task_intent=intent, approach=intent, outcome=Outcome.success, released=released,
    )


# ── thread primitives ────────────────────────────────────────────────────────
def test_thread_key_strips_slice_suffix() -> None:
    assert thread_key("abc-123") == "abc-123"
    assert thread_key("abc-123.2") == "abc-123"
    assert thread_key("abc-123.45") == "abc-123"


def test_group_threads_groups_and_orders() -> None:
    a = _c("a", "e@x", "base", "x")
    b = _c("b", "e@x", "base.2", "y")
    b.started_at = _T0 + timedelta(hours=1)
    other = _c("c", "e@x", "zzz", "z")
    threads = group_threads([b, a, other])
    assert set(threads) == {"base", "zzz"}
    assert [c.id for c in threads["base"]] == ["a", "b"]  # chronological


# ── topics + the k-anon gate ────────────────────────────────────────────────
def _postgres_cohort() -> list[EngineeringCompaction]:
    # 4 distinct contributors on the same topic + 2 single-contributor react sessions
    pg = [
        _c(f"pg{i}", f"e{i}@x", f"s{i}", "optimize postgres database query plans")
        for i in range(4)
    ]
    react = [
        _c(f"r{i}", "e0@x", f"sr{i}", "build the react frontend component library")
        for i in range(2)
    ]
    return pg + react


def test_topics_k_anon_gate_and_dedup() -> None:
    emb = HashingEmbedder()
    comps = _postgres_cohort()
    founder = topics(comps, emb, min_contributors=4)  # founder view
    assert len(founder) == 1
    assert founder[0].contributors == {"e0@x", "e1@x", "e2@x", "e3@x"}
    named = topics(comps, emb, min_contributors=1)  # manager / engineer view
    assert len(named) == 2  # the react single-contributor cluster shows too


def test_deidentified_drops_names() -> None:
    t = topics(_postgres_cohort(), HashingEmbedder(), min_contributors=4)[0]
    d = t.deidentified()
    assert d["contributor_count"] == 4 and d["session_count"] == 4
    assert "contributors" not in d and "members" not in d  # no names/ids leak


# ── server endpoints: founder de-identified vs manager named (+ audit) ───────
def _app() -> tuple[TestClient, ServerStore]:
    config = ServerConfig(
        jwt_secret="x" * 40, admin_token="adm", manager_token="mgr", k_anon_floor=4
    )
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    return TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([]))), store


def test_founder_topics_deidentified_and_kanon() -> None:
    client, store = _app()
    for i in range(4):
        store.ingest_compaction(
            _c(f"pg{i}", f"e{i}@x", f"s{i}", "optimize postgres queries", released=True),
            org_id="o1", team_id="t1",
        )
    resp = client.get("/v1/founder/topics", params={"org_id": "o1"}, headers=ADMIN)
    topics_out = resp.json()["topics"]
    assert topics_out and topics_out[0]["contributor_count"] == 4
    assert "contributors" not in topics_out[0]  # de-identified


def test_manager_topics_named_and_audited() -> None:
    client, store = _app()
    for i in range(4):
        store.ingest_compaction(
            _c(f"pg{i}", f"e{i}@x", f"s{i}", "optimize postgres queries", released=True),
            org_id="o1", team_id="t1",
        )
    assert client.get("/v1/manager/topics", params={"org_id": "o1"}).status_code == 401  # no token
    out = client.get("/v1/manager/topics", params={"org_id": "o1"}, headers=MGR).json()["topics"]
    assert out and "contributors" in out[0]  # named for the manager
    audit = client.get("/v1/admin/audit", params={"org_id": "o1"}, headers=ADMIN).json()["entries"]
    assert any(e["individual"] for e in audit)


def test_manager_thread_returns_arc_and_audits() -> None:
    client, store = _app()
    for sid in ("base", "base.2", "base.3"):
        store.ingest_compaction(
            _c(sid, "suraj@x", sid, f"work in {sid}", released=True), org_id="o1", team_id="t1"
        )
    r = client.post(
        "/v1/manager/thread", json={"org_id": "o1", "session_id": "base.2"}, headers=MGR
    ).json()
    assert [a["id"] for a in r["arc"]] == ["base", "base.2", "base.3"]  # full arc by thread_key
    audit = client.get("/v1/admin/audit", params={"org_id": "o1"}, headers=ADMIN).json()["entries"]
    assert any(e["individual"] and "thread" in e["query"] for e in audit)
