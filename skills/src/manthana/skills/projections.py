"""Zero-LLM page projections shared by the personal wiki (agent) and org wiki (server).

Pure functions over lists of ``Compaction`` schema objects — no store dependency;
each side passes in what it already queried and renders its own HTML. This keeps
the two wikis' numbers identical without coupling their UIs.

The projections implement the live half of the note-vs-rollup split: "what is X
working on" / "state of project Y" is always computed fresh from recent
compactions here, never persisted as a note — so it can't go stale.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


def _sort_key(c: Any) -> datetime:
    return c.started_at


def filter_since(compactions: list[Any], since: datetime | None) -> list[Any]:
    """In-memory window filter for callers that hold a full list (agent side)."""
    if since is None:
        return list(compactions)
    return [c for c in compactions if c.started_at >= since]


@dataclass(frozen=True)
class ProjectRollup:
    """One project's live status line."""

    project: str
    sessions: int
    actors: list[str]  # distinct, sorted
    outcome_mix: dict[str, int]  # outcome -> count
    last_active: datetime
    top_intent: str  # most recent session's task_intent — the human-readable "what"
    est_cost_usd: float
    total_tokens: int


#: Project slugs that are an ABSENCE of a project rather than a project. The
#: compactor falls back to these when a session ran outside a git repo or the
#: directory name told it nothing, so they collect unrelated work from everyone
#: and read on a wiki as if they were real shared efforts. Excluded from rollups
#: and indexes; the sessions themselves stay reachable via their author.
JUNK_PROJECTS = frozenset({"", "unknown", "project", "projects", "tmp", "temp", "untitled"})


def is_pending(compaction: Any) -> bool:
    """True when a digest has not been summarised yet.

    An unenriched digest's ``task_intent`` is the engineer's LITERAL first
    prompt (the compactor's deterministic fallback, "grounded in the first user
    turn"), not a summary — typos, run-ons and all. That is legitimate data on
    the session's own page and unreadable anywhere else, so the projections that
    quote intent OUT of context skip it rather than clipping it prettier.

    The projections must do this, not the client: ``ProjectRollup.top_intent``
    and ``ActorActivity.intents`` are bare strings with no ``source`` beside
    them, so a renderer has no way to tell a summary from a raw prompt.
    """
    return str(getattr(compaction, "source", "")) == "pending"


#: A project with no released session in this window reads as stale. 7 days
#: matches the wiki's home window: "active" means "someone touched it this week".
STALE_AFTER_DAYS = 7


def project_status(
    last_active: datetime, *, now: datetime | None = None, stale_days: int = STALE_AFTER_DAYS
) -> str:
    """"active" | "stale", computed from the last session time. Deliberately a
    pure function and deliberately not an LLM call — staleness is a fact about
    timestamps, and the product spec calls for exactly this: status detection is
    free because the sessions already carry times."""
    now = now or datetime.now(UTC)
    anchor = last_active if last_active.tzinfo else last_active.replace(tzinfo=UTC)
    return "stale" if (now - anchor) > timedelta(days=stale_days) else "active"


def is_real_project(project: str | None) -> bool:
    return bool(project) and project.strip().lower() not in JUNK_PROJECTS


def project_rollups(compactions: list[Any]) -> list[ProjectRollup]:
    """Group by project, most recently active first. Junk slugs are dropped."""
    by_project: dict[str, list[Any]] = {}
    for c in compactions:
        if not is_real_project(c.project):
            continue
        by_project.setdefault(c.project, []).append(c)
    out: list[ProjectRollup] = []
    for project, items in by_project.items():
        items = sorted(items, key=_sort_key, reverse=True)
        out.append(
            ProjectRollup(
                project=project,
                sessions=len(items),
                actors=sorted({c.actor for c in items}),
                outcome_mix=dict(Counter(str(c.outcome) for c in items)),
                last_active=items[0].started_at,
                top_intent=next(
                    (c.task_intent for c in items if not is_pending(c)), ""
                ),
                est_cost_usd=round(sum(c.est_cost_usd or 0.0 for c in items), 4),
                total_tokens=sum(c.total_tokens or 0 for c in items),
            )
        )
    out.sort(key=lambda r: r.last_active, reverse=True)
    return out


@dataclass(frozen=True)
class SessionCard:
    """Display projection of one full compaction — the wiki shows the digest
    itself, not a re-summarization of it."""

    id: str
    session_id: str
    actor: str
    project: str
    surface: str
    started_at: datetime
    duration_seconds: float
    task_intent: str
    approach: str
    outcome: str
    friction: list[str]  # "category: description"
    artifacts: list[str]
    files_touched: list[str]
    prs_opened: list[str]
    tests_added: list[str]
    languages: list[str]
    tier_used: str | None
    est_cost_usd: float | None
    total_tokens: int | None
    source: str
    released: bool
    hold: bool


def session_card(c: Any) -> SessionCard:
    return SessionCard(
        id=c.id,
        session_id=c.session_id,
        actor=c.actor,
        project=c.project,
        surface=str(c.surface),
        started_at=c.started_at,
        duration_seconds=c.duration_seconds,
        task_intent=c.task_intent,
        approach=c.approach,
        outcome=str(c.outcome),
        friction=[f"{fp.category}: {fp.description}" for fp in c.friction_points],
        artifacts=list(c.artifacts),
        files_touched=list(getattr(c, "files_touched", [])),
        prs_opened=list(getattr(c, "prs_opened", [])),
        tests_added=list(getattr(c, "tests_added", [])),
        languages=list(getattr(c, "languages", [])),
        tier_used=c.tier_used,
        est_cost_usd=c.est_cost_usd,
        total_tokens=c.total_tokens,
        source=c.source,
        released=c.released,
        hold=c.hold,
    )


def session_cards(compactions: list[Any]) -> list[SessionCard]:
    """Chronological cards, newest first."""
    return [session_card(c) for c in sorted(compactions, key=_sort_key, reverse=True)]


@dataclass(frozen=True)
class ActorActivity:
    """One person's live "currently working on" line."""

    actor: str
    sessions: int
    projects: list[str]  # distinct, most-recent first
    intents: list[str]  # recent task_intents, newest first
    last_active: datetime
    outcome_mix: dict[str, int]


def activity_rollup(compactions: list[Any], *, max_intents: int = 5) -> list[ActorActivity]:
    """Group by actor, most recently active first. This IS the answer to
    "what is <person> working on" — always live, never a note."""
    by_actor: dict[str, list[Any]] = {}
    for c in compactions:
        by_actor.setdefault(c.actor, []).append(c)
    out: list[ActorActivity] = []
    for actor, items in by_actor.items():
        items = sorted(items, key=_sort_key, reverse=True)
        projects = list(dict.fromkeys(c.project for c in items if is_real_project(c.project)))
        out.append(
            ActorActivity(
                actor=actor,
                sessions=len(items),
                projects=projects,
                # Dropped, not placeheld: a column of identical "awaiting
                # summary" strings answers nothing, while an empty list renders
                # honestly as an em dash.
                intents=[c.task_intent for c in items if not is_pending(c)][:max_intents],
                last_active=items[0].started_at,
                outcome_mix=dict(Counter(str(c.outcome) for c in items)),
            )
        )
    out.sort(key=lambda a: a.last_active, reverse=True)
    return out


__all__ = [
    "ProjectRollup",
    "SessionCard",
    "ActorActivity",
    "project_rollups",
    "session_card",
    "session_cards",
    "activity_rollup",
    "filter_since",
]
