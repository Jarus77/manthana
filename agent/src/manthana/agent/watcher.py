"""Auto-capture daemon: poll the Claude Code transcript dir and ingest changes.

``watch`` is a stdlib polling loop (deliberately no ``watchdog`` dependency). It
tracks each transcript's mtime and re-ingests only new/changed files via the
incremental, idempotent ``ingest_file``. Capture-only by default; ``compact=True``
also runs ``compact_pending`` after a change (which spends model tokens).

Everything external (the collector, the ingest/compact callables, ``sleep``, the
log sink, and the cycle count) is injectable so the loop is hermetically testable
without a real ``~/.claude`` or a real model.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from manthana.collectors import ClaudeCodeCollector

from .capture import IngestResult, ingest_file
from .compact import compact_settled
from .llm import LLMProvider
from .release import auto_release as _auto_release
from .store import Store

_log = logging.getLogger(__name__)


def _scan(collector: ClaudeCodeCollector) -> dict[str, float]:
    """Map each discovered transcript to its mtime (raced/removed files skipped)."""
    out: dict[str, float] = {}
    try:
        sources = collector.discover()
    except OSError:
        # A glob over the projects dir can raise on a permission change / broken
        # symlink — log and skip this cycle rather than killing the daemon.
        _log.exception("watch: discover() failed; skipping this cycle")
        return out
    for src in sources:
        try:
            out[src] = os.stat(src).st_mtime
        except OSError:
            continue  # file vanished between discover() and stat() — pick it up later
    return out


def watch(
    store: Store,
    *,
    collector: ClaudeCodeCollector | None = None,
    interval: float = 5.0,
    auto_compact: bool = False,
    summarized_only: bool = False,
    settle_seconds: float = 600.0,
    max_per_cycle: int = 5,
    provider: LLMProvider | None = None,
    iterations: int | None = None,
    ingest: Callable[..., IngestResult] = ingest_file,
    compact_fn: Callable[..., list[Any]] = compact_settled,
    auto_release: bool = False,
    release_window: float = 600.0,
    release_fn: Callable[..., int] = _auto_release,
    auto_min_interval: float = 30.0,
    sync_fn: Callable[[Store], int] | None = None,
    sync_min_interval: float = 60.0,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> dict[str, float]:
    """Poll for new/changed transcripts; ingest, (auto-)compact, auto-release, sync.

    Ingest is incremental/idempotent (only mtime-changed files). When ``auto_compact``
    is on, each cycle compacts Work sessions whose transcript has been quiet for
    ``settle_seconds`` and lack an up-to-date digest (missing or stale-after-resume) —
    run every cycle (not just on change) so a session that *went* quiet compacts on a
    later cycle. When ``auto_release`` is on, compactions past ``release_window`` are
    released (opt-out; personal/held excluded), then auto-synced. ``clock`` is monotonic
    (sync throttle); ``wall_clock`` is wall time (settle/release windows vs file mtime).
    Returns the final ``{path: mtime}`` map.
    """
    collector = collector or ClaudeCodeCollector()
    emit = log or _log.info
    seen: dict[str, float] = {}
    last_sync: float | None = None
    last_auto: float | None = None
    cycle = 0
    while iterations is None or cycle < iterations:
        current = _scan(collector)
        changed = [path for path, mtime in current.items() if seen.get(path) != mtime]
        if changed:
            sessions = turns = ok = 0
            for src in changed:
                try:
                    result = ingest(store, src, collector=collector)
                except Exception:  # noqa: BLE001 - one bad transcript must not kill the loop
                    _log.exception("watch: failed to ingest %s", src)
                    seen.pop(src, None)  # forget it so it retries even if mtime is unchanged
                    continue
                sessions += result.session_count
                turns += result.turn_count
                ok += 1
                seen[src] = current[src]  # remember only cleanly-ingested files
            emit(f"ingested {ok} files -> {sessions} sessions, {turns} turns")
        # Auto-compact SETTLED sessions + auto-release past-window digests. Run on a
        # throttle (the windows are minutes-scale; the scan covers the whole store, so we
        # don't redo it every 5s cycle), independent of `changed` so a session that *went*
        # quiet is still picked up. ``max_per_cycle`` bounds the first-run backlog burst.
        if (auto_compact or auto_release) and (
            last_auto is None or clock() - last_auto >= auto_min_interval
        ):
            if auto_compact:
                try:
                    results = compact_fn(
                        store, provider=provider, now=wall_clock(),
                        settle_seconds=settle_seconds, summarized_only=summarized_only,
                        max_per_cycle=max_per_cycle,
                    )
                    if results:
                        cost = sum(c or 0.0 for _, c in results)
                        emit(
                            f"compacted {len(results)} settled session(s) "
                            f"(call cost ~${cost:.4f})"
                        )
                except Exception:  # noqa: BLE001 - compaction failure must not kill the loop
                    _log.exception("watch: auto-compaction failed")
            if auto_release:
                try:
                    n = release_fn(store, now=wall_clock(), window_seconds=release_window)
                    if n:
                        emit(f"auto-released {n} compaction(s)")
                except Exception:  # noqa: BLE001 - a release failure must not kill the loop
                    _log.exception("watch: auto-release failed")
            last_auto = clock()
        # Auto-sync released compactions, rate-limited to once per `sync_min_interval` so a
        # short poll interval doesn't hammer the server. Only released/redacted/non-personal.
        if sync_fn is not None and (last_sync is None or clock() - last_sync >= sync_min_interval):
            try:
                pushed = sync_fn(store)
                if pushed:
                    emit(f"synced {pushed} released compactions")
            except Exception:  # noqa: BLE001 - a sync failure must not kill the loop
                _log.exception("watch: auto-sync failed")
            last_sync = clock()  # set even on failure, so a failing sync doesn't retry-spam
        # Forget files that disappeared so a recreated path re-ingests.
        seen = {path: mtime for path, mtime in seen.items() if path in current}
        cycle += 1
        if iterations is not None and cycle >= iterations:
            break
        sleep(interval)
    return seen


__all__ = ["watch"]
