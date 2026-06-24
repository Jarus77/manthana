"""Auto-release: an opt-OUT grace window for releasing compactions from the laptop.

The engineer wanted capture→compact→release to run itself. So a freshly-built compaction
auto-releases once it has sat for a grace window (default 10 min) — UNLESS the engineer
opted out by marking the session **personal** or **holding** the compaction in that window.
Personal-mode sessions NEVER auto-release (the hard trust invariant); "released" still
means redaction-on-sync downstream. This flips the default from opt-in-to-share to
opt-out-within-a-window, which is why the window + the explicit personal/hold escape hatches
matter.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from manthana.schemas import Mode

from .store import Store


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.timestamp()


def auto_release(store: Store, *, now: float | None = None, window_seconds: float = 600.0) -> int:
    """Release every compaction past its grace window, except held or personal ones.

    Returns the count newly released. A compaction is released when: not already
    released, not ``hold``, its session is not personal-mode, and ``now`` is at least
    ``window_seconds`` past ``created_at``. Sessions are looked up to enforce the
    personal-mode exclusion; an orphaned compaction (no session) is conservatively skipped.
    """
    now = time.time() if now is None else now
    modes = {s.id: s.mode for s in store.list_sessions()}
    released = 0
    for comp in store.list_compactions():
        if comp.released or getattr(comp, "hold", False):
            continue
        mode = modes.get(comp.session_id)
        if mode is None or mode is Mode.personal:
            continue  # personal never auto-releases; unknown session → skip (fail closed)
        created = _epoch(comp.created_at)
        if created is None or now - created < window_seconds:
            continue
        if store.mark_released(comp.id, released_at=datetime.now(UTC)):
            released += 1
    return released


__all__ = ["auto_release"]
