"""Local purge of self-generated compactions.

Agents used to compact by shelling out to ``claude -p``. That call created a
Claude Code transcript, which the watcher then captured and compacted —
recursively. The recursion is fixed (agents never call a model now), but the
junk digests it produced are still sitting in engineers' local stores. This is
the local counterpart to the server's admin purge, so an engineer can clean
their own machine.

The markers below MIRROR ``manthana.server.purge.SELF_GENERATED_MARKERS`` and
must be kept in step with them. They are duplicated rather than imported because
the agent (Apache-2.0) must not depend on the server package (AGPL) — the same
reason ``PROMPT_VERSION`` is duplicated in ``compactor/compactor.py``.

Each marker is a contiguous phrase from the compaction prompt template or an
observed model echo of it. They are deliberately long: a false positive deletes
an engineer's real work. A genuine session ABOUT the compactor ("fix the
Manthana compactor prompt") matches none of them, because none of those is the
template's own sentence.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import re

from manthana.schemas import BaseCompaction

SELF_GENERATED_MARKERS: tuple[str, ...] = (
    "you are manthana's compactor",
    "summarize one engineering session",
    # Cut short of "compaction prompt": task_intent is truncated (the agent's
    # fallback caps it at 200 chars), so a longer marker would miss real rows.
    "session contains only the manthana compa",
)

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, fold curly apostrophes to straight, collapse whitespace — so a
    model paraphrase using U+2019, or the template's hard wrapping, still match."""
    return _WS.sub(" ", text.replace("’", "'").replace("ʼ", "'")).strip().lower()


def _haystack(compaction: BaseCompaction) -> str:
    # Only content-bearing fields — never the deterministic metadata, so a project
    # literally named "manthana" is never at risk.
    return _normalize(
        " ".join(
            p
            for p in (
                compaction.task_intent,
                compaction.approach,
                compaction.native_summary or "",
            )
            if p
        )
    )


def is_self_generated(compaction: BaseCompaction) -> bool:
    """True when this digest describes one of Manthana's OWN compaction calls."""
    haystack = _haystack(compaction)
    return any(marker in haystack for marker in SELF_GENERATED_MARKERS)


def matches(
    compaction: BaseCompaction,
    *,
    source: str | None = None,
    contains: str | None = None,
    self_generated: bool = False,
) -> bool:
    """AND across the criteria actually supplied — narrowing, never widening."""
    if source is not None and compaction.source != source:
        return False
    if self_generated and not is_self_generated(compaction):
        return False
    if contains and _normalize(contains) not in _haystack(compaction):
        return False
    return True


__all__ = ["SELF_GENERATED_MARKERS", "is_self_generated", "matches"]
