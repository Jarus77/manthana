"""Router analyzer — counterfactual re-pricing + downgrade heuristic (roadmap phase D).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.schemas import (
    EngineeringCompaction,
    FrictionCategory,
    FrictionPoint,
    Outcome,
    Surface,
)
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.analyzer import analyze_counterfactual_costs
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _comp(
    cid: str, *, outcome: Outcome = Outcome.success, tier: str = "opus",
    friction: list[FrictionPoint] | None = None, breakdown: bool = True,
) -> EngineeringCompaction:
    ti, to, cw, cr = (1000, 2000, 0, 100_000) if breakdown else (None, None, None, None)
    return EngineeringCompaction(
        id=cid, session_id=cid, actor="e@x.com", surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent="t", approach="a",
        outcome=outcome, tier_used=tier, total_tokens=103_000,
        input_tokens=ti, output_tokens=to, cache_write_tokens=cw, cache_read_tokens=cr,
        friction_points=friction or [], released=True,
    )


def _store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    return store


def test_reprices_and_flags_safe_downgrades() -> None:
    store = _store()
    store.ingest_compaction(_comp("clean"), org_id="o1", team_id="t")  # safe → opus→sonnet
    store.ingest_compaction(  # loop friction → NOT safe
        _comp("hard", friction=[FrictionPoint(category=FrictionCategory.loop, description="x")]),
        org_id="o1", team_id="t",
    )
    rep = analyze_counterfactual_costs(store, "o1")
    assert rep.priced == 2 and rep.skipped_no_tokens == 0
    by_id = {r.id: r for r in rep.rows}
    # exact re-pricing: opus 0.315, sonnet 0.063 → save 0.252 on the clean session
    assert abs(by_id["clean"].current_usd - 0.315) < 1e-3
    assert by_id["clean"].safe_to_downgrade and by_id["clean"].target_tier == "sonnet"
    assert abs(by_id["clean"].savings_usd - 0.252) < 1e-3
    # the loop session is kept on opus (no downgrade, no savings)
    assert by_id["hard"].safe_to_downgrade is False and by_id["hard"].target_tier is None
    assert by_id["hard"].savings_usd == 0.0
    assert rep.by_target == {"sonnet": 1} and rep.savings_usd > 0


def test_abandoned_is_not_downgraded() -> None:
    store = _store()
    store.ingest_compaction(_comp("ab", outcome=Outcome.abandoned), org_id="o1", team_id="t")
    rep = analyze_counterfactual_costs(store, "o1")
    assert rep.rows[0].safe_to_downgrade is False and rep.savings_usd == 0.0


def test_skips_pre_breakdown_digests() -> None:
    store = _store()
    store.ingest_compaction(_comp("old", breakdown=False), org_id="o1", team_id="t")
    rep = analyze_counterfactual_costs(store, "o1")
    assert rep.priced == 0 and rep.skipped_no_tokens == 1 and rep.rows == []


def test_router_endpoint_admin_gated() -> None:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = _store()
    store.ingest_compaction(_comp("clean"), org_id="o1", team_id="t")
    client = TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])))
    assert client.get("/v1/admin/router-analysis?org_id=o1").status_code == 401  # no token
    resp = client.get("/v1/admin/router-analysis?org_id=o1", headers={"X-Admin-Token": "adm"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["priced"] == 1 and body["savings_usd"] > 0 and body["by_target"] == {"sonnet": 1}
