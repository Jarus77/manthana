"""Local purge of self-generated compactions.

Agents used to compact by shelling out to ``claude -p``. That call created a
Claude Code transcript, which the watcher then captured and compacted —
recursively. The recursion is fixed (agents never call a model now), but the
junk digests it produced are still sitting in engineers' local stores. This is
the local counterpart to the server's admin purge, so an engineer can clean
their own machine.

The token tuples below MIRROR their namesakes in ``manthana.server.purge`` and
must be kept in step with them. They are duplicated rather than imported because
the agent (Apache-2.0) must not depend on the server package (AGPL) — the same
reason ``PROMPT_VERSION`` is duplicated in ``compactor/compactor.py``. A test
asserts the two sides stay identical.

Each marker is a contiguous phrase from the compaction prompt template or an
observed model echo of it. They are deliberately long: a false positive deletes
an engineer's real work. A genuine session ABOUT the compactor ("fix the
Manthana compactor prompt") matches none of them, because none of those is the
template's own sentence.

Fixed phrases cannot catch open-ended paraphrase, though — the model reworded
the prompt differently on nearly every junk row. ``is_structural_junk`` handles
those by ANDing a much looser text test with three structural facts about the
session itself (no files touched, no real project, abandoned). See the server
module for the full rationale; the short version is that a session that IS a
compaction call did no work, and no paraphrase can fake having touched files.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import re

from manthana.schemas import BaseCompaction, Outcome

SELF_GENERATED_MARKERS: tuple[str, ...] = (
    "you are manthana's compactor",
    "summarize one engineering session",
    # Cut short of "compaction prompt": task_intent is truncated (the agent's
    # fallback caps it at 200 chars), so a longer marker would miss real rows.
    "session contains only the manthana compa",
)

# Loose token classes used ONLY under the structural conjunction below, never on
# their own. Both classes must appear: what the session was about, and what it
# was doing. Substring matching, so "summar" covers every inflection.
COMPACTION_SUBJECT_TOKENS: tuple[str, ...] = ("manthana", "compactor", "compaction")
COMPACTION_ACTION_TOKENS: tuple[str, ...] = (
    "summar",
    "digest",
    "system prompt",
    "compaction prompt",
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


def _is_compaction_shaped(compaction: BaseCompaction) -> bool:
    """Loose, paraphrase-tolerant text test — safe ONLY under the structural
    conjunction in ``is_structural_junk``, never as a standalone selector."""
    haystack = _haystack(compaction)
    return any(t in haystack for t in COMPACTION_SUBJECT_TOKENS) and any(
        t in haystack for t in COMPACTION_ACTION_TOKENS
    )


def is_structural_junk(compaction: BaseCompaction) -> bool:
    """True when this digest is a compaction call rather than real work.

    Requires ALL of: no files touched, no real project, an abandoned outcome,
    and a compaction-shaped text signal. The structural facts are what make the
    looser text test safe — a genuine session ABOUT the compactor touches files
    and carries a project, so it fails here on structure alone.
    """
    # ``files_touched`` lives on EngineeringCompaction; a bare BaseCompaction
    # carries no file evidence at all, which is the empty case either way.
    files = getattr(compaction, "files_touched", None) or []
    if files:
        return False
    project = _normalize(compaction.project or "")
    if project and project != "unknown":
        return False
    if compaction.outcome != Outcome.abandoned:
        return False
    return is_self_generated(compaction) or _is_compaction_shaped(compaction)


def matches(
    compaction: BaseCompaction,
    *,
    source: str | None = None,
    contains: str | None = None,
    self_generated: bool = False,
    structural_junk: bool = False,
) -> bool:
    """AND across the criteria actually supplied — narrowing, never widening."""
    if source is not None and compaction.source != source:
        return False
    if self_generated and not is_self_generated(compaction):
        return False
    if structural_junk and not is_structural_junk(compaction):
        return False
    if contains and _normalize(contains) not in _haystack(compaction):
        return False
    return True


__all__ = [
    "SELF_GENERATED_MARKERS",
    "COMPACTION_SUBJECT_TOKENS",
    "COMPACTION_ACTION_TOKENS",
    "is_self_generated",
    "is_structural_junk",
    "matches",
]
