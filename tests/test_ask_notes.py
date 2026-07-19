"""Notes-first founder Q&A (`ask.py`).

Covers the three properties that make the answers trustworthy: notes carry the
common questions (with sessions drilled only when they're thin), human notes
outrank AI notes so a correction sticks, and an uncited narrative is withheld.
Freshness questions must reach live activity rather than a note.

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
from manthana.server.ask import ask
from manthana.server.founder import INSUFFICIENT
from manthana.server.llm import ScriptedProvider
from manthana.skills.embed import HashingEmbedder

_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_EMPTY_FILTER = json.dumps({})


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


def _comp(
    cid: str,
    *,
    actor: str = "suraj@x.com",
    project: str = "bench",
    intent: str = "run the BIRD benchmark",
    days_ago: int = 1,
) -> EngineeringCompaction:
    at = _NOW - timedelta(days=days_ago)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent=intent,
        approach="swept temperature",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def _note(
    nid: str,
    *,
    title: str = "BIRD accuracy is 61%",
    body: str = "The BIRD benchmark reached 61% execution accuracy.",
    kind: NoteKind = NoteKind.benchmark,
    project: str = "bench",
    source: NoteSource = NoteSource.ai,
    status: NoteStatus = NoteStatus.candidate,
    actors: list[str] | None = None,
    **kw: object,
) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=kind,
        title=title,
        body=body,
        scope=f"project:{project}",
        entities=NoteEntities(projects=[project]),
        actors=actors if actors is not None else ["suraj@x.com"],
        evidence=["c1"],
        source=source,
        status=status,
        created_at=_NOW - timedelta(days=2),
        updated_at=_NOW - timedelta(days=2),
        **kw,  # type: ignore[arg-type]
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


def _ask(store: ServerStore, query: str, responses: list[str], **kw: object):
    return ask(
        store, _config(), org_id="o1", query=query,
        provider=ScriptedProvider(responses), embedder=HashingEmbedder(), now=_NOW,
        **kw,  # type: ignore[arg-type]
    )


# ── notes-first ──────────────────────────────────────────────────────────
def test_answers_from_notes_and_cites_note_ids() -> None:
    store = _store(_comp("c1"))
    for i in range(3):  # enough relevant notes → no session drill
        store.upsert_note(_note(f"kn-{i}", title=f"BIRD accuracy note {i}"))
    result = _ask(
        store, "what is the BIRD benchmark accuracy?",
        [_EMPTY_FILTER, "BIRD reached 61% [kn-0]."],
    )
    assert not result.insufficient_data
    assert result.note_citations == ["kn-0"]
    assert result.compaction_citations == []
    assert "note(s)" in result.coverage_note()


def test_thin_notes_drill_to_sessions() -> None:
    store = _store(_comp("c1"), _comp("c2", intent="fix the retry loop"))
    # No notes at all → the wiki can't cover it, so sessions must be read.
    result = _ask(
        store, "what went wrong with retries?",
        [_EMPTY_FILTER, "A retry loop was fixed [c2]."],
    )
    assert result.drilled is True
    assert result.compaction_citations == ["c2"]
    assert result.sessions_used > 0
    assert "session(s)" in result.coverage_note()


def test_sufficient_notes_skip_the_drill() -> None:
    store = _store(_comp("c1"))
    for i in range(3):
        store.upsert_note(_note(f"kn-{i}", title=f"BIRD accuracy note {i}"))
    result = _ask(store, "BIRD accuracy", [_EMPTY_FILTER, "61% [kn-0]."])
    assert result.drilled is False and result.sessions_used == 0


# ── human authority ──────────────────────────────────────────────────────
def test_human_notes_are_listed_first_and_marked_authoritative() -> None:
    # The teaching mechanic: a founder's correction must reach the model as the
    # authority, ahead of the AI note it contradicts.
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-ai", title="BIRD accuracy is 61%"))
    store.upsert_note(
        _note("kn-human", title="BIRD accuracy is 64%", source=NoteSource.human,
              status=NoteStatus.established, author="founder")
    )
    provider = ScriptedProvider([_EMPTY_FILTER, "BIRD is 64% [kn-human]."])
    ask(
        store, _config(), org_id="o1", query="what is BIRD accuracy?",
        provider=provider, embedder=HashingEmbedder(), now=_NOW,
    )
    prompt = provider.calls[-1]
    assert "AUTHORITATIVE" in prompt
    # The human note appears before the AI one in the NOTES payload.
    assert prompt.index("kn-human") < prompt.index("kn-ai")


def test_disputed_notes_are_flagged_to_the_model() -> None:
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-d", status=NoteStatus.disputed, disputed_by=["c1"]))
    provider = ScriptedProvider([_EMPTY_FILTER, "Reportedly 61% [kn-d]."])
    ask(
        store, _config(), org_id="o1", query="BIRD accuracy?",
        provider=provider, embedder=HashingEmbedder(), now=_NOW,
    )
    assert "DISPUTED" in provider.calls[-1]


# ── freshness comes from live activity, never a note ─────────────────────
def test_freshness_question_uses_live_activity() -> None:
    store = _store(
        _comp("c1", intent="run the BIRD benchmark", days_ago=1),
        _comp("c2", intent="tune the reranker", days_ago=3),
    )
    provider = ScriptedProvider(
        [json.dumps({"actor": "suraj"}), "Suraj is on the BIRD benchmark [c1]."]
    )
    store.upsert_actor("suraj@x.com", "o1", "t1")
    result = ask(
        store, _config(), org_id="o1", query="what is Suraj working on right now?",
        provider=provider, embedder=HashingEmbedder(), now=_NOW,
    )
    prompt = provider.calls[-1]
    assert "CURRENT ACTIVITY" in prompt
    assert "run the BIRD benchmark" in prompt  # live intents reached the model
    assert not result.insufficient_data


# ── grounding is non-optional ────────────────────────────────────────────
def test_uncited_narrative_is_withheld() -> None:
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1"))
    result = _ask(
        store, "what is BIRD accuracy?",
        [_EMPTY_FILTER, "I believe it is around 61 percent."],  # no [citation]
    )
    assert result.insufficient_data and result.narrative == INSUFFICIENT
    assert result.citations == []


def test_empty_org_is_insufficient_without_a_model_call() -> None:
    store = _store()
    provider = ScriptedProvider([_EMPTY_FILTER])
    result = ask(
        store, _config(), org_id="o1", query="anything?",
        provider=provider, embedder=HashingEmbedder(), now=_NOW,
    )
    assert result.insufficient_data
    assert len(provider.calls) == 1  # only the filter parse — no narrative burned


def test_provider_failure_withholds_rather_than_guessing() -> None:
    class _Boom:
        name = "boom"
        calls = 0

        def complete(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return _EMPTY_FILTER
            raise RuntimeError("upstream down")

    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1"))
    result = ask(
        store, _config(), org_id="o1", query="BIRD accuracy?",
        provider=_Boom(), embedder=HashingEmbedder(), now=_NOW,
    )
    assert result.insufficient_data and result.narrative == INSUFFICIENT


def test_citations_split_notes_from_sessions() -> None:
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1"))
    result = _ask(
        store, "BIRD accuracy and what happened?",
        [_EMPTY_FILTER, "61% [kn-1] measured in a session [c1]."],
    )
    assert result.note_citations == ["kn-1"]
    assert result.compaction_citations == ["c1"]
    assert result.citations == ["kn-1", "c1"]
