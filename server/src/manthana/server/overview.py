"""Project overviews — one note per project saying what the project IS.

A project slug is only ever the git repo directory name (`collectors/project.py`),
so the wiki had no way to say what `scribe` is beyond "a project in the LSIITB
organisation". That is a fact about the org, not a description of the project.

The description lives in a ``project_overview`` KnowledgeNote rather than a new
column, which buys the whole editorial contract for free: versioning, the teach
verbs so a human can correct it, append-only supersede chains, and
human-outranks-AI. Correcting a description is the same gesture as correcting any
other claim in the wiki.

Two properties do the heavy lifting:

  * **``contributors_hash`` is the cost control.** It digests the exact set of
    contributing compaction ids, so a pass regenerates when the WORK changed, not
    when the clock moved. Keyed on time instead, ten projects on an hourly loop
    would be ~87,000 model calls a year; keyed on this it is roughly one per
    project per burst of new work.
  * **A human-written overview is never regenerated, and never even costed.**
    Stronger than ``consolidate``'s law (which downgrades a `refines` against a
    human note to `contradicts`) and deliberately so: there is nothing to
    *dispute* about a page's own description — the human's version simply wins —
    and skipping before the call also saves the money.

Structure mirrors ``consolidate.py``: bounded batches, per-org ``MeteredProvider``
sharing the monthly cap, quota defers the org cleanly, the pass never raises, and
``build_overview_note`` is pure so the write path is testable without a model.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from manthana.schemas import (
    BODY_CHAR_CAP,
    BaseCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
)
from manthana.skills.projections import is_real_project

from .enrich.coerce import extract_json, str_list
from .graph import entity_edges
from .metering import MeteredProvider, QuotaExceededError
from .pages import _readable
from .teach import _note_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import ServerConfig
    from .llm import LLMProvider
    from .store import ServerStore

_log = logging.getLogger(__name__)

#: States that mean "nothing to do unless the work changes".
_SETTLED = ("done", "human_held", "insufficient")


@dataclass
class OverviewStats:
    """Outcome of one pass, per the states an operator cares about."""

    written: int = 0
    skipped_unchanged: int = 0
    skipped_human: int = 0
    insufficient: int = 0
    failed: int = 0
    abandoned: int = 0
    quota_blocked: int = 0
    orgs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "written": self.written,
            "skipped_unchanged": self.skipped_unchanged,
            "skipped_human": self.skipped_human,
            "insufficient": self.insufficient,
            "failed": self.failed,
            "abandoned": self.abandoned,
            "quota_blocked": self.quota_blocked,
            "orgs": self.orgs,
        }


def contributing_compactions(
    store: ServerStore, org_id: str, project: str, *, limit: int
) -> list[BaseCompaction]:
    """The evidence an overview is written from.

    Reuses the wiki's own display filter so the description is grounded in
    exactly what a reader sees, and drops unenriched digests because their
    ``task_intent`` is the engineer's literal first prompt rather than a summary
    — feeding those to the model would describe the project in terms of whatever
    someone happened to paste.
    """
    comps = _readable(store.query_compactions(org_id=org_id, project=project, limit=limit))
    return [c for c in comps if str(getattr(c, "source", "")) != "pending"]


def contributors_hash(comps: Sequence[BaseCompaction]) -> str:
    """Digest of the exact contributing session set.

    Sorted, so ordering churn cannot trigger a rewrite. Ids ONLY, so re-enriching
    a session in place does not count as the project changing — a reworded
    approach on the same session is not a change to what the project is. Nothing
    time-varying may enter this: a timestamp here would regenerate every project
    on every interval, which is the failure mode this exists to prevent.
    """
    return hashlib.sha256("\n".join(sorted(c.id for c in comps)).encode()).hexdigest()[:16]


def _top_files(comps: Sequence[BaseCompaction], n: int = 20) -> list[str]:
    counter: Counter[str] = Counter()
    for c in comps:
        counter.update(getattr(c, "files_touched", None) or [])
    return [path for path, _count in counter.most_common(n)]


def _distinct(comps: Sequence[BaseCompaction], attr: str, n: int = 12) -> list[str]:
    seen: list[str] = []
    for c in comps:
        for value in getattr(c, attr, None) or []:
            if value not in seen:
                seen.append(value)
    return seen[:n]


def build_overview_prompt(
    project: str, org_id: str, comps: Sequence[BaseCompaction]
) -> str:
    """The prompt. Session lines deliberately OMIT the actor field — the rule
    against naming people is then unbreakable rather than merely instructed."""
    lines = [
        "You are writing the encyclopedia lead for ONE software project in a",
        "startup's engineering wiki. Describe what the project IS — its purpose,",
        "what it does, its main components and the technologies it is built on —",
        "for a reader who has never seen it before.",
        "",
        f"PROJECT: {project}",
        f"ORGANISATION: {org_id}",
        "",
        f"RECENT WORK ({len(comps)} sessions, newest first):",
    ]
    for c in comps:
        files = ", ".join((getattr(c, "files_touched", None) or [])[:8])
        lines.append(f"  - intent: {c.task_intent}")
        lines.append(f"    approach: {c.approach}")
        if files:
            lines.append(f"    files: {files}")
    lines += [
        "",
        f"MOST-TOUCHED FILES: {', '.join(_top_files(comps)) or '(none)'}",
        f"LANGUAGES: {', '.join(_distinct(comps, 'languages')) or '(unknown)'}",
        f"FRAMEWORKS: {', '.join(_distinct(comps, 'frameworks')) or '(unknown)'}",
        "",
        "Return ONLY a JSON object:",
        '{"title": "<the project\'s name as an article heading>",',
        ' "body": "<2-4 short markdown paragraphs, under 300 tokens. The FIRST',
        '           paragraph must be ONE self-contained sentence stating what the',
        '           project is; it is shown alone as the article lead.>",',
        ' "concepts": ["..."], "libraries": ["..."]}',
        "",
        "Rules:",
        "- Describe the PROJECT, not the sessions. Never write 'engineers worked",
        "  on...', never name a person, never give dates, session counts, costs or",
        "  activity levels — those are computed live on the page and would go",
        "  stale the moment they were written into a note.",
        "- Claim only what the evidence supports. Do not guess at a purpose the",
        "  files and intents do not show.",
        "- If the work shown is too thin to say what the project is, return exactly",
        '  {"insufficient": true} and nothing else.',
        "- No decisions, conventions, gotchas, failure patterns or benchmark",
        "  results — those are separate note kinds written by a different pass.",
        "- Plain declarative prose. No marketing language, no feature bullet lists.",
    ]
    return "\n".join(lines)


def _clip(body: str) -> str:
    if len(body) <= BODY_CHAR_CAP:
        return body
    return body[: BODY_CHAR_CAP - 12].rstrip() + " …[truncated]"


def build_overview_note(
    data: dict[str, Any],
    *,
    prior: KnowledgeNote | None,
    comps: Sequence[BaseCompaction],
    org_id: str,
    project: str,
    now: datetime,
) -> KnowledgeNote | None:
    """Pure: model output → a note version. None when the payload is unusable."""
    body = str(data.get("body") or "").strip()
    if not body:
        return None
    title = str(data.get("title") or project).strip()[:200]
    status = (
        NoteStatus.established
        if prior is not None and prior.status == NoteStatus.established
        else NoteStatus.candidate
    )
    return KnowledgeNote(
        id=_note_id(),
        org_id=org_id,
        kind=NoteKind.project_overview,
        title=title,
        body=_clip(body),
        scope=f"project:{project}",
        entities=NoteEntities(
            projects=[project],
            files=_top_files(comps, n=10),
            libraries=str_list(data.get("libraries")),
            concepts=str_list(data.get("concepts")),
        ),
        evidence=[c.id for c in comps],
        actors=sorted({c.actor for c in comps if c.actor}),
        source=NoteSource.ai,
        status=status,
        confidence=0.5,
        version=(prior.version + 1) if prior else 1,
        supersedes=prior.id if prior else None,
        created_at=now,
        updated_at=now,
    )


def _current_overview(
    store: ServerStore, org_id: str, project: str
) -> KnowledgeNote | None:
    notes = store.query_notes(
        org_id,
        kind=str(NoteKind.project_overview),
        scope=f"project:{project}",
        exclude_superseded=True,
    )
    return notes[0] if notes else None


def refresh_org_overviews(
    store: ServerStore,
    provider: LLMProvider,
    config: ServerConfig,
    *,
    org_id: str,
    limit: int,
    now: datetime | None = None,
) -> OverviewStats:
    """Refresh up to ``limit`` project overviews for one org. Never raises."""
    now = now or datetime.now(UTC)
    stats = OverviewStats(orgs=[org_id])
    state = store.overview_state(org_id)
    written = 0

    for project in store.list_projects(org_id):
        if written >= limit:
            break
        if not is_real_project(project):
            continue

        comps = contributing_compactions(
            store, org_id, project, limit=config.overview_session_limit
        )
        if len(comps) < config.overview_min_sessions:
            continue  # nothing to describe; no state write, no call

        digest = contributors_hash(comps)
        st = state.get(project)
        if st is not None:
            if st.state == "abandoned":
                continue
            if st.contributors_hash == digest and st.state in _SETTLED:
                stats.skipped_unchanged += 1
                continue  # the work has not changed — NO MODEL CALL
            if st.state == "failed" and st.attempts >= config.overview_max_attempts:
                store.mark_overview_abandoned(
                    org_id, project, detail="attempts exhausted"
                )
                stats.abandoned += 1
                continue

        prior = _current_overview(store, org_id, project)
        if prior is not None and prior.source == NoteSource.human:
            # A human wrote this description. It wins, permanently — and
            # recording the hash means we never even price the call again.
            store.mark_overview_done(
                org_id, project, contributors_hash=digest,
                note_id=prior.id, state="human_held",
            )
            stats.skipped_human += 1
            continue

        try:
            raw = provider.complete(build_overview_prompt(project, org_id, comps))
        except QuotaExceededError:
            stats.quota_blocked += 1
            return stats  # defer the whole org; retry next interval
        except Exception as exc:  # noqa: BLE001 - a bad call must not kill the pass
            _log.exception("overview: model call failed for %s/%s", org_id, project)
            store.record_overview_failure(org_id, project, detail=str(exc))
            stats.failed += 1
            continue

        data = extract_json(raw)
        if not data:
            store.record_overview_failure(org_id, project, detail="no JSON in response")
            stats.failed += 1
            continue
        if data.get("insufficient"):
            # Record the hash so this does not retry until new work lands.
            store.mark_overview_done(
                org_id, project, contributors_hash=digest,
                note_id=prior.id if prior else "", state="insufficient",
            )
            stats.insufficient += 1
            continue

        note = build_overview_note(
            data, prior=prior, comps=comps, org_id=org_id, project=project, now=now
        )
        if note is None:
            store.record_overview_failure(org_id, project, detail="unusable payload")
            stats.failed += 1
            continue

        if prior is None:
            store.upsert_note(note)
        else:
            store.supersede_note(prior.id, note, org_id)  # append-only chain
        store.add_edges(org_id, entity_edges(note))
        store.mark_overview_done(
            org_id, project, contributors_hash=digest, note_id=note.id
        )
        stats.written += 1
        written += 1

    return stats


def overview_provider_for(
    store: ServerStore, config: ServerConfig, inner: LLMProvider
) -> Callable[[str], LLMProvider]:
    """Per-org metered provider — shares the monthly cap with the other passes."""

    def _for(org_id: str) -> LLMProvider:
        cap = store.get_org_quota(org_id)
        if cap is None:
            cap = config.llm_monthly_cap_usd
        return MeteredProvider(inner, store, org_id, cap)

    return _for


def run_overview_pass(
    store: ServerStore,
    config: ServerConfig,
    provider_for: Callable[[str], LLMProvider],
    *,
    now: datetime | None = None,
) -> OverviewStats:
    """One whole-pass sweep across every org. Never raises."""
    total = OverviewStats()
    remaining = config.overview_max_per_pass
    for org in store.list_orgs():
        if remaining <= 0:
            break
        stats = refresh_org_overviews(
            store, provider_for(org.id), config, org_id=org.id, limit=remaining, now=now
        )
        total.written += stats.written
        total.skipped_unchanged += stats.skipped_unchanged
        total.skipped_human += stats.skipped_human
        total.insufficient += stats.insufficient
        total.failed += stats.failed
        total.abandoned += stats.abandoned
        total.quota_blocked += stats.quota_blocked
        if stats.written or stats.failed:
            total.orgs.append(org.id)
        remaining -= stats.written
    return total


__all__ = [
    "OverviewStats",
    "build_overview_note",
    "build_overview_prompt",
    "contributing_compactions",
    "contributors_hash",
    "overview_provider_for",
    "refresh_org_overviews",
    "run_overview_pass",
]
