"""Org-wiki page projections — zero-LLM, computed on read.

A wiki page is not a document: it is a COMPILED VIEW over two sources, joined
here and nowhere else.

  * **Live rollups** over recent released compactions (via the shared
    ``skills.projections`` functions the personal wiki also uses) answer the
    freshness questions — who is active, what state a project is in, what
    someone is working on right now. Never persisted, so never stale.
  * **KnowledgeNotes** carry the durable claims — decisions, conventions,
    gotchas, benchmark results — each citing the compactions it came from.

Nothing is cached: at a ten-person startup's scale every page is a handful of
indexed SQLite queries, and the only cache in the system stays the vector cache.
No k-anonymity is applied on this path (the consented-startup segment; the
k-anon pipeline in ``founder.py`` remains for the original contract).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from manthana.schemas import BaseCompaction, KnowledgeNote, NoteKind, NoteStatus
from manthana.skills.projections import (
    ActorActivity,
    ProjectRollup,
    SessionCard,
    activity_rollup,
    project_rollups,
    session_cards,
)

from .purge import is_structural_junk

if TYPE_CHECKING:
    from .store import ServerStore

#: Windows. The feed answers "this week"; a project page shows a fortnight so a
#: slower-moving project still reads as alive.
HOME_WINDOW_DAYS = 7
PROJECT_WINDOW_DAYS = 14

#: Notes are grouped into these sections, in this order, on a project page.
SECTION_ORDER = [
    NoteKind.decision,
    NoteKind.convention,
    NoteKind.gotcha,
    NoteKind.failure_pattern,
    NoteKind.benchmark,
    NoteKind.procedure_ref,
    NoteKind.faq,
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _window(now: datetime | None, days: int) -> tuple[datetime, str]:
    now = now or datetime.now(UTC)
    return now, _iso(now - timedelta(days=days))


def _live_notes(store: ServerStore, org_id: str, **kw: object) -> list[KnowledgeNote]:
    """Current versions only — superseded rows are history, not content."""
    return store.query_notes(org_id, exclude_superseded=True, **kw)  # type: ignore[arg-type]


def _readable(compactions: list[BaseCompaction]) -> list[BaseCompaction]:
    """Drop digests that describe Manthana compacting itself.

    These are real rows — an engineer's tool ran, a digest was produced — but
    they describe the plumbing rather than any work the team did, and on a wiki
    they read as a colleague's project ("Summarize a single engineering session
    into a structured JSON digest"). ``is_structural_junk`` is deliberately
    conservative: it requires no files touched AND no real project AND an
    abandoned outcome AND a compaction-shaped text signal, so a genuine session
    *about* the compactor keeps its files and project and is never caught.

    A DISPLAY filter, not an ingest one. The rows stay in the store, stay
    purgeable through the audited admin path, and stay reachable by id — hiding
    something from a reader is a much weaker act than deleting it, and only the
    weaker act is justified by a heuristic.
    """
    return [c for c in compactions if not is_structural_junk(c)]


@dataclass(frozen=True)
class BenchmarkDelta:
    """A benchmark note plus, when it can be read, what it moved FROM.

    Best-effort by design: the delta needs the superseded predecessor to carry a
    parseable ``value``. When it doesn't, the note still shows — the feed is
    never gated on structured extraction working.
    """

    note: KnowledgeNote
    previous_value: str | None = None

    @property
    def moved(self) -> bool:
        return self.previous_value is not None and self.previous_value != self.note.value


@dataclass(frozen=True)
class HomeFeed:
    """The founder's daily 30-second scan."""

    org_id: str
    since: str
    projects: list[ProjectRollup] = field(default_factory=list)
    new_decisions: list[KnowledgeNote] = field(default_factory=list)
    benchmarks: list[BenchmarkDelta] = field(default_factory=list)
    people: list[ActorActivity] = field(default_factory=list)
    unreviewed: int = 0  # candidate notes awaiting a human look


def _benchmark_deltas(
    store: ServerStore, org_id: str, notes: list[KnowledgeNote]
) -> list[BenchmarkDelta]:
    out: list[BenchmarkDelta] = []
    for note in notes:
        previous: str | None = None
        if note.supersedes:
            prior = store.get_note(note.supersedes, org_id)
            if prior is not None and prior.value:
                previous = prior.value
        out.append(BenchmarkDelta(note=note, previous_value=previous))
    return out


def org_home(
    store: ServerStore, org_id: str, *, now: datetime | None = None, days: int = HOME_WINDOW_DAYS
) -> HomeFeed:
    """This week across the org: project status lines, notable decisions,
    benchmarks that moved, who's active."""
    _now, since = _window(now, days)
    comps = _readable(store.query_compactions(org_id=org_id, since=since))
    fresh = _live_notes(store, org_id, since=since)
    return HomeFeed(
        org_id=org_id,
        since=since,
        projects=project_rollups(comps),
        new_decisions=[n for n in fresh if n.kind == NoteKind.decision],
        benchmarks=_benchmark_deltas(
            store, org_id, [n for n in fresh if n.kind == NoteKind.benchmark]
        ),
        people=activity_rollup(comps),
        unreviewed=len(_live_notes(store, org_id, status=str(NoteStatus.candidate))),
    )


