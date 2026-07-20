"""Founder Q&A over the org wiki — notes first, sessions on demand.

The difference from ``founder.py`` (which stays as the k-anonymized legacy path)
is what the model is given to reason over. Here the primary context is
consolidated KnowledgeNotes — durable claims that already carry their evidence —
so the common questions are answered from a handful of short notes rather than
forty raw digests. Sessions are drilled into only when the notes are too thin to
carry the question.

Three rules make the answers trustworthy:

  * **Human notes outrank AI notes.** They are listed first and tagged
    AUTHORITATIVE, so a founder's correction propagates into every later answer
    — the "correct it once, it sticks for everyone" mechanic.
  * **Freshness never comes from notes.** "What is X working on" is served from
    a live rollup over recent compactions, because a persisted answer to that
    question is stale the moment it's written.
  * **Grounding is non-optional.** Every claim must cite a note or compaction id;
    an uncited narrative is withheld, exactly as in the legacy path.

No k-anonymity is applied (consented-startup segment; see ``pages.py``).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from manthana.schemas import KnowledgeNote, NoteSource
from manthana.skills.embed import Embedder, default_embedder
from manthana.skills.projections import activity_rollup
from manthana.skills.retrieval import Coverage, rank, rank_scored

from .founder import (
    INSUFFICIENT,
    FounderFilter,
    _match_citations,
    _resolve_actor,
    _resolve_project,
    parse_filter,
)
from .metering import QuotaExceededError
from .vectors import ensure_note_vectors, ensure_vectors

if TYPE_CHECKING:
    from .config import ServerConfig
    from .llm import LLMProvider
    from .store import ServerStore

_log = logging.getLogger(__name__)

#: How many notes the narrative sees, and the cosine floor for "this note is
#: actually about the question".
NOTES_K = 12
NOTE_MIN_SCORE = 0.3
#: Below this many relevant notes the question is not yet covered by the wiki, so
#: we fall back to reading sessions.
NOTES_SUFFICIENT = 3
#: Session drill is deliberately smaller than the legacy path's 40 — notes carry
#: most of the load, and a shorter prompt is a cheaper, sharper answer.
DRILL_K = 20
#: Window for the live activity rollup, now always consulted.
ACTIVITY_DAYS = 14
#: Sessions pulled in per explicitly-named person, on top of the semantic drill.
PER_ACTOR_DRILL = 8


def _named_actors(store: ServerStore, org_id: str, query: str) -> list[str]:
    """Known actors whose name appears in the query, by local-part or full id.

    This is what makes a question ABOUT PEOPLE reach data about people. The
    filter parser can only carry ONE actor, so it deliberately drops the filter
    when a question names two — which is exactly the "how do A and B compare"
    shape a founder asks most. Matching here is independent of that filter: it
    does not narrow the query, it only proves people were mentioned, so the
    answer can consult live activity and sessions instead of notes alone.
    """
    lowered = query.lower()
    hits = []
    for row in store.list_actors(org_id):
        local = row.id.split("@")[0].lower()
        # Word-boundary match so "sam" does not fire on "sample".
        if re.search(rf"\b{re.escape(local)}\b", lowered) or row.id.lower() in lowered:
            hits.append(row.id)
    return hits


_PROMPT = (
    "Answer the founder's QUESTION in 2-5 sentences using ONLY the data below.\n"
    "Cite the id in [square brackets] for EVERY claim — note ids look like "
    "[kn-1a2b3c], session ids are the compaction ids. Do not invent facts; omit "
    "anything the data does not support.\n"
    "NOTES are the team's consolidated knowledge. Notes marked AUTHORITATIVE were "
    "written or confirmed by a human: when an AI-written note conflicts with one, "
    "the AUTHORITATIVE note is correct and the other must be ignored. A note marked "
    "DISPUTED has conflicting evidence — say so rather than stating it as fact.\n"
    "CURRENT ACTIVITY is live from recent sessions and is the authority on what "
    "people are working on right now.\n"
    "Never claim a person, project or topic is ABSENT from the wiki, or that "
    "there is 'no data' on it. You are shown a RETRIEVED SUBSET, not the whole "
    "wiki, so absence here is not evidence of absence. If the data below does "
    "not answer the question, say only that what you were given does not cover "
    "it, and name what you did find.\n"
    "({coverage}.)\n"
    "QUESTION: {query}\n"
    "NOTES:\n{notes}\n"
    "CURRENT ACTIVITY:\n{activity}\n"
    "SESSIONS:\n{sessions}\n"
)


@dataclass
class AskResult:
    filter: FounderFilter
    narrative: str
    note_citations: list[str] = field(default_factory=list)
    compaction_citations: list[str] = field(default_factory=list)
    notes_used: int = 0
    sessions_used: int = 0
    drilled: bool = False  # notes were thin, so sessions were read too
    insufficient_data: bool = False
    coverage: Coverage | None = None

    @property
    def citations(self) -> list[str]:
        return [*self.note_citations, *self.compaction_citations]

    def coverage_note(self) -> str:
        parts = [f"answered from {self.notes_used} note(s)"]
        if self.drilled:
            parts.append(f"{self.sessions_used} session(s)")
        return " and ".join(parts)


def _rank_notes(
    store: ServerStore, org_id: str, query: str, notes: list[KnowledgeNote], embedder: Embedder
) -> list[tuple[float, KnowledgeNote]]:
    if not notes:
        return []
    try:
        vectors = ensure_note_vectors(store, org_id, notes, embedder)
        ranked, _cov = rank_scored(query, notes, vectors, embedder, k=NOTES_K)
        return [(score, n) for score, n in ranked]  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - embedder failure degrades to unranked, never 500s
        _log.exception("ask: note retrieval failed, returning unranked")
        return [(0.0, n) for n in notes[:NOTES_K]]


def _note_brief(note: KnowledgeNote) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "id": note.id,
        "kind": str(note.kind),
        "title": note.title,
        "body": note.body,
        "scope": note.scope,
        "people": note.actors,
        "sessions": note.evidence[:5],
    }
    if note.metric and note.value:
        brief["metric"] = f"{note.metric}={note.value}"
    if note.source == NoteSource.human or note.confirmed_by:
        brief["AUTHORITATIVE"] = True
    if note.disputed_by:
        brief["DISPUTED"] = True
    return brief


def _session_brief(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "engineer": c.actor,
        "project": c.project,
        "intent": c.task_intent,
        "approach": c.approach,
        "outcome": str(c.outcome),
        "friction": [
            {"category": str(fp.category), "issue": fp.description}
            for fp in (getattr(c, "friction_points", None) or [])
        ],
    }


def ask(
    store: ServerStore,
    config: ServerConfig,
    *,
    org_id: str,
    query: str,
    provider: LLMProvider,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> AskResult:
    """Answer a founder question from the wiki, drilling to sessions if needed."""
    now = now or datetime.now(UTC)
    embedder = embedder or default_embedder()

    spec = parse_filter(query, provider, now=now)
    spec.project = _resolve_project(store, org_id, spec.project)
    if spec.actor:
        # Keep the actor filter only when it names a real person; an unresolved
        # name (or a comparison of two people) drops it so the answer can still
        # span everyone rather than matching nobody.
        resolved = _resolve_actor(store, org_id, spec.actor)
        known = {a.id for a in store.list_actors(org_id)}
        spec.actor = resolved if resolved in known else None

    # ── notes first ──────────────────────────────────────────────────────
    pool = store.query_notes(org_id, project=spec.project, limit=config.consolidate_note_scan)
    ranked = _rank_notes(store, org_id, query, pool, embedder)
    relevant = [n for score, n in ranked if score >= NOTE_MIN_SCORE]
    # Human-authored notes lead the prompt — the model reads them as the
    # authority, which is what makes a founder's correction stick.
    notes = sorted(
        relevant or [n for _s, n in ranked],
        key=lambda n: (n.source != NoteSource.human and not n.confirmed_by),
    )

    # ── live activity ────────────────────────────────────────────────────
    # ALWAYS fetched. This used to be gated on a freshness regex, which made the
    # commonest founder question — "how do X and Y compare" — structurally blind:
    # naming two people drops the actor filter, the phrasing matches no freshness
    # word, so live activity was never read, and anyone whose work had not yet
    # been consolidated into notes did not exist as far as the answer was
    # concerned. The gate saved nothing worth having: this is one indexed query
    # and an in-memory group-by, with no model call.
    since = (now - timedelta(days=ACTIVITY_DAYS)).astimezone(UTC).isoformat()
    recent = store.query_compactions(
        org_id=org_id, actor=spec.actor, project=spec.project, since=since
    )
    activity = [
        {
            "engineer": a.actor,
            "sessions": a.sessions,
            "projects": a.projects,
            "working_on": a.intents,
        }
        for a in activity_rollup(recent)
    ]

    # ── drill to sessions ────────────────────────────────────────────────
    # Thin notes still trigger a drill, but so does naming people: a question
    # about someone is answerable from their sessions even when the wiki has a
    # dozen well-scoring notes about unrelated things.
    mentioned = _named_actors(store, org_id, query)
    drilled = len(relevant) < NOTES_SUFFICIENT or bool(mentioned)
    sessions: list[Any] = []
    coverage: Coverage | None = None
    if drilled:
        candidates = store.query_compactions(
            org_id=org_id,
            team_id=spec.team_id,
            project=spec.project,
            outcome=spec.outcome,
            actor=spec.actor,
            surface=spec.surface,
            since=spec.since,
            until=spec.until,
        )
        # Guarantee every named person is represented. Semantic ranking alone
        # can bury one side of a comparison — the question mentions two people,
        # so an answer that saw sessions from only one of them is worse than
        # useless, because it reads as a finding about the other.
        if mentioned:
            have = {c.id for c in candidates}
            for who in mentioned:
                for c in store.query_compactions(
                    org_id=org_id, actor=who, since=spec.since or since, limit=PER_ACTOR_DRILL
                ):
                    if c.id not in have:
                        have.add(c.id)
                        candidates.append(c)
        try:
            vectors = ensure_vectors(store, org_id, candidates, embedder)
            sessions, coverage = rank(query, candidates, vectors, embedder, k=DRILL_K)
        except Exception:  # noqa: BLE001 - degrade to unranked, never 500
            _log.exception("ask: session retrieval failed, returning unranked")
            sessions = candidates[:DRILL_K]
            coverage = Coverage(matched=len(candidates), used=len(sessions))

    if not notes and not sessions and not activity:
        return AskResult(filter=spec, narrative=INSUFFICIENT, insufficient_data=True)

    result = AskResult(
        filter=spec,
        narrative="",
        notes_used=len(notes),
        sessions_used=len(sessions),
        drilled=drilled,
        coverage=coverage,
    )
    try:
        narrative = provider.complete(
            _PROMPT.format(
                query=query,
                notes=json.dumps([_note_brief(n) for n in notes], indent=None),
                activity=json.dumps(activity) if activity else "(not asked / none)",
                sessions=(
                    json.dumps([_session_brief(c) for c in sessions])
                    if sessions
                    else "(none read — the notes above cover this)"
                ),
                coverage=coverage.note() if coverage else result.coverage_note(),
            )
        ).strip()
    except QuotaExceededError:
        raise  # surface as 429, never as a misleading "insufficient data"
    except Exception:  # noqa: BLE001 - provider failure → withhold, don't guess
        _log.exception("ask: narrative provider call failed")
        result.narrative = INSUFFICIENT
        result.insufficient_data = True
        return result

    # Citations resolve against notes and sessions in one namespace, so the
    # existing exact-or-unique-prefix matcher works unchanged for both.
    cited = _match_citations(narrative, [*notes, *sessions])
    note_ids = {n.id for n in notes}
    result.narrative = narrative
    result.note_citations = [c for c in cited if c in note_ids]
    result.compaction_citations = [c for c in cited if c not in note_ids]
    if not cited:
        # Non-optional grounding: an uncited narrative is withheld.
        result.narrative = INSUFFICIENT
        result.insufficient_data = True
    return result


__all__ = ["AskResult", "ask", "NOTES_K", "NOTES_SUFFICIENT", "DRILL_K"]
