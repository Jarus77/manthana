"""KnowledgeNote persistence — the org-wiki substrate.

Covers the append-only version model (supersede is transactional; history walks
the chain), org isolation, the note-vector cache, consolidation bookkeeping
(inverted done-row marker), and the purge extension (evidence stripping; AI
notes with no evidence left go stale, human notes keep standing).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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
from manthana.server import ServerStore
from manthana.server.vectors import ensure_note_vectors
from manthana.skills.embed import HashingEmbedder

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Org One")
    store.create_org("o2", "Org Two")
    return store


def _note(
    nid: str = "kn-1",
    *,
    org_id: str = "o1",
    kind: NoteKind = NoteKind.decision,
    title: str = "Use sqlite-vec for local search",
    body: str = "Chosen over faiss for zero native deps.",
    project: str = "search",
    status: NoteStatus = NoteStatus.candidate,
    source: NoteSource = NoteSource.ai,
    evidence: list[str] | None = None,
    at: datetime = _T0,
    **kw: object,
) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id=org_id,
        kind=kind,
        title=title,
        body=body,
        scope=f"project:{project}" if project else "org",
        entities=NoteEntities(projects=[project] if project else []),
        evidence=evidence if evidence is not None else ["c1"],
        source=source,
        status=status,
        created_at=at,
        updated_at=at,
        **kw,  # type: ignore[arg-type]
    )


def _comp(cid: str = "c1", *, source: str = "full", at: datetime = _T0) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="e@x.com",
        surface=Surface.claude_code,
        project="search",
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent="intent",
        approach="approach",
        outcome=Outcome.success,
        released=True,
        source=source,  # type: ignore[arg-type]
    )


# ── CRUD + org isolation ─────────────────────────────────────────────────
def test_upsert_and_get_roundtrip() -> None:
    store = _store()
    note = _note()
    store.upsert_note(note)
    got = store.get_note("kn-1", "o1")
    assert got == note


def test_org_isolation() -> None:
    # A note from org A must be invisible to org B — reads are org-scoped and
    # the PK is org-namespaced, so ids can't collide either.
    store = _store()
    store.upsert_note(_note("kn-1", org_id="o1"))
    assert store.get_note("kn-1", "o2") is None
    assert store.query_notes("o2") == []
    store.upsert_note(_note("kn-1", org_id="o2", title="other org's kn-1"))
    assert store.get_note("kn-1", "o1").title == "Use sqlite-vec for local search"  # type: ignore[union-attr]


def test_query_notes_filters() -> None:
    store = _store()
    store.upsert_note(_note("kn-1", kind=NoteKind.decision, project="search", at=_T0))
    store.upsert_note(
        _note("kn-2", kind=NoteKind.gotcha, project="infra", at=_T0 + timedelta(days=1))
    )
    store.upsert_note(
        _note("kn-3", kind=NoteKind.decision, project="search", at=_T0 + timedelta(days=2))
    )
    assert [n.id for n in store.query_notes("o1")] == ["kn-3", "kn-2", "kn-1"]  # newest first
    assert [n.id for n in store.query_notes("o1", kind="decision")] == ["kn-3", "kn-1"]
    # Project match is case-insensitive like query_compactions.
    assert [n.id for n in store.query_notes("o1", project="SEARCH")] == ["kn-3", "kn-1"]
    assert [n.id for n in store.query_notes("o1", since="2026-01-02")] == ["kn-3", "kn-2"]
    assert len(store.query_notes("o1", limit=1)) == 1


# ── versioning (append-only; the teaching substrate) ─────────────────────
def test_supersede_marks_old_and_links_chain() -> None:
    store = _store()
    v1 = _note("kn-1")
    store.upsert_note(v1)
    v2 = _note("kn-2", body="Actually faiss won.", at=_T0 + timedelta(days=1)).model_copy(
        update={"version": 2, "supersedes": "kn-1"}
    )
    store.supersede_note("kn-1", v2, "o1")

    old = store.get_note("kn-1", "o1")
    assert old is not None
    assert old.status == NoteStatus.superseded
    assert old.superseded_by == "kn-2"
    # Live queries only see the current version; the old row still exists.
    assert [n.id for n in store.query_notes("o1")] == ["kn-2"]
    assert [n.id for n in store.query_notes("o1", exclude_superseded=False)] == ["kn-2", "kn-1"]


def test_supersede_missing_old_raises_and_writes_nothing() -> None:
    store = _store()
    v2 = _note("kn-2").model_copy(update={"version": 2, "supersedes": "kn-ghost"})
    with pytest.raises(ValueError):
        store.supersede_note("kn-ghost", v2, "o1")
    assert store.get_note("kn-2", "o1") is None  # transactional: no orphan new version


def test_note_history_walks_chain_newest_first() -> None:
    store = _store()
    store.upsert_note(_note("kn-1"))
    v2 = _note("kn-2", at=_T0 + timedelta(days=1)).model_copy(
        update={"version": 2, "supersedes": "kn-1"}
    )
    store.supersede_note("kn-1", v2, "o1")
    v3 = _note("kn-3", at=_T0 + timedelta(days=2)).model_copy(
        update={"version": 3, "supersedes": "kn-2"}
    )
    store.supersede_note("kn-2", v3, "o1")
    assert [n.id for n in store.note_history("kn-3", "o1")] == ["kn-3", "kn-2", "kn-1"]


# ── note vectors ─────────────────────────────────────────────────────────
class _CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return super().embed(texts)


def test_ensure_note_vectors_embeds_once() -> None:
    store = _store()
    notes = [_note("kn-1"), _note("kn-2", title="Second", body="note")]
    for n in notes:
        store.upsert_note(n)
    emb = _CountingEmbedder()
    vecs = ensure_note_vectors(store, "o1", notes, emb)
    assert set(vecs) == {"kn-1", "kn-2"}
    assert len(emb.calls) == 1 and len(emb.calls[0]) == 2
    # Second call: everything cached, nothing re-embedded.
    vecs2 = ensure_note_vectors(store, "o1", notes, emb)
    assert set(vecs2) == {"kn-1", "kn-2"}
    assert len(emb.calls) == 1
    # A changed body re-embeds just that note (text_hash staleness).
    changed = notes[0].model_copy(update={"body": "new body"})
    ensure_note_vectors(store, "o1", [changed, notes[1]], emb)
    assert len(emb.calls) == 2 and len(emb.calls[1]) == 1


# ── consolidation bookkeeping ────────────────────────────────────────────
def test_list_unconsolidated_filters_and_orders() -> None:
    store = _store()
    store.ingest_compaction(_comp("c-pending", source="pending"), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("c-old", at=_T0), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("c-new", at=_T0 + timedelta(days=1)), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("c-done", at=_T0 + timedelta(days=2)), org_id="o1", team_id="t1")
    store.mark_consolidated("o1", "c-done")

    got = [c.id for c in store.list_unconsolidated("o1")]
    assert got == ["c-new", "c-old"]  # pending + done excluded, newest first

    # failed stays eligible (the pass bounds retries); abandoned is terminal.
    store.record_consolidation_failure("o1", "c-new", detail="boom")
    assert "c-new" in [c.id for c in store.list_unconsolidated("o1")]
    store.mark_consolidation_abandoned("o1", "c-new", detail="gave up")
    assert [c.id for c in store.list_unconsolidated("o1")] == ["c-old"]

    assert store.count_unconsolidated("o1") == 1
    assert store.orgs_with_unconsolidated() == ["o1"]


def test_consolidation_failure_attempts_increment() -> None:
    store = _store()
    assert store.record_consolidation_failure("o1", "c1", detail="a") == 1
    assert store.record_consolidation_failure("o1", "c1", detail="b") == 2
    rows = store.list_consolidation_state("o1", state="failed")
    assert len(rows) == 1 and rows[0].attempts == 2 and rows[0].detail == "b"


# ── purge extension ──────────────────────────────────────────────────────
def test_purge_strips_evidence_and_stales_ai_notes() -> None:
    store = _store()
    store.ingest_compaction(_comp("c1"), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("c2"), org_id="o1", team_id="t1")
    store.mark_consolidated("o1", "c1")
    store.upsert_note(_note("kn-ai", evidence=["c1", "c2"]))
    store.upsert_note(
        _note("kn-human", source=NoteSource.human, status=NoteStatus.established, evidence=["c1"])
    )

    store.delete_compactions("o1", ["c1"])
    ai = store.get_note("kn-ai", "o1")
    assert ai is not None and ai.evidence == ["c2"] and ai.status != NoteStatus.stale
    # Bookkeeping for the purged id is gone too.
    assert "c1" not in store.consolidation_meta("o1")

    store.delete_compactions("o1", ["c2"])
    ai = store.get_note("kn-ai", "o1")
    assert ai is not None and ai.evidence == [] and ai.status == NoteStatus.stale
    # The human note lost its citation but keeps standing on the author's authority.
    human = store.get_note("kn-human", "o1")
    assert human is not None and human.evidence == [] and human.status == NoteStatus.established