@dataclass(frozen=True)
class DiscoveryFeed:
    """The home page of the shared wiki — built for DISCOVERY, not oversight.

    Two differences from ``HomeFeed``, both deliberate:

      * a ``stream`` of recent sessions, so opening the wiki answers "what is
        everyone actually doing" before you have thought of a question;
      * ``sections`` keyed by note kind, emitted only for kinds that have fresh
        content. ``HomeFeed`` promoted *benchmark* to a top-level field, which
        made one org's use case part of everyone's information architecture —
        here a benchmark is simply the kind that happens to have moved this week.

    Benchmark notes still carry their prev→new delta (via ``benchmarks``, keyed
    by note id) because that IS the readable form of a benchmark; it is a
    rendering hint, not a section.
    """

    org_id: str
    since: str
    stream: list[SessionCard] = field(default_factory=list)
    sections: list[tuple[NoteKind, list[KnowledgeNote]]] = field(default_factory=list)
    projects: list[ProjectRollup] = field(default_factory=list)
    people: list[ActorActivity] = field(default_factory=list)
    benchmarks: dict[str, BenchmarkDelta] = field(default_factory=dict)
    unreviewed: int = 0


def discovery_feed(
    store: ServerStore,
    org_id: str,
    *,
    now: datetime | None = None,
    days: int = HOME_WINDOW_DAYS,
    stream_limit: int = 40,
) -> DiscoveryFeed:
    """This week across the org, as a browsable stream plus whatever knowledge
    kinds actually moved."""
    _now, since = _window(now, days)
    comps = _readable(store.query_compactions(org_id=org_id, since=since, limit=stream_limit))
    windowed = _readable(store.query_compactions(org_id=org_id, since=since))
    fresh = _live_notes(store, org_id, since=since)
    sections = [
        (kind, [n for n in fresh if n.kind == kind])
        for kind in SECTION_ORDER
        if any(n.kind == kind for n in fresh)
    ]
    deltas = _benchmark_deltas(store, org_id, [n for n in fresh if n.kind == NoteKind.benchmark])
    return DiscoveryFeed(
        org_id=org_id,
        since=since,
        stream=session_cards(comps),
        sections=sections,
        projects=project_rollups(windowed),
        people=activity_rollup(windowed),
        benchmarks={d.note.id: d for d in deltas},
        unreviewed=len(_live_notes(store, org_id, status=str(NoteStatus.candidate))),
    )


@dataclass(frozen=True)
class ProjectPage:
    project: str
    rollup: ProjectRollup | None  # None when nothing happened in the window
    sections: list[tuple[NoteKind, list[KnowledgeNote]]] = field(default_factory=list)
    sessions: list[SessionCard] = field(default_factory=list)

    @property
    def note_count(self) -> int:
        return sum(len(notes) for _kind, notes in self.sections)


def project_page(
    store: ServerStore,
    org_id: str,
    project: str,
    *,
    now: datetime | None = None,
    days: int = PROJECT_WINDOW_DAYS,
    session_limit: int = 50,
) -> ProjectPage:
    """State of one project: a live header, its notes grouped by kind, and the
    sessions behind them."""
    _now, since = _window(now, days)
    windowed = _readable(store.query_compactions(org_id=org_id, project=project, since=since))
    recent = _readable(store.query_compactions(org_id=org_id, project=project, limit=session_limit))
    rollups = project_rollups(windowed)

    notes = _live_notes(store, org_id, project=project)
    # Org-scoped notes that name this project apply here too (a convention can be
    # org-wide but still be *about* a project).
    have = {n.id for n in notes}
    notes += [
        n
        for n in _live_notes(store, org_id, scope="org")
        if n.id not in have and project in n.entities.projects
    ]
    sections = [
        (kind, [n for n in notes if n.kind == kind])
        for kind in SECTION_ORDER
        if any(n.kind == kind for n in notes)
    ]
    return ProjectPage(
        project=project,
        rollup=rollups[0] if rollups else None,
        sections=sections,
        sessions=session_cards(recent),
    )


@dataclass(frozen=True)
class PersonPage:
    actor: str
    activity: ActorActivity | None  # None when quiet in the window
    notes: list[KnowledgeNote] = field(default_factory=list)
    sessions: list[SessionCard] = field(default_factory=list)


def person_page(
    store: ServerStore,
    org_id: str,
    actor: str,
    *,
    now: datetime | None = None,
    days: int = PROJECT_WINDOW_DAYS,
    session_limit: int = 50,
) -> PersonPage:
    """What one person is working on (live) and what durable knowledge their work
    produced (notes whose evidence names them).

    Deliberately un-gated: for a consented ~10-person startup the founder's
    flagship question IS person-shaped, and the k-anon floor made it
    unanswerable. Note membership comes from ``actors``, derived at consolidation
    time from the evidence compactions — no entity resolution needed.
    """
    _now, since = _window(now, days)
    windowed = _readable(store.query_compactions(org_id=org_id, actor=actor, since=since))
    recent = _readable(store.query_compactions(org_id=org_id, actor=actor, limit=session_limit))
    acts = activity_rollup(windowed)
    notes = [n for n in _live_notes(store, org_id) if actor in n.actors]
    return PersonPage(
        actor=actor,
        activity=acts[0] if acts else None,
        notes=notes,
        sessions=session_cards(recent),
    )


def note_page(
    store: ServerStore, org_id: str, note_id: str
) -> tuple[KnowledgeNote, list[BaseCompaction], list[BaseCompaction]] | None:
    """One note with its evidence and, when disputed, the conflicting sessions
    resolved to real compactions (purged ids simply drop out)."""
    note = store.get_note(note_id, org_id)
    if note is None:
        return None

    def _resolve(ids: list[str]) -> list[BaseCompaction]:
        found = (store.get_compaction(cid, org_id) for cid in ids)
        return [c for c in found if c is not None]

    return note, _resolve(note.evidence), _resolve(note.disputed_by)


__all__ = [
    "BenchmarkDelta",
    "DiscoveryFeed",
    "HomeFeed",
    "PersonPage",
    "ProjectPage",
    "HOME_WINDOW_DAYS",
    "PROJECT_WINDOW_DAYS",
    "SECTION_ORDER",
    "discovery_feed",
    "note_page",
    "org_home",
    "person_page",
    "project_page",
]
