"""Server-side digest enrichment.

Agents emit deterministic ``source="pending"`` digests; the server fills the
qualitative fields on the operator's metered key. Covers the input preference
(native_summary over raw), the wait-don't-burn-a-call path when neither has
arrived, quota enforcement, and the files_touched authority invariant.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.schemas import EngineeringCompaction, Outcome, Role, Surface, Turn
from manthana.server import ServerConfig, ServerStore
from manthana.server.enrich import apply_enrichment, enrich_org, run_enrichment_pass
from manthana.server.llm import MockProvider, ScriptedProvider
from manthana.server.metering import MeteredProvider
from manthana.server.storage import InMemoryObjectStore
from manthana.server.tables import EnrichmentStateRow

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# What the model is asked to return — the full qualitative field set.
_GOOD = json.dumps(
    {
        "task_intent": "wire up the webhook retry path",
        "approach": "traced api/webhook.py, added backoff",
        "artifacts": ["api/webhook.py"],
        "outcome": "success",
        "reusable_pattern": True,
        "friction_points": [
            {"category": "retry", "description": "429s from upstream", "turn_refs": ["3"]}
        ],
        "files_touched": ["data/train.csv"],
        "prs_opened": ["#12"],
        "tests_added": ["tests/test_webhook.py"],
        "dead_end_branches": [],
        "languages": ["python"],
        "frameworks": ["fastapi"],
    }
)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


def _pending(cid: str = "c1", *, native: str | None = None) -> EngineeringCompaction:
    """A digest exactly as the agent emits it: deterministic fields filled,
    qualitative fields empty, ``source="pending"``."""
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="e@x.com",
        surface=Surface.claude_code,
        project="demo",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=42.0,
        task_intent="fix the webhook",  # crude fallback: first user turn
        approach="",
        outcome=Outcome.partial,
        tier_used="opus",
        est_cost_usd=0.5,
        total_tokens=1234,
        released=True,
        source="pending",
        native_summary=native,
        files_touched=["/repo/api/webhook.py"],  # from real tool calls — authoritative
    )


def _seeded(comp: EngineeringCompaction | None = None) -> tuple[ServerStore, InMemoryObjectStore]:
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    store.create_org("o1", "Org")
    store.ingest_compaction(comp or _pending(), org_id="o1", team_id="t1")
    return store, obj


def _fetch(store: ServerStore, cid: str = "c1") -> EngineeringCompaction:
    """Fetch a stored digest, narrowed for the type checker (and asserting it is
    still there — a silently-vanished row would otherwise read as a pass)."""
    out = store.get_compaction(cid, "o1")
    assert isinstance(out, EngineeringCompaction)
    return out


def _state(store: ServerStore, cid: str = "c1") -> EnrichmentStateRow:
    row = store.get_enrichment_state("o1", cid)
    assert row is not None
    return row


def _put_raw(store: ServerStore, obj: InMemoryObjectStore, cid: str = "c1", n: int = 3) -> None:
    """Raw as the agent uploads it: JSONL, one serialized Turn per line."""
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


# ── input preference ──────────────────────────────────────────────────────
def test_prefers_native_summary_over_raw() -> None:
    # A digest carrying the coding agent's OWN compaction summary must enrich from
    # it — that's the cheap path (a fraction of the transcript's tokens).
    store, obj = _seeded(_pending(native="PRIOR STATE: refactored the webhook handler"))
    _put_raw(store, obj)  # raw exists too, but must not be the input
    provider = MockProvider(_GOOD)

    stats = enrich_org(store, obj, provider, _config(), org_id="o1", limit=10)

    assert stats.enriched == 1
    prompt = provider.calls[0]
    assert "PRIOR_SUMMARY" in prompt
    assert "PRIOR STATE: refactored the webhook handler" in prompt
    assert "turn 0 content" not in prompt  # raw was not rehydrated
    assert _fetch(store).source == "claude_summary"


def test_falls_back_to_raw_when_no_native_summary() -> None:
    store, obj = _seeded()  # no native_summary
    _put_raw(store, obj)
    provider = MockProvider(_GOOD)

    stats = enrich_org(store, obj, provider, _config(), org_id="o1", limit=10)

    assert stats.enriched == 1
    prompt = provider.calls[0]
    assert "PRIOR_SUMMARY" not in prompt
    assert "turn 0 content" in prompt  # real Turn objects rehydrated from the blob
    enriched = _fetch(store)
    assert enriched.source == "full"
    assert enriched.approach == "traced api/webhook.py, added backoff"
    assert enriched.outcome is Outcome.success


def test_waits_when_neither_summary_nor_raw_has_arrived() -> None:
    # Metadata and raw are separate requests, so raw may lag or never arrive.
    # The pass must NOT burn a model call on an empty transcript.
    store, obj = _seeded()  # no native_summary, no raw uploaded
    provider = MockProvider(_GOOD)

    stats = enrich_org(store, obj, provider, _config(), org_id="o1", limit=10)

    assert stats.waiting == 1 and stats.enriched == 0
    assert provider.calls == []  # nothing spent
    assert _fetch(store).source == "pending"  # untouched
    state = store.get_enrichment_state("o1", "c1")
    assert state is not None and state.state == "waiting" and state.attempts == 1


def test_waiting_digest_is_abandoned_after_max_attempts() -> None:
    # A bounded number of attempts — it can never retry forever.
    store, obj = _seeded()
    config = _config(enrich_max_attempts=3)
    provider = MockProvider(_GOOD)

    for _ in range(3):
        enrich_org(store, obj, provider, config, org_id="o1", limit=10)
    stats = enrich_org(store, obj, provider, config, org_id="o1", limit=10)

    assert stats.abandoned == 1
    assert _state(store).state == "abandoned"
    # Abandoned digests are never picked up again.
    assert store.list_pending_for_enrichment(
        "o1", skip_ids=store.abandoned_enrichment_ids("o1")
    ) == []
    assert provider.calls == []


def test_waiting_digest_ages_out_even_below_the_attempt_cap() -> None:
    store, obj = _seeded()
    config = _config(enrich_max_attempts=99, enrich_max_age_days=7)
    provider = MockProvider(_GOOD)

    enrich_org(store, obj, provider, config, org_id="o1", limit=10)  # first_seen stamped
    later = datetime.now(UTC) + timedelta(days=8)
    stats = enrich_org(store, obj, provider, config, org_id="o1", limit=10, now=later)

    assert stats.abandoned == 1
    assert "7d" in _state(store).detail


# ── metering / quota ──────────────────────────────────────────────────────
def test_quota_exceeded_leaves_digest_pending_and_does_not_crash() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))
    # Burn the org's whole monthly budget before the pass runs.
    store.set_org_quota("o1", 1.0)
    store.add_llm_usage(
        "o1", datetime.now(UTC).strftime("%Y-%m"),
        input_tokens=0, output_tokens=0, est_cost_usd=5.0,
    )
    metered = MeteredProvider(MockProvider(_GOOD), store, "o1", 1.0)

    stats = enrich_org(store, obj, metered, _config(), org_id="o1", limit=10)

    assert stats.quota_blocked == 1 and stats.enriched == 0
    assert _fetch(store).source == "pending"  # no partial write
    # Not counted as an attempt against the digest — nothing was tried on it.
    assert store.get_enrichment_state("o1", "c1") is None


def test_enrichment_usage_is_metered_against_the_org() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))
    metered = MeteredProvider(MockProvider(_GOOD), store, "o1", 0.0)  # 0 = unlimited

    enrich_org(store, obj, metered, _config(), org_id="o1", limit=10)

    usage = store.get_llm_usage("o1", datetime.now(UTC).strftime("%Y-%m"))
    assert usage.calls == 1 and usage.input_tokens > 0


# ── deterministic-field invariants ────────────────────────────────────────
def test_never_overwrites_files_touched_or_other_deterministic_fields() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))

    enrich_org(store, obj, MockProvider(_GOOD), _config(), org_id="o1", limit=10)
    out = _fetch(store)

    # The agent's tool-call-derived path is kept verbatim and FIRST.
    assert out.files_touched[0] == "/repo/api/webhook.py"
    # A model-suggested path that passes the sanity check may only be APPENDED.
    assert out.files_touched == ["/repo/api/webhook.py", "data/train.csv"]
    # Deterministic fields survive untouched.
    assert out.duration_seconds == 42.0
    assert out.total_tokens == 1234
    assert out.est_cost_usd == 0.5
    assert out.tier_used == "opus"
    assert out.actor == "e@x.com" and out.project == "demo"
    assert out.released is True


def test_model_suggested_junk_paths_are_rejected() -> None:
    # A dataset description is not a path — the sanity gate drops it.
    data: dict[str, object] = {
        "files_touched": ["patents (5.4GB)", "Mongo: articles_db", "src/ok.py"]
    }
    out = apply_enrichment(_pending(), data, used_summary=False)
    assert out.files_touched == ["/repo/api/webhook.py", "src/ok.py"]


def test_malformed_model_output_leaves_digest_pending() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))

    stats = enrich_org(
        store, obj, MockProvider("sorry, I cannot help"), _config(), org_id="o1", limit=10
    )

    assert stats.failed == 1 and stats.enriched == 0
    assert _fetch(store).source == "pending"
    assert _state(store).state == "failed"


def test_provider_exception_does_not_kill_the_batch() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))
    store.ingest_compaction(_pending("c2", native="PRIOR STATE 2"), org_id="o1", team_id="t1")

    class _Boom:
        name = "boom"
        calls = 0

        def complete(self, prompt: str) -> str:
            _Boom.calls += 1
            if _Boom.calls == 1:
                raise RuntimeError("upstream 500")
            return _GOOD

    stats = enrich_org(store, obj, _Boom(), _config(), org_id="o1", limit=10)

    # One failed, one still enriched — the batch kept going.
    assert stats.failed == 1 and stats.enriched == 1


def test_prompt_version_is_stamped_for_traceability() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))
    enrich_org(store, obj, MockProvider(_GOOD), _config(), org_id="o1", limit=10)
    assert _fetch(store).prompt_version == "v2-summary"

    store2, obj2 = _seeded()
    _put_raw(store2, obj2)
    enrich_org(store2, obj2, MockProvider(_GOOD), _config(), org_id="o1", limit=10)
    assert _fetch(store2).prompt_version == "v2"


# ── batching ──────────────────────────────────────────────────────────────
def test_pass_is_bounded_per_org_so_one_org_cannot_starve_others() -> None:
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    for org in ("o1", "o2"):
        store.create_org(org, org)
        for i in range(5):
            store.ingest_compaction(
                _pending(f"{org}-c{i}", native="PRIOR STATE"), org_id=org, team_id="t1"
            )
    config = _config(enrich_batch_per_org=2, enrich_max_batch=10)
    provider = ScriptedProvider([_GOOD] * 20)

    stats = run_enrichment_pass(store, obj, config, lambda _org: provider)

    # Both orgs got served, neither more than its per-org cap.
    assert stats.enriched == 4
    assert set(stats.orgs) == {"o1", "o2"}
    assert store.count_pending_for_enrichment("o1") == 3
    assert store.count_pending_for_enrichment("o2") == 3


def test_enriched_digests_are_not_picked_up_again() -> None:
    store, obj = _seeded(_pending(native="PRIOR STATE"))
    config = _config()

    first = enrich_org(store, obj, MockProvider(_GOOD), config, org_id="o1", limit=10)
    second = enrich_org(store, obj, MockProvider(_GOOD), config, org_id="o1", limit=10)

    assert first.enriched == 1
    assert second.enriched == 0  # no longer "pending"
    assert store.get_enrichment_state("o1", "c1") is None  # bookkeeping cleared
