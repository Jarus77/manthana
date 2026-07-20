"""Knowledge consolidation — enriched digests → typed org-wiki notes.

Covers the deterministic apply (all four relations, the human-authority law,
promotion thresholds, new-note gates), the pass runner (failure bookkeeping,
bounded attempts → abandoned, quota deferral), and the org batching bounds.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
    Outcome,
    Surface,
)
from manthana.server import ServerConfig, ServerStore
from manthana.server.consolidate import (
    apply_verdicts,
    consolidate_org,
    consolidate_provider_for,
    retrieve_candidates,
    run_consolidation_pass,
)
from manthana.server.llm import ScriptedProvider
from manthana.server.metering import MeteredProvider
from manthana.skills.embed import HashingEmbedder

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NOW = _T0 + timedelta(days=30)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


def _comp(
    cid: str = "c1", *, actor: str = "suraj@x.com", at: datetime = _T0
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project="bench",
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent="run the BIRD benchmark",
        approach="swept temperature, fixed the eval harness",
        outcome=Outcome.success,
        released=True,
        source="full",
        files_touched=["eval/harness.py"],
    )


def _note(
    nid: str = "kn-1",
    *,
    source: NoteSource = NoteSource.ai,
    status: NoteStatus = NoteStatus.candidate,
    evidence: list[str] | None = None,
    actors: list[str] | None = None,
    title: str = "BIRD accuracy is 61%",
) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=NoteKind.benchmark,
        title=title,
        body="Latest run of the BIRD benchmark hit 61% exec accuracy.",
        scope="project:bench",
        entities=NoteEntities(projects=["bench"], files=["eval/harness.py"]),
        evidence=evidence if evidence is not None else ["c0"],
        actors=actors if actors is not None else ["mira@x.com"],
        source=source,
        status=status,
        created_at=_T0,
        updated_at=_T0,
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Org")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


# ── deterministic apply (pure) ───────────────────────────────────────────
def test_supports_bumps_and_promotes() -> None:
    note = _note(evidence=["c0", "c9"], actors=["mira@x.com"])
    plan = apply_verdicts(
        _comp(), [note],
        {"verdicts": [{"note_id": "kn-1", "relation": "supports"}]},
        org_id="o1", now=_NOW,
    )
    assert plan.supported == 1 and len(plan.upserts) == 1
    updated = plan.upserts[0]
    assert updated.evidence == ["c0", "c9", "c1"]
    assert updated.actors == ["mira@x.com", "suraj@x.com"]
    assert updated.confidence == 0.6
    assert updated.last_confirmed_at == _NOW
    # ≥3 evidence sessions (and ≥2 actors) → established.
    assert updated.status == NoteStatus.established


def test_supports_below_thresholds_stays_candidate() -> None:
    note = _note(evidence=["c0"], actors=["suraj@x.com"])  # same actor as the session
    plan = apply_verdicts(
        _comp(), [note],
        {"verdicts": [{"note_id": "kn-1", "relation": "supports"}]},
        org_id="o1", now=_NOW,
    )
    assert plan.upserts[0].status == NoteStatus.candidate  # 2 evidence, 1 actor


def test_contradicts_badges_never_rewrites() -> None:
    note = _note(status=NoteStatus.established)
    plan = apply_verdicts(
        _comp(), [note],
        {"verdicts": [{"note_id": "kn-1", "relation": "contradicts"}]},
        org_id="o1", now=_NOW,
    )
    updated = plan.upserts[0]
    assert updated.status == NoteStatus.disputed
    assert updated.disputed_by == ["c1"]
    assert updated.body == note.body  # the claim text is untouched


def test_refines_supersedes_ai_note() -> None:
    note = _note(status=NoteStatus.established)
    plan = apply_verdicts(
        _comp(), [note],
        {"verdicts": [{"note_id": "kn-1", "relation": "refines",
                       "updated_body": "BIRD is now 64% after the harness fix."}]},
        org_id="o1", now=_NOW,
    )
    assert plan.refined == 1 and len(plan.supersedes) == 1
    old_id, new_version = plan.supersedes[0]
    assert old_id == "kn-1"
    assert new_version.id != "kn-1" and new_version.version == 2
    assert new_version.supersedes == "kn-1"
    assert new_version.body == "BIRD is now 64% after the harness fix."
    assert new_version.evidence == ["c0", "c1"]
    assert new_version.status == NoteStatus.established  # keeps earned status
    assert new_version.disputed_by == []


def test_refines_human_note_downgrades_to_contradicts() -> None:
    # The one law of the layer: AI may dispute a human note, never supersede it.
    note = _note(source=NoteSource.human, status=NoteStatus.established)
    plan = apply_verdicts(
        _comp(), [note],
        {"verdicts": [{"note_id": "kn-1", "relation": "refines",
                       "updated_body": "attempted rewrite"}]},
        org_id="o1", now=_NOW,
    )
    assert plan.refined == 0 and plan.disputed == 1 and not plan.supersedes
    updated = plan.upserts[0]
    assert updated.status == NoteStatus.disputed
    assert updated.body == note.body  # human body stays canonical


def test_new_notes_gated_capped_and_deduped() -> None:
    existing = _note(title="BIRD accuracy is 61%")
    data = {
        "verdicts": [],
        "new_notes": [
            {"kind": "decision", "title": "Pin torch 2.4", "body": "2.5 breaks the eval."},
            {"kind": "not-a-kind", "title": "bad", "body": "dropped"},
            {"kind": "gotcha", "title": "", "body": "no title → dropped"},
            {"kind": "benchmark", "title": "bird ACCURACY is 61%",  # casefold dup → supports
             "body": "duplicate"},
            {"kind": "gotcha", "title": "Harness caches stale preds", "body": "rm the cache."},
            {"kind": "gotcha", "title": "One too many", "body": "over the cap"},
        ],
    }
    plan = apply_verdicts(_comp(), [existing], data, org_id="o1", now=_NOW)
    # Cap is 3 CONSIDERED items: decision + invalid + empty-title → only the
    # decision lands as a note; the dup lands as supports on the existing note.
    assert plan.new_notes == 1
    created = [n for n in plan.upserts if n.id != existing.id and n.title == "Pin torch 2.4"]
    assert len(created) == 1
    note = created[0]
    assert note.status == NoteStatus.candidate and note.source == NoteSource.ai
    assert note.evidence == ["c1"] and note.actors == ["suraj@x.com"]
    assert note.scope == "project:bench" and note.entities.projects == ["bench"]


def test_unknown_note_id_and_malformed_verdicts_dropped() -> None:
    plan = apply_verdicts(
        _comp(), [_note()],
        {"verdicts": [{"note_id": "kn-ghost", "relation": "supports"}, "junk",
                      {"note_id": "kn-1", "relation": "unrelated"}],
         "new_notes": "not-a-list"},
        org_id="o1", now=_NOW,
    )
    assert not plan.upserts and not plan.supersedes


# ── the pass (store + provider integration) ──────────────────────────────
def _good_response(new_title: str = "Pin torch 2.4") -> str:
    return json.dumps(
        {"verdicts": [],
         "new_notes": [{"kind": "decision", "title": new_title, "body": "2.5 breaks the eval."}]}
    )


def test_consolidate_org_creates_notes_and_marks_done() -> None:
    store = _store(_comp("c1"))
    stats = consolidate_org(
        store, ScriptedProvider([_good_response()]), _config(),
        org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
    )
    assert stats.consolidated == 1 and stats.new_notes == 1
    notes = store.query_notes("o1")
    assert len(notes) == 1 and notes[0].title == "Pin torch 2.4"
    assert store.list_unconsolidated("o1") == []  # done-row written


def test_malformed_json_records_failure_writes_nothing() -> None:
    store = _store(_comp("c1"))
    stats = consolidate_org(
        store, ScriptedProvider(["I could not produce JSON, sorry"]), _config(),
        org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
    )
    assert stats.failed == 1 and stats.consolidated == 0
    assert store.query_notes("o1") == []
    assert [c.id for c in store.list_unconsolidated("o1")] == ["c1"]  # still eligible


def test_attempts_exhausted_becomes_abandoned() -> None:
    store = _store(_comp("c1"))
    config = _config(consolidate_max_attempts=2)
    for _ in range(2):
        consolidate_org(
            store, ScriptedProvider(["junk"]), config,
            org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
        )
    stats = consolidate_org(
        store, ScriptedProvider([_good_response()]), config,
        org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
    )
    assert stats.abandoned == 1 and stats.consolidated == 0
    assert store.list_unconsolidated("o1") == []  # terminal — never retried


def test_quota_defers_org_cleanly() -> None:
    store = _store(_comp("c1"))
    # Spend the whole cap up front; the metered provider must refuse the call.
    store.add_llm_usage("o1", datetime.now(UTC).strftime("%Y-%m"),
                        input_tokens=1, output_tokens=1, est_cost_usd=99.0)
    metered = MeteredProvider(ScriptedProvider([_good_response()]), store, "o1", 5.0)
    stats = consolidate_org(
        store, metered, _config(),
        org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
    )
    assert stats.quota_blocked == 1 and stats.consolidated == 0
    # Nothing recorded against the digest — the next pass retries it.
    assert [c.id for c in store.list_unconsolidated("o1")] == ["c1"]


def test_run_pass_batches_across_orgs() -> None:
    store = ServerStore.open("sqlite://")
    for org in ("o1", "o2"):
        store.create_org(org, org)
        store.ingest_compaction(_comp(f"{org}-c1"), org_id=org, team_id="t1")
    provider_for = consolidate_provider_for(
        store, _config(), ScriptedProvider([_good_response("A"), _good_response("B")])
    )
    stats = run_consolidation_pass(
        store, _config(), provider_for, embedder=HashingEmbedder(), now=_NOW
    )
    assert stats.consolidated == 2 and stats.orgs == ["o1", "o2"]
    assert len(store.query_notes("o1")) == 1 and len(store.query_notes("o2")) == 1


def test_retrieval_unions_semantic_and_entity_overlap() -> None:
    store = _store()
    related = _note("kn-1")  # same project + file as the session
    unrelated = KnowledgeNote(
        id="kn-2", org_id="o1", kind=NoteKind.convention, title="Ruff line length is 100",
        body="Keep lines under 100 chars.", scope="project:styleguide",
        entities=NoteEntities(projects=["styleguide"]),
        created_at=_T0, updated_at=_T0,
    )
    store.upsert_note(related)
    store.upsert_note(unrelated)
    got = retrieve_candidates(
        store, _config(), _comp(), org_id="o1", embedder=HashingEmbedder()
    )
    assert "kn-1" in {n.id for n in got}  # entity overlap guarantees inclusion


# ── project_overview is not the adjudicator's to create ──────────────────
def test_adjudicator_cannot_create_a_project_overview() -> None:
    """`_new_note` builds NoteKind straight from model output, so the moment the
    member exists a hallucinating adjudicator can emit it. The prompt has never
    been able to stop that — ADJUDICABLE_KINDS is the actual gate."""
    from manthana.server.consolidate import apply_verdicts

    payload = {
        "verdicts": [],
        "new_notes": [
            {"kind": "project_overview", "title": "scribe", "body": "A transcription service."}
        ],
    }
    plan = apply_verdicts(_comp("c1"), [], payload, org_id="o1", now=_NOW)
    assert plan.new_notes == 0 and plan.upserts == []


def test_adjudication_prompt_offers_only_adjudicable_kinds() -> None:
    from manthana.server.consolidate import ADJUDICABLE_KINDS, build_adjudication_prompt

    prompt = build_adjudication_prompt(_comp("c1"), [])
    assert "project_overview" not in prompt
    for kind in ADJUDICABLE_KINDS:
        assert str(kind) in prompt
