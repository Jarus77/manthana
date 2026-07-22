"""DRY-RUN enrichment model comparison.

The invariant under test above all others: comparing a candidate model must not
touch the data being compared. Everything else here (diffing, parse failures,
skips, bounds, admin auth) protects a report the founder is going to spend money
on the strength of.

Hermetic: in-memory SQLite + in-memory object store + scripted providers.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Role, Surface, Turn
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.enrich import MAX_ITEMS, compare_enrichment, summarize
from manthana.server.llm import MockProvider, ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}

# The stored (baseline) qualitative text, as production's Haiku pass wrote it.
_BASELINE = {
    "task_intent": "wire up the webhook retry path",
    "approach": "traced api/webhook.py and added exponential backoff",
    "artifacts": ["api/webhook.py"],
    "outcome": Outcome.success,
    "languages": ["python"],
    "frameworks": ["fastapi"],
}

# A candidate that agrees on some fields and disagrees on others.
_CANDIDATE = json.dumps(
    {
        "task_intent": "make webhooks retry",  # DIFFERS
        "approach": "traced api/webhook.py and added exponential backoff",  # IDENTICAL
        "artifacts": ["api/webhook.py"],  # IDENTICAL
        "outcome": "success",  # IDENTICAL
        "reusable_pattern": False,
        "friction_points": [],
        "files_touched": [],
        "prs_opened": [],
        "tests_added": [],
        "dead_end_branches": [],
        "languages": ["python"],  # IDENTICAL
        "frameworks": ["fastapi", "httpx"],  # DIFFERS
    }
)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


def _enriched(cid: str = "c1", *, started: datetime = _T0) -> EngineeringCompaction:
    """A digest as it looks AFTER production enrichment — this is the baseline."""
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="e@x.com",
        surface=Surface.claude_code,
        project="demo",
        started_at=started,
        ended_at=started,
        duration_seconds=42.0,
        tier_used="opus",
        est_cost_usd=0.5,
        total_tokens=1234,
        released=True,
        source="full",
        files_touched=["/repo/api/webhook.py"],
        **_BASELINE,  # type: ignore[arg-type]
    )


def _pending(cid: str = "p1") -> EngineeringCompaction:
    comp = _enriched(cid)
    comp.source = "pending"
    comp.approach = ""
    return comp


def _seed(
    *comps: EngineeringCompaction, raw_for: tuple[str, ...] = ()
) -> tuple[ServerStore, InMemoryObjectStore]:
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    store.create_org("o1", "Org")
    for comp in comps:
        store.ingest_compaction(comp, org_id="o1", team_id="t1")
    for cid in raw_for:
        _put_raw(store, obj, cid)
    return store, obj


def _put_raw(store: ServerStore, obj: InMemoryObjectStore, cid: str, n: int = 3) -> None:
    turns = [
        Turn(
            id=f"{cid}-{i}",
            session_id=f"s-{cid}",
            actor="e@x.com",
            seq=i,
            role=Role.user,
            content=f"turn {i} content",
        )
        for i in range(n)
    ]
    key = f"o1/t1/{cid}.jsonl"
    obj.put(key, "\n".join(t.model_dump_json() for t in turns).encode("utf-8"))
    store.record_raw(cid, "o1", key)


def _snapshot(store: ServerStore, ids: list[str]) -> dict[str, dict]:
    """Every stored digest's full payload — the thing that must not change."""
    out: dict[str, dict] = {}
    for cid in ids:
        comp = store.get_compaction(cid, "o1")
        assert comp is not None
        out[cid] = comp.model_dump(mode="json")
    return out


# ── THE invariant: a comparison never mutates anything ────────────────────
def test_compare_does_not_mutate_the_store() -> None:
    store, obj = _seed(_enriched("c1"), _enriched("c2"), raw_for=("c1", "c2"))
    before = _snapshot(store, ["c1", "c2"])

    items = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="cheap-model", limit=10,
    )

    assert [i.compared for i in items] == [True, True]  # it really did run
    assert _snapshot(store, ["c1", "c2"]) == before  # ... and changed nothing
    # Nor did it invent enrichment bookkeeping for digests it merely read.
    assert store.get_enrichment_state("o1", "c1") is None
    assert store.list_enrichment_state("o1") == []


def test_compare_leaves_the_store_alone_even_when_the_candidate_fails() -> None:
    # A failed candidate must not "fall back" to writing anything either.
    store, obj = _seed(_enriched("c1"), raw_for=("c1",))
    before = _snapshot(store, ["c1"])

    compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider("I'm sorry, I can't do that."),
        candidate_label="cheap-model", limit=10,
    )

    assert _snapshot(store, ["c1"]) == before


