"""Founder weekly digest — composition + k-anon omission (roadmap phase E).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.digest import build_weekly_digest, default_window
from manthana.server.founder import run_query
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore
from manthana.skills.embed import HashingEmbedder

_T0 = datetime(2026, 3, 10, tzinfo=UTC)


def _comp(cid: str, actor: str) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=cid, actor=actor, surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent="ship the thing",
        approach="a", outcome=Outcome.success, released=True,
    )


def _store(n_actors: int) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(n_actors):
        store.ingest_compaction(_comp(f"comp-{i}", f"e{i}@x.com"), org_id="o1", team_id="t")
    return store


def test_default_window_is_seven_days() -> None:
    since, until = default_window(datetime(2026, 6, 25, tzinfo=UTC))
    assert since == "2026-06-18" and until == "2026-06-25"


def test_run_query_since_until_override() -> None:
    store = _store(1)
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=1)
    res = run_query(
        store, cfg, org_id="o1", query="what shipped?", provider=ScriptedProvider(["{}"]),
        since="2026-03-01", until="2026-03-31",
    )
    assert res.filter.since == "2026-03-01" and res.filter.until == "2026-03-31"


def test_weekly_digest_happy_path() -> None:
    store = _store(4)  # 4 contributors → clears the k-anon floor of 4
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=4)
    # 3 sections × (parse + narrative); narrative cites a visible id so it grounds.
    scripted = ScriptedProvider(["{}", "shipped [comp-0]"] * 3)
    d = build_weekly_digest(
        store, cfg, org_id="o1", provider=scripted, since="2026-03-01", until="2026-03-31",
        embedder=HashingEmbedder(),
    )
    assert len(d.sections) == 3 and d.omitted == []
    assert all("comp-0" in s.citations for s in d.sections)
    assert d.since == "2026-03-01" and d.until == "2026-03-31"


def test_weekly_digest_omits_insufficient_sections() -> None:
    store = _store(2)  # 2 contributors < floor 4 → every section insufficient
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=4)
    d = build_weekly_digest(
        store, cfg, org_id="o1", provider=ScriptedProvider(["{}"] * 6),
        since="2026-03-01", until="2026-03-31", embedder=HashingEmbedder(),
    )
    assert d.sections == [] and len(d.omitted) == 3  # withheld, never leaked


def test_digest_endpoint_admin_gated() -> None:
    store = _store(4)
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=4)
    provider = ScriptedProvider(["{}", "shipped [comp-0]"] * 3)
    client = TestClient(create_app(cfg, store, InMemoryObjectStore(), provider))
    assert client.get("/v1/admin/digest?org_id=o1").status_code == 401  # no token
    resp = client.get(
        "/v1/admin/digest?org_id=o1&since=2026-03-01&until=2026-03-31",
        headers={"X-Admin-Token": "adm"},
    )
    assert resp.status_code == 200
    assert resp.json()["since"] == "2026-03-01"
