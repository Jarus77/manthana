"""Per-org LLM metering + monthly quota caps (hosted multi-tenant).

Runs on in-memory SQLite + scripted providers — no Postgres/network/key needed.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import MockProvider, ScriptedProvider
from manthana.server.metering import (
    MeteredProvider,
    QuotaExceededError,
    estimate_cost_usd,
    month_key,
)
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}


def _comp(cid: str, actor: str, project: str = "demo") -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=cid,
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=f"intent {cid}",
        approach="a",
        outcome=Outcome.success,
        est_cost_usd=0.5,
        tier_used="opus",
        released=True,
    )


def _make(provider: ScriptedProvider | None = None, cap: float = 0.0):
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", llm_monthly_cap_usd=cap)
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, provider or ScriptedProvider([])))
    return client, config, store, obj


def _seed_contributors(store: ServerStore, n: int, org: str = "o1") -> None:
    store.create_org(org, "Org")
    for i in range(n):
        store.ingest_compaction(_comp(f"c{i}", f"e{i}@x.com"), org_id=org, team_id="t1")


# ── unit: cost table + store accounting ───────────────────────────────────
def test_estimate_cost_matches_tier_rates() -> None:
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 0) == 3.0
    assert estimate_cost_usd("claude-opus-4-8", 0, 1_000_000) == 75.0
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == 6.0
    assert estimate_cost_usd("unknown-model", 1_000_000, 0) == 3.0  # sonnet-class default


def test_usage_accumulates_and_zero_row_when_absent() -> None:
    store = ServerStore.open("sqlite://")
    month = month_key()
    empty = store.get_llm_usage("o1", month)
    assert (empty.calls, empty.est_cost_usd) == (0, 0.0)
    store.add_llm_usage("o1", month, input_tokens=100, output_tokens=50, est_cost_usd=0.01)
    store.add_llm_usage("o1", month, input_tokens=10, output_tokens=5, est_cost_usd=0.02)
    row = store.get_llm_usage("o1", month)
    assert row.calls == 2
    assert row.input_tokens == 110
    assert row.output_tokens == 55
    assert row.est_cost_usd == pytest.approx(0.03)
    # other org / other month unaffected
    assert store.get_llm_usage("o2", month).calls == 0


def test_org_quota_override_roundtrip() -> None:
    store = ServerStore.open("sqlite://")
    assert store.get_org_quota("o1") is None
    store.set_org_quota("o1", 12.5)
    assert store.get_org_quota("o1") == 12.5
    store.set_org_quota("o1", None)  # clear → back to server default
    assert store.get_org_quota("o1") is None


def test_metered_provider_records_and_enforces() -> None:
    store = ServerStore.open("sqlite://")
    inner = ScriptedProvider(["hello world response"] * 5)
    metered = MeteredProvider(inner, store, "o1", cap_usd=10.0)
    assert metered.complete("a prompt" * 10) == "hello world response"
    assert store.get_llm_usage("o1", month_key()).calls == 1
    # push spend past the cap → next call refuses BEFORE hitting the provider
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=10.0)
    with pytest.raises(QuotaExceededError):
        metered.complete("another prompt")
    assert len(inner.calls) == 1  # the refused call never reached the provider


def test_metered_provider_cap_zero_is_unlimited() -> None:
    store = ServerStore.open("sqlite://")
    metered = MeteredProvider(ScriptedProvider(["ok"] * 3), store, "o1", cap_usd=0.0)
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=999.0)
    assert metered.complete("p") == "ok"  # still records, never refuses
    assert store.get_llm_usage("o1", month_key()).calls == 2


# ── API: quota surfaces as 429 (never "insufficient data") ────────────────
def test_founder_query_returns_429_when_quota_exhausted() -> None:
    client, _config, store, _obj = _make(cap=5.0)
    _seed_contributors(store, 5)
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=5.0)
    resp = client.post(
        "/v1/founder/query", json={"org_id": "o1", "query": "what happened?"}, headers=ADMIN
    )
    assert resp.status_code == 429
    assert "monthly AI budget" in resp.json()["detail"]


def test_org_override_beats_server_default() -> None:
    # Server default is unlimited (0), but the org's own cap of $1 is exhausted → 429.
    client, _config, store, _obj = _make(cap=0.0)
    _seed_contributors(store, 5)
    assert client.put(
        "/v1/admin/orgs/o1/quota", json={"monthly_cap_usd": 1.0}, headers=ADMIN
    ).is_success
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=1.0)
    resp = client.post(
        "/v1/founder/query", json={"org_id": "o1", "query": "what happened?"}, headers=ADMIN
    )
    assert resp.status_code == 429


def test_usage_endpoint_reports_spend_and_cap() -> None:
    client, _config, store, _obj = _make(cap=25.0)
    store.create_org("o1", "Org")
    store.add_llm_usage("o1", month_key(), input_tokens=100, output_tokens=50, est_cost_usd=0.5)
    data = client.get("/v1/admin/usage", params={"org_id": "o1"}, headers=ADMIN).json()
    assert data["monthly_cap_usd"] == 25.0
    assert data["cap_is_override"] is False
    assert data["months"][0]["calls"] == 1
    assert data["months"][0]["est_cost_usd"] == 0.5
    assert data["spent_usd"] == 0.5
    assert data["quota_blocked"] is False


def test_usage_endpoint_reports_a_blocked_org() -> None:
    """An exhausted cap has no other visible symptom.

    Nothing errors where a human looks: enrichment simply stops, every session
    stays `pending`, and the wiki fills with unsummarised work that reads as a
    bug. This flag is the only place the real cause is stated, so it must agree
    with the gate exactly — hence the boundary (spent == cap) rather than over.
    """
    client, _config, store, _obj = _make(cap=25.0)
    store.create_org("o1", "Org")
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=25.0)
    data = client.get("/v1/admin/usage", params={"org_id": "o1"}, headers=ADMIN).json()
    assert data["spent_usd"] == 25.0
    assert data["quota_blocked"] is True


def test_unlimited_org_is_never_reported_blocked() -> None:
    """cap 0 means unlimited — the self-hosted default. Spend must never flip it."""
    client, _config, store, _obj = _make(cap=0.0)
    store.create_org("o1", "Org")
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=999.0)
    data = client.get("/v1/admin/usage", params={"org_id": "o1"}, headers=ADMIN).json()
    assert data["quota_blocked"] is False


def test_usage_and_quota_endpoints_require_admin() -> None:
    client, *_ = _make()
    assert client.get("/v1/admin/usage", params={"org_id": "o1"}).status_code == 401
    assert client.put("/v1/admin/orgs/o1/quota", json={"monthly_cap_usd": 1}).status_code == 401


def test_negative_quota_rejected() -> None:
    client, *_ = _make()
    resp = client.put(
        "/v1/admin/orgs/o1/quota", json={"monthly_cap_usd": -1}, headers=ADMIN
    )
    assert resp.status_code == 422


def test_queries_meter_usage_per_org() -> None:
    # A successful founder query records usage under the queried org only.
    provider = ScriptedProvider(["{}", "The team shipped things [c0]."])
    client, _config, store, _obj = _make(provider, cap=25.0)
    _seed_contributors(store, 5)
    resp = client.post(
        "/v1/founder/query", json={"org_id": "o1", "query": "what shipped?"}, headers=ADMIN
    )
    assert resp.status_code == 200
    usage = store.get_llm_usage("o1", month_key())
    assert usage.calls == 2  # filter parse + narrative
    assert usage.est_cost_usd > 0
    assert store.get_llm_usage("other", month_key()).calls == 0


def test_ui_query_shows_quota_banner_not_insufficient_data() -> None:
    client, _config, store, _obj = _make(cap=5.0)
    _seed_contributors(store, 5)
    store.add_llm_usage("o1", month_key(), input_tokens=0, output_tokens=0, est_cost_usd=5.0)
    client.post("/ui/login", data={"token": "adm"})
    resp = client.post("/ui/query", data={"org_id": "o1", "query": "what happened?"})
    assert resp.status_code == 429
    assert "Monthly AI quota reached" in resp.text
    assert "insufficient data" not in resp.text


def test_console_shows_monthly_budget_column() -> None:
    client, _config, store, _obj = _make(cap=25.0)
    store.create_org("o1", "Org")
    store.add_llm_usage("o1", month_key(), input_tokens=100, output_tokens=50, est_cost_usd=0.5)
    client.post("/ui/login", data={"token": "adm"})
    page = client.get("/ui").text
    assert "AI budget (mo)" in page
    assert "$0.50 / $25.00" in page


# ── per-pass attribution ─────────────────────────────────────────────────
def _usage_store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Org")
    return store


def test_purpose_rows_accumulate_alongside_the_org_bucket() -> None:
    store = _usage_store()
    p1 = MeteredProvider(MockProvider("x" * 40), store, "o1", 0.0, purpose="enrich")
    p2 = MeteredProvider(MockProvider("y" * 40), store, "o1", 0.0, purpose="ask")
    p1.complete("prompt one")
    p1.complete("prompt two")
    p2.complete("prompt three")

    from manthana.server.metering import month_key

    month = month_key()
    org = store.get_llm_usage("o1", month)
    purposes = {r.purpose: r for r in store.list_llm_usage_purposes("o1", month)}
    assert org.calls == 3
    assert purposes["enrich"].calls == 2 and purposes["ask"].calls == 1
    # Attribution reconciles with the bucket the cap enforces against.
    assert sum(r.calls for r in purposes.values()) == org.calls
    assert abs(sum(r.est_cost_usd for r in purposes.values()) - org.est_cost_usd) < 1e-9


def test_unlabelled_provider_writes_only_the_org_bucket() -> None:
    store = _usage_store()
    MeteredProvider(MockProvider("z" * 40), store, "o1", 0.0).complete("p")
    from manthana.server.metering import month_key

    assert store.get_llm_usage("o1", month_key()).calls == 1
    assert store.list_llm_usage_purposes("o1", month_key()) == []


def test_cap_enforcement_ignores_purpose_mix() -> None:
    """The cap stays whole-org: attribution is a lens, never a loophole."""
    store = _usage_store()
    spender = MeteredProvider(MockProvider("x" * 4000), store, "o1", 0.000001, purpose="ask")
    spender.complete("first call is allowed to overshoot")
    try:
        MeteredProvider(MockProvider("y"), store, "o1", 0.000001, purpose="enrich").complete("p")
        raise AssertionError("expected QuotaExceededError")
    except QuotaExceededError:
        pass
