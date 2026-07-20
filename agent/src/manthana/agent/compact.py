"""High-level compaction orchestration: load a session, compact it, store it.

Compaction here is deterministic and local — no LLM provider is involved on the
agent at any point (see ``compactor.py`` for why). The qualitative fields are
filled server-side later.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from manthana.collectors.sessionize import GAP
from manthana.schemas import EngineeringCompaction, Mode, Surface

from .compactor import Compactor
from .store import Store

_log = logging.getLogger(__name__)

#: How long a transcript must be quiet before it is compacted — DERIVED from the
#: session-boundary gap, never chosen independently.
#:
#: ``sessionize`` closes a segment only when the next turn arrives more than
#: ``GAP`` after the last one. If the settle window were SHORTER than that, a
#: pause in between would compact a session sessionize has not yet closed —
#: half a session, uploaded. Worse, the compaction id is deterministic
#: (``comp-<session.id>``) and server ingest is an upsert, so resuming rewrites
#: that row with ``source="pending"`` again and the server pays to enrich the
#: same session twice.
#:
#: Deriving the constant is the point: two independently-chosen numbers drifted
#: apart once already (settle 10 min vs GAP 30 min) and nothing caught it.
DEFAULT_SETTLE_SECONDS = GAP.total_seconds()


def _native_summary_for(session: object) -> str | None:
    """Read a surface-native context summary on demand when one was captured."""
    if not getattr(session, "has_compact_summary", False):
        return None
    source = getattr(session, "source_path", None)
    if not source:
        return None
    from manthana.collectors import ClaudeCodeCollector, CodexCollector

    collector = (
        CodexCollector()
        if getattr(session, "surface", None) is Surface.codex
        else ClaudeCodeCollector()
    )
    summary = collector.read_summary(source)
    return summary.text if summary else None


def compact_session(store: Store, session_id: str) -> EngineeringCompaction | None:
    """Compact one stored session and persist the result. None if not found.

    When the session carries a surface-native compaction summary, its text is
    carried on the digest (``native_summary``) so the server can enrich from it
    instead of the whole transcript.
    """
    session = store.get_session(session_id)
    if session is None:
        return None
    turns = store.get_turns(session_id)
    compaction = Compactor().compact(session, turns, native_summary=_native_summary_for(session))
    # Re-compaction (resume): carry over the engineer's local trust flags — `hold` MUST
    # survive (it's the auto-release opt-out), and a previously-released digest stays
    # released. Force the changed content to re-sync (sync dedups by id).
    prev = store.get_compaction(compaction.id)
    if prev is not None:
        compaction.hold = prev.hold
        compaction.released = prev.released
        compaction.released_at = prev.released_at
        store.clear_synced(compaction.id)
    # call_cost_usd stays None: building a digest costs nothing now (no model call).
    store.upsert_compaction(compaction)
    return compaction


def compact_pending(
    store: Store,
    *,
    limit: int | None = None,
    summarized_only: bool = False,
) -> list[EngineeringCompaction]:
    """Compact Work-mode sessions that don't yet have a compaction.

    Personal-mode sessions are skipped (they never contribute to anything that
    could be released). With ``summarized_only`` set, only sessions that carry
    the surface's own compaction summary are compacted.
    """
    existing = {c.session_id for c in store.list_compactions()}
    out: list[EngineeringCompaction] = []
    for session in store.list_sessions(limit=limit):
        if session.mode is Mode.personal or session.id in existing:
            continue
        if summarized_only and not session.has_compact_summary:
            continue
        compaction = compact_session(store, session.id)
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
    now: float | None = None,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    mtime_of: object = os.path.getmtime,
    summarized_only: bool = False,
    max_per_cycle: int | None = None,
    limit: int | None = None,
) -> list[EngineeringCompaction]:
    """Auto-compact Work sessions whose transcript has been quiet for >= ``settle_seconds``
    and lack an up-to-date digest (none yet, OR stale because the transcript changed after
    the digest was built — i.e. a resume).

    Personal-mode sessions are skipped (they never leave the laptop). ``max_per_cycle``
    caps how many NEW compactions run per call. This is no longer a cost bound (compaction
    is local and free); it now bounds how long a single watcher cycle can block on store
    I/O for a first-run backlog, keeping the poll loop responsive. The staleness signal is
    file-mtime > digest ``created_at`` (robust even when a session has no ``ended_at``)."""
    now = time.time() if now is None else now
    existing = {c.session_id: c for c in store.list_compactions()}
    out: list[EngineeringCompaction] = []
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
            new = compact_session(store, session.id)
        except Exception:  # noqa: BLE001 - one bad session must not abort the whole cycle
            _log.exception("compact_settled: compaction failed for %s", session.id)
            continue
        if new is not None:
            out.append(new)
    return out


__all__ = [
    "DEFAULT_SETTLE_SECONDS","compact_session", "compact_pending", "compact_settled"]
