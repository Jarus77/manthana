"""Teaching the wiki — human writes and the authority they carry.

The load-bearing property across this file: a human write produces a
``source="human"`` version that the AI consolidator cannot supersede (only
dispute), which is what makes "correct it once and it sticks for everyone" true
rather than aspirational. History is append-only throughout — including revert.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
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
from manthana.server import ServerConfig, ServerStore
from manthana.server.ask import ask
from manthana.server.consolidate import consolidate_org
from manthana.server.llm import ScriptedProvider
from manthana.server.teach import (
    HUMAN_CONFIDENCE,
    NoteNotFoundError,
    confirm,
    create,
    edit,
    revert,
)
from manthana.skills.embed import HashingEmbedder

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


def _note(
    nid: str = "kn-1",
    *,
    title: str = "BIRD accuracy is 61%",
    body: str = "Measured on the dev split.",
    source: NoteSource = NoteSource.ai,
    status: NoteStatus = NoteStatus.candidate,
    **kw: object,
) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=NoteKind.benchmark,
        title=title,
        body=body,
        scope="project:bench",
        entities=NoteEntities(projects=["bench"]),
        evidence=["c1"],
        actors=["suraj@x.com"],
        source=source,
        status=status,
        created_at=_T0,
        updated_at=_T0,
        **kw,  # type: ignore[arg-type]
    )


def _comp(cid: str = "c1") -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="suraj@x.com",
        surface=Surface.claude_code,
        project="bench",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=60.0,
        task_intent="run the BIRD benchmark",
        approach="swept temperature",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


# ── edit ─────────────────────────────────────────────────────────────────
def test_edit_appends_authoritative_version() -> None:
    store = _store()
    store.upsert_note(_note("kn-1"))
    new = edit(
        store, "o1", "kn-1", title="BIRD accuracy is 64%",
        body="Re-measured after the harness fix.", author="founder", now=_NOW,
    )
    assert new.id != "kn-1" and new.version == 2 and new.supersedes == "kn-1"
    assert new.source == NoteSource.human and new.author == "founder"
    assert new.status == NoteStatus.established and new.confidence == HUMAN_CONFIDENCE
    assert new.evidence == ["c1"]  # provenance carries forward
    # Append-only: the old version survives, marked superseded.
    old = store.get_note("kn-1", "o1")
    assert old is not None and old.status == NoteStatus.superseded
    assert old.body == "Measured on the dev split."
    assert [n.id for n in store.query_notes("o1")] == [new.id]


def test_edit_resolves_a_dispute() -> None:
    # The founder has seen the conflicting evidence and made a call — the new
    # version shouldn't inherit a red badge it no longer deserves.
    store = _store()
    store.upsert_note(
        _note("kn-1", status=NoteStatus.disputed, disputed_by=["c2", "c3"])
    )
    new = edit(store, "o1", "kn-1", title="t", body="settled", author="founder", now=_NOW)
    assert new.status == NoteStatus.established and new.disputed_by == []


def test_edit_missing_note_raises() -> None:
    store = _store()
    with pytest.raises(NoteNotFoundError):
        edit(store, "o1", "kn-ghost", title="t", body="b", author="founder")


# ── create ───────────────────────────────────────────────────────────────
def test_create_captures_knowledge_with_no_session_behind_it() -> None:
    store = _store()
    note = create(
        store, "o1", kind=NoteKind.convention, title="We deploy on Tuesdays",
        body="Never on Friday.", author="founder", project="infra", now=_NOW,
    )
    assert note.source == NoteSource.human and note.status == NoteStatus.established
    assert note.evidence == []  # authority is the author's, not a session's
    assert note.scope == "project:infra" and note.entities.projects == ["infra"]
    assert store.get_note(note.id, "o1") == note


# ── confirm ──────────────────────────────────────────────────────────────
def test_confirm_endorses_without_creating_a_version() -> None:
    store = _store()
    store.upsert_note(_note("kn-1"))
    confirmed = confirm(store, "o1", "kn-1", author="founder", now=_NOW)
    assert confirmed.id == "kn-1" and confirmed.version == 1  # same row
    assert confirmed.status == NoteStatus.established
    assert confirmed.confirmed_by == "founder"
    assert confirmed.source == NoteSource.ai  # who WROTE it is unchanged
    assert len(store.note_history("kn-1", "o1")) == 1  # no history noise


# ── revert ───────────────────────────────────────────────────────────────
def test_revert_is_append_only_and_restores_text() -> None:
    store = _store()
    store.upsert_note(_note("kn-1", body="original text"))
    bad = edit(store, "o1", "kn-1", title="t", body="a bad edit", author="ai-ish", now=_NOW)
    restored = revert(
        store, "o1", bad.id, to_version_id="kn-1", author="founder",
        now=_NOW + timedelta(days=1),
    )
    assert restored.body == "original text"
    assert restored.version == 3  # a NEW version, not a rewind
    assert restored.supersedes == bad.id
    assert restored.source == NoteSource.human
    # All three versions remain on the record.
    assert [n.version for n in store.note_history(restored.id, "o1")] == [3, 2, 1]
    assert [n.id for n in store.query_notes("o1")] == [restored.id]


def test_revert_to_unrelated_version_raises() -> None:
    store = _store()
    store.upsert_note(_note("kn-1"))
    store.upsert_note(_note("kn-other", title="Unrelated"))
    with pytest.raises(NoteNotFoundError):
        revert(store, "o1", "kn-1", to_version_id="kn-other", author="founder")


# ── the authority contract (teach ↔ consolidate ↔ ask) ───────────────────
def test_ai_cannot_supersede_a_taught_note_only_dispute_it() -> None:
    # End-to-end version of the one law: after a human edit, a consolidation pass
    # that tries to rewrite the claim must leave the text alone.
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1"))
    taught = edit(
        store, "o1", "kn-1", title="BIRD accuracy is 64%",
        body="Re-measured after the harness fix.", author="founder", now=_NOW,
    )
    response = json.dumps(
        {"verdicts": [{"note_id": taught.id, "relation": "refines",
                       "updated_body": "Actually it is 61%."}],
         "new_notes": []}
    )
    consolidate_org(
        store, ScriptedProvider([response]), _config(),
        org_id="o1", limit=5, embedder=HashingEmbedder(), now=_NOW,
    )
    after = store.get_note(taught.id, "o1")
    assert after is not None
    assert after.body == "Re-measured after the harness fix."  # untouched
    assert after.status == NoteStatus.disputed  # downgraded to a dispute
    assert after.disputed_by == ["c1"]
    assert after.source == NoteSource.human


def test_correction_propagates_into_later_answers() -> None:
    # The payoff: teach once, and the next question sees the corrected claim as
    # AUTHORITATIVE — with the superseded text gone from the prompt entirely.
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1", title="BIRD accuracy is 61%", body="61% on dev."))
    edit(
        store, "o1", "kn-1", title="BIRD accuracy is 64%",
        body="64% after the harness fix.", author="founder", now=_NOW,
    )
    provider = ScriptedProvider([json.dumps({}), "BIRD is 64%."])
    ask(
        store, _config(), org_id="o1", query="what is BIRD accuracy?",
        provider=provider, embedder=HashingEmbedder(), now=_NOW,
    )
    prompt = provider.calls[-1]
    assert "64% after the harness fix." in prompt
    assert "61% on dev." not in prompt  # the superseded version is not consulted
    assert "AUTHORITATIVE" in prompt
