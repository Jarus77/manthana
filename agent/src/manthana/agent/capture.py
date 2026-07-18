"""Capture pipeline: transcripts -> normalized Sessions/Turns -> local store.

Ties the Claude Code and Codex collectors to the SQLite store. New sessions
default to Work mode.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from dataclasses import dataclass

from manthana.collectors import (
    ClaudeCodeCollector,
    CodexCollector,
    infer_project,
    resolve_actor,
    sessionize,
)
from manthana.schemas import Mode, Session

from .store import Store


@dataclass
class IngestResult:
    source: str
    sessions: list[Session]

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def turn_count(self) -> int:
        return sum(s.turn_count for s in self.sessions)


ReadableCollector = ClaudeCodeCollector | CodexCollector


def ingest_file(
    store: Store,
    source: str,
    *,
    actor: str | None = None,
    mode: Mode = Mode.work,
    collector: ReadableCollector | None = None,
) -> IngestResult:
    """Parse one supported transcript and persist its Session(s) and Turns."""
    actor = actor or resolve_actor()
    collector = collector or ClaudeCodeCollector(actor=actor)
    turns, meta = collector.read(source)
    project, repo_root = infer_project(meta.cwd)

    sessions = sessionize(
        turns,
        surface=collector.surface,
        actor=actor,
        project=project,
        repo_root=repo_root,
        base_session_id=meta.session_id,
        source_path=source,
        fallback_time=meta.mtime,
        mode=mode,
        summary_at_index=meta.compact_summary_at,
    )

    # Idempotent re-ingest in ONE transaction: atomically drop any prior rows for
    # this transcript's session family and persist the freshly sessionized result,
    # so a concurrent reader (e.g. the dashboard compaction thread on the same
    # SQLite file) never observes the session mid-delete (no phantom splits).
    items = list(sessions)
    store.replace_session_family(meta.session_id, items)
    return IngestResult(source=source, sessions=[session for session, _ in items])


def ingest_all(
    store: Store,
    *,
    actor: str | None = None,
    mode: Mode = Mode.work,
    collectors: list[ReadableCollector] | None = None,
) -> list[IngestResult]:
    """Discover and ingest every Claude Code and Codex transcript on this machine."""
    actor = actor or resolve_actor()
    active = collectors or [
        ClaudeCodeCollector(actor=actor),
        CodexCollector(actor=actor),
    ]
    return [
        ingest_file(store, source, actor=actor, mode=mode, collector=collector)
        for collector in active
        for source in collector.discover()
    ]


__all__ = ["ingest_file", "ingest_all", "IngestResult", "ReadableCollector"]
