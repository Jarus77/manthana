"""High-level compaction orchestration: load a session, compact it, store it.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.schemas import EngineeringCompaction, Mode

from .compactor import Compactor
from .llm import LLMProvider, default_provider
from .store import Store


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
    compaction = Compactor(provider or default_provider()).compact(
        session, turns, claude_summary=_claude_summary_for(session)
    )
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


__all__ = ["compact_session", "compact_pending"]