# ── the diff itself ───────────────────────────────────────────────────────
def test_differing_and_identical_fields_are_reported_as_such() -> None:
    store, obj = _seed(_enriched("c1"), raw_for=("c1",))

    (item,) = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="cheap-model",
    )

    diffs = {f.name: f for f in item.fields}
    assert diffs["task_intent"].identical is False
    assert diffs["task_intent"].baseline == "wire up the webhook retry path"
    assert diffs["task_intent"].candidate == "make webhooks retry"
    assert diffs["frameworks"].identical is False
    assert diffs["frameworks"].candidate == "fastapi; httpx"
    assert diffs["approach"].identical is True
    assert diffs["outcome"].identical is True
    assert diffs["languages"].identical is True
    assert {f.name for f in item.differing} == {"task_intent", "frameworks"}
    assert "approach" in item.identical_names


def test_the_production_prompt_is_used_verbatim() -> None:
    # A different prompt would measure the prompt, not the model.
    from manthana.server.enrich import build_prompt
    from manthana.server.enrich.enricher import _load_raw_turns, _session_for

    store, obj = _seed(_enriched("c1"), raw_for=("c1",))
    provider = MockProvider(_CANDIDATE)

    compare_enrichment(
        store, obj, _config(), "o1", provider=provider, candidate_label="cheap-model"
    )

    stored = store.get_compaction("c1", "o1")
    assert stored is not None
    turns = _load_raw_turns(store, obj, "c1", "o1")
    assert provider.calls[0] == build_prompt(_session_for(stored, len(turns)), turns)


def test_native_summary_path_is_taken_when_production_would_have() -> None:
    comp = _enriched("c1")
    comp.native_summary = "PRIOR STATE: refactored the webhook handler"
    store, obj = _seed(comp, raw_for=("c1",))
    provider = MockProvider(_CANDIDATE)

    (item,) = compare_enrichment(
        store, obj, _config(), "o1", provider=provider, candidate_label="cheap-model"
    )

    assert item.used_summary is True
    assert "PRIOR_SUMMARY" in provider.calls[0]


# ── failures are results, not crashes ─────────────────────────────────────
def test_unparseable_output_is_a_recorded_result_and_the_run_continues() -> None:
    store, obj = _seed(_enriched("c1"), _enriched("c2"), raw_for=("c1", "c2"))
    # First session: prose, no JSON. Second: a valid object.
    provider = ScriptedProvider(["Sure! Here is a summary of the session.", _CANDIDATE])

    items = compare_enrichment(
        store, obj, _config(), "o1", provider=provider, candidate_label="cheap-model", limit=10
    )

    assert len(items) == 2  # it did NOT abort on the first
    assert items[0].parse_failure is True and items[0].fields == []
    assert items[1].parse_failure is False and items[1].compared
    assert summarize(items)["parse_failures"] == 1


def test_a_provider_error_on_one_item_does_not_abort_the_run() -> None:
    class _Flaky:
        name = "flaky"

        def __init__(self) -> None:
            self.n = 0

        def complete(self, prompt: str) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("openrouter returned 503")
            return _CANDIDATE

    store, obj = _seed(_enriched("c1"), _enriched("c2"), raw_for=("c1", "c2"))

    items = compare_enrichment(
        store, obj, _config(), "o1", provider=_Flaky(), candidate_label="cheap-model", limit=10
    )

    assert len(items) == 2
    assert "503" in items[0].error
    assert items[1].compared
    assert summarize(items)["errors"] == 1


def test_digest_without_a_raw_transcript_is_skipped_with_a_reason() -> None:
    store, obj = _seed(_enriched("c1"))  # no raw uploaded, no native summary
    provider = MockProvider(_CANDIDATE)

    (item,) = compare_enrichment(
        store, obj, _config(), "o1", provider=provider, candidate_label="cheap-model"
    )

    assert "no raw transcript" in item.skipped
    assert item.fields == []
    assert provider.calls == []  # nothing spent on a session with no input


# ── selection ─────────────────────────────────────────────────────────────
def test_pending_digests_are_never_selected_as_baselines() -> None:
    # A pending digest has no stored qualitative text — comparing against it
    # would compare the candidate against empty strings and call them differences.
    store, obj = _seed(
        _pending("p1", ), _enriched("c1"), raw_for=("p1", "c1")
    )

    items = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="cheap-model", limit=10,
    )

    assert [i.compaction_id for i in items] == ["c1"]


