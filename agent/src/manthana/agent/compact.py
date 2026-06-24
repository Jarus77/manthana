"""High-level compaction orchestration: load a session, compact it, store it.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from manthana.schemas import EngineeringCompaction, Mode

from .compactor import Compactor
from .llm import LLMError, LLMProvider, default_provider
from .store import Store

_log = logging.getLogger(__name__)


def _claude_summary_for(session: object) -> str | None:
    """Claude's own compaction summary for a session, read on demand from its
    transcript (cheap scan) — None unless the session was flagged as carrying one."""
    if not getattr(session, "has_compact_summary", False):
        return None
    source = getattr(session, "source_path", None)
    if not source:
        return None
    from manthana.collectors import ClaudeCodeCollector

    summary = ClaudeCodeCollector().read_summary(source)
    return summary.text if summary else None


def compact_session(
    store: Store,
    session_id: str,
    *,
    provider: LLMProvider | None = None,
) -> EngineeringCompaction | None:
    """Compact one stored session and persist the result. None if not found.

    When the session carries Claude's own compaction summary, that is used as the
    (cheap) input instead of the full transcript.
    """
    session = store.get_session(session_id)
    if session is None:
        return None
    turns = store.get_turns(session_id)
    provider = provider or default_provider()
    compaction = Compactor(provider).compact(
        session, turns, claude_summary=_claude_summary_for(session)
    )
    # Re-compaction (resume): carry over the engineer's local trust flags — `hold` MUST
    # survive (it's the auto-release opt-out), and a previously-released digest stays
    # released. Force the changed content to re-sync (sync dedups by id).
    prev = store.get_compaction(compaction.id)
    if prev is not None:
        compaction.hold = prev.hold
        compaction.released = prev.released
        compaction.released_at = prev.released_at
        store.clear_synced(compaction.id)
    compaction.call_cost_usd = getattr(provider, "last_cost_usd", None)
    store.upsert_compaction(compaction)
    return compaction


def compact_pending(
    store: Store,
    *,
    provider: LLMProvider | None = None,
    limit: int | None = None,
    summarized_only: bool = False,
) -> list[EngineeringCompaction]:
    """Compact Work-mode sessions that don't yet have a compaction.

    Personal-mode sessions are skipped (they never contribute to anything that
    could be released). With ``summarized_only`` set, only sessions that carry
    Claude's own compaction summary are compacted (the cheap path used by the
    auto-compact daemon). Each session is compacted via ``compact_session``, so the
    summary is used as the input when present.
    """
    provider = provider or default_provider()
    existing = {c.session_id for c in store.list_compactions()}
    out: list[EngineeringCompaction] = []
    for session in store.list_sessions(limit=limit):
        if session.mode is Mode.personal or session.id in existing:
            continue
        if summarized_only and not session.has_compact_summary:
            continue
        compaction = compact_session(store, session.id, provider=provider)
        if compaction is not None:
            out.append(compaction)
    return out


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def compact_settled(
    store: Store,
    *,
    provider: LLMProvider | None = None,
    now: float | None = None,
    settle_seconds: float = 600.0,
    mtime_of: object = os.path.getmtime,
    summarized_only: bool = False,
    max_per_cycle: int | None = None,
    limit: int | None = None,
) -> list[tuple[EngineeringCompaction, float | None]]:
    """Auto-compact Work sessions whose transcript has been quiet for >= ``settle_seconds``
    and lack an up-to-date digest (none yet, OR stale because the transcript changed after
    the digest was built — i.e. a resume).

    Personal-mode sessions are skipped (they never leave the laptop). ``max_per_cycle``
    caps how many NEW compactions run per call so a first-run backlog doesn't fire an
    unbounded burst of (paid) CLI calls — the rest are picked up on later cycles. The
    staleness signal is file-mtime > digest ``created_at`` (robust even when a session has
    no ``ended_at``). Returns ``[(compaction, call_cost_usd)]`` (CLI-reported call cost)."""
    provider = provider or default_provider()
    now = time.time() if now is None else now
    existing = {c.session_id: c for c in store.list_compactions()}
    out: list[tuple[EngineeringCompaction, float | None]] = []
    for session in store.list_sessions(limit=limit):
        if max_per_cycle is not None and len(out) >= max_per_cycle:
            break  # per-cycle budget reached — remaining settled sessions wait for a later cycle
        if session.mode is Mode.personal:
            continue
        if summarized_only and not session.has_compact_summary:
            continue
        src = session.source_path
        if not src:
            continue
        try:
            mtime = mtime_of(src)  # type: ignore[operator]
        except OSError:
            continue
        if now - mtime < settle_seconds:
            continue  # still active / may resume — wait for it to settle
        comp = existing.get(session.id)
        if comp is not None:
            built = _as_utc(comp.created_at)
            # Up-to-date if the file hasn't changed since the digest was built. Legacy
            # digests without created_at are left alone (don't churn the whole backlog).
            if built is None or mtime <= built.timestamp():
                continue
        try:
            new = compact_session(store, session.id, provider=provider)
        except LLMError:  # one failed/timed-out CLI call must not abort the whole cycle
            _log.exception("compact_settled: compaction failed for %s", session.id)
            continue
        if new is not None:
            out.append((new, getattr(provider, "last_cost_usd", None)))
    return out


__all__ = ["compact_session", "compact_pending", "compact_settled"]
