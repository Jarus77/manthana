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
    project_status,
    session_cards,
)

from .purge import is_structural_junk

if TYPE_CHECKING:
    from .store import ServerStore

#: Windows. The feed answers "this week"; a project page shows a fortnight so a
#: slower-moving project still reads as alive.
HOME_WINDOW_DAYS = 7
PROJECT_WINDOW_DAYS = 14

#: Sessions shown per project block on a person page — a glance, not a log.
PERSON_SESSIONS_PER_PROJECT = 3


def _article_lead(body: str) -> str:
    """The article's "What this is" line: first non-heading, non-empty line."""
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
            return stripped
    return ""


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


def split_summarised(
    cards: list[SessionCard],
) -> tuple[list[SessionCard], list[tuple[str, int]]]:
    """Summarised cards, plus per-project counts of the pending ones.

    A pending digest has no summary — its title is the engineer's raw first
    prompt — so a reader gets nothing from a list of them. They collapse to one
    count per project ("bird-sql: 14 awaiting summary") and the article surfaces
    only work that can actually be read. Server-side, because the projections
    that quote intents are bare strings the client cannot classify.
    """
    summarised = [c for c in cards if c.source != "pending"]
    counts: dict[str, int] = {}
    for c in cards:
        if c.source == "pending":
            counts[c.project or ""] = counts.get(c.project or "", 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return summarised, ordered


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
    #: The last few SUMMARISED sessions — never raw prompts. Capped hard: the
    #: front page is a glance, not an archive.
    stream: list[SessionCard] = field(default_factory=list)
    #: Sessions awaiting summary, collapsed to (project, count). One line each.
    pending_counts: list[tuple[str, int]] = field(default_factory=list)
    projects: list[ProjectRollup] = field(default_factory=list)
    people: list[ActorActivity] = field(default_factory=list)


def discovery_feed(
    store: ServerStore,
    org_id: str,
    *,
    now: datetime | None = None,
    days: int = HOME_WINDOW_DAYS,
    stream_limit: int = 10,
) -> DiscoveryFeed:
    """This week across the org: who is active, what the projects are, and the
    last few sessions a reader can actually read. Note-kind sections are gone —
    the taxonomy is a retrieval substrate now, not reading material."""
    _now, since = _window(now, days)
    windowed = _readable(store.query_compactions(org_id=org_id, since=since))
    summarised, pending = split_summarised(session_cards(windowed))
    return DiscoveryFeed(
        org_id=org_id,
        since=since,
        stream=summarised[:stream_limit],
        pending_counts=pending,
        projects=project_rollups(windowed),
        people=activity_rollup(windowed),
    )


@dataclass(frozen=True)
class ProjectPage:
    """One project as a living article.

    The page IS the article (the overview note) plus live facts: status from
    timestamps, sessions as the primary-source layer, and a changelog projected
    from the article's version chain. Note-kind sections are gone — notes are a
    retrieval substrate now, reachable as citations, not page furniture.
    """

    project: str
    rollup: ProjectRollup | None  # None when nothing happened in the window
    #: "active" | "stale", from the last session timestamp. Zero-LLM.
    status: str = "stale"
    #: The living article. None until the overview pass has run.
    overview: KnowledgeNote | None = None
    #: Append-only, one line per article revision: the version chain's
    #: change_summary values. Never part of the body, so the body stays O(1).
    changelog: list[dict[str, object]] = field(default_factory=list)
    sessions: list[SessionCard] = field(default_factory=list)
    pending_count: int = 0


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
    now_dt, since = _window(now, days)
    windowed = _readable(store.query_compactions(org_id=org_id, project=project, since=since))
    recent = _readable(store.query_compactions(org_id=org_id, project=project, limit=session_limit))
    rollups = project_rollups(windowed)

    overviews = _live_notes(
        store, org_id, kind=str(NoteKind.project_overview), scope=f"project:{project}"
    )
    overview = overviews[0] if overviews else None

    # Status from the newest session we can see, windowed or not — a project
    # untouched for months should read stale, not vanish.
    latest = recent[0].started_at if recent else None
    status = project_status(latest, now=now_dt) if latest is not None else "stale"

    changelog: list[dict[str, object]] = []
    if overview is not None:
        history = store.note_history(overview.id, org_id)
        for note in sorted(history, key=lambda n: n.version, reverse=True)[:50]:
            changelog.append(
                {
                    "date": _iso(note.updated_at),
                    "version": note.version,
                    "note_id": note.id,
                    "source": str(note.source),
                    "change_summary": note.change_summary
                    or ("edited by " + note.author if note.author else "updated"),
                }
            )

    summarised, pending = split_summarised(session_cards(recent))
    return ProjectPage(
        project=project,
        rollup=rollups[0] if rollups else None,
        status=status,
        overview=overview,
        changelog=changelog,
        sessions=summarised,
        pending_count=sum(n for _p, n in pending),
    )


@dataclass(frozen=True)
class PersonProject:
    """One project an engineer works on, with the sessions behind it.

    The rollup is computed over the SAME cards listed here, not over the
    fortnight activity window — a block headed "7 sessions" sitting above 4 rows
    is a bug report waiting to happen.
    """

    rollup: ProjectRollup
    #: "active" | "stale" from the newest session in this block. Zero-LLM.
    status: str = "active"
    #: The project article's "What this is" line — a real description, so the
    #: person page can say what each project IS instead of only naming it.
    what_this_is: str = ""
    #: The last few SUMMARISED sessions only; the block is a glance, not a log.
    sessions: list[SessionCard] = field(default_factory=list)
    #: Sessions awaiting summary, collapsed to a count — never listed as rows.
    pending_count: int = 0


@dataclass(frozen=True)
class PersonPage:
    actor: str
    activity: ActorActivity | None  # None when quiet in the window
    #: The engineer's work, grouped: an engineer has several projects and each
    #: project has several sessions. A flat list cannot show which link belongs
    #: to what, which is the whole reason this page was hard to read.
    projects: list[PersonProject] = field(default_factory=list)
    #: Sessions that ran outside a git repo, so the compactor could not name a
    #: project (see JUNK_PROJECTS). Kept as their own section rather than
    #: dropped: this page is the canonical index of one engineer's work, and
    #: bucketing by rollup alone would make real released work unreachable.
    unfiled: list[SessionCard] = field(default_factory=list)
    #: Flat, all projects together. Retained for the legacy server-rendered
    #: person page (``wiki_ui``) and existing callers.
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
    """One engineer: their projects, what each project IS, and the last few
    readable sessions per project.

    Deliberately un-gated (consented-startup segment). Note-kind sections are
    gone from this page — the taxonomy is a retrieval substrate, not reading
    material, and a founder visiting a person wants "which projects, what are
    they, how's it going", not 458 gotchas.
    """
    now_dt, since = _window(now, days)
    windowed = _readable(store.query_compactions(org_id=org_id, actor=actor, since=since))
    recent = _readable(store.query_compactions(org_id=org_id, actor=actor, limit=session_limit))
    acts = activity_rollup(windowed)

    # One query for every article lead, matched by scope — not per-project.
    leads: dict[str, str] = {}
    for note in _live_notes(store, org_id, kind=str(NoteKind.project_overview)):
        slug = note.scope.removeprefix("project:")
        if slug and slug not in leads:
            leads[slug] = _article_lead(note.body)

    # Grouped over ``recent`` — the same rows that get listed — so every block's
    # header describes exactly the sessions beneath it. project_rollups already
    # orders most-recently-active first and already drops junk slugs.
    cards = session_cards(recent)
    rollups = project_rollups(recent)
    by_project: dict[str, list[SessionCard]] = {}
    for card in cards:
        by_project.setdefault(card.project, []).append(card)
    projects = []
    for r in rollups:
        block_summarised, block_pending = split_summarised(by_project.get(r.project, []))
        projects.append(
            PersonProject(
                rollup=r,
                status=project_status(r.last_active, now=now_dt),
                what_this_is=leads.get(r.project, ""),
                sessions=block_summarised[:PERSON_SESSIONS_PER_PROJECT],
                pending_count=sum(n for _p, n in block_pending),
            )
        )
    named = {r.project for r in rollups}

    unfiled_summarised, unfiled_pending = split_summarised(
        [c for c in cards if c.project not in named]
    )
    return PersonPage(
        actor=actor,
        activity=acts[0] if acts else None,
        projects=projects,
        unfiled=unfiled_summarised,
        sessions=cards,
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
    "PersonProject",
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