def test_explicit_pending_id_is_skipped_rather_than_compared() -> None:
    store, obj = _seed(_pending("p1"), raw_for=("p1",))

    (item,) = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="cheap-model", ids=["p1"],
    )

    assert "no stored enrichment" in item.skipped


def test_limit_is_bounded_hard() -> None:
    # Each item is a paid call: an absurd --limit must cost pennies, not the cap.
    comps = [_enriched(f"c{i}") for i in range(MAX_ITEMS + 5)]
    store, obj = _seed(*comps, raw_for=tuple(c.id for c in comps))

    items = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="cheap-model", limit=10_000,
    )

    assert len(items) == MAX_ITEMS


def test_summary_reports_cost_and_latency() -> None:
    store, obj = _seed(_enriched("c1"), raw_for=("c1",))

    items = compare_enrichment(
        store, obj, _config(), "o1",
        provider=MockProvider(_CANDIDATE), candidate_label="claude-haiku-4-5",
    )
    summary = summarize(items)

    assert summary["compared"] == 1
    assert summary["input_tokens"] > 0  # chars/4 heuristic for a mock provider
    assert summary["total_cost_usd"] > 0
    assert summary["cost_per_token"] > 0


# ── the endpoint ──────────────────────────────────────────────────────────
def _client(provider: ScriptedProvider | None = None) -> tuple[TestClient, ServerStore]:
    config = _config()
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    store.create_org("o1", "Org")
    store.ingest_compaction(_enriched("c1"), org_id="o1", team_id="t1")
    _put_raw(store, obj, "c1")
    client = TestClient(create_app(config, store, obj, provider or ScriptedProvider([])))
    return client, store


def test_compare_endpoint_requires_admin() -> None:
    client, _ = _client()
    body = {"org_id": "o1", "model": "some/cheap-model"}

    assert client.post("/v1/admin/enrichment/compare", json=body).status_code == 401
    assert (
        client.post(
            "/v1/admin/enrichment/compare", json=body, headers={"X-Admin-Token": "wrong"}
        ).status_code
        == 401
    )


def test_compare_endpoint_runs_and_writes_nothing() -> None:
    # llm_provider defaults to "mock", so make_enrich_provider yields a mock that
    # returns "{}" — no network, no key. The point here is the shape and the
    # read-only guarantee, not the model's opinion.
    client, store = _client()
    before = _snapshot(store, ["c1"])

    resp = client.post(
        "/v1/admin/enrichment/compare",
        json={"org_id": "o1", "model": "some/cheap-model", "limit": 3},
        headers=ADMIN,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate_model"] == "some/cheap-model"
    assert data["baseline_model"] == "claude-haiku-4-5"
    assert data["summary"]["items"] == 1
    assert _snapshot(store, ["c1"]) == before


def test_compare_endpoint_rejects_an_unknown_provider() -> None:
    client, _ = _client()

    resp = client.post(
        "/v1/admin/enrichment/compare",
        json={"org_id": "o1", "model": "x", "provider": "not-a-provider"},
        headers=ADMIN,
    )

    assert resp.status_code == 400


def test_compare_endpoint_does_not_repoint_the_servers_own_config() -> None:
    # Running an experiment must never re-point the background enrichment loop at
    # an unvetted model. The candidate provider is built from a config COPY.
    config = _config()
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    store.create_org("o1", "Org")
    store.ingest_compaction(_enriched("c1"), org_id="o1", team_id="t1")
    _put_raw(store, obj, "c1")
    client = TestClient(create_app(config, store, obj, ScriptedProvider([])))

    client.post(
        "/v1/admin/enrichment/compare",
        json={"org_id": "o1", "model": "some/cheap-model", "provider": "openrouter"},
        headers=ADMIN,
    )

    assert config.enrich_model == "claude-haiku-4-5"
    assert config.llm_provider == "mock"


def test_compare_spend_is_attributed_to_the_compare_purpose() -> None:
    # It spends real money, so it must show up in /v1/admin/usage under its own
    # purpose AND count against the same org-wide cap as everything else.
    from manthana.server.metering import month_key

    client, store = _client()
    client.post(
        "/v1/admin/enrichment/compare",
        json={"org_id": "o1", "model": "some/cheap-model"},
        headers=ADMIN,
    )

    purposes = {r.purpose for r in store.list_llm_usage_purposes("o1", month_key())}
    assert "compare" in purposes
