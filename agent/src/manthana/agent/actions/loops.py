"""Loop detection over a session's turns + the compaction's friction.

Deterministic (no model call): a "loop" is the engineer stuck repeating a failing
operation. Two independent signals — (1) the same tool failing repeatedly in the raw
turns, and (2) the compactor's own ``loop``/``retry`` friction. Used by the
loop-warning action ([loop_warning]) to give immediate per-session feedback.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from manthana.schemas import BaseCompaction, Turn

DEFAULT_THRESHOLD = 3  # N+ failures of one tool (or N+ retry turn_refs) = a loop


@dataclass
class LoopSignal:
    """One piece of evidence that a session looped."""

    source: str  # "tool_errors" | "friction:loop" | "friction:retry"
    label: str  # the tool name, or the friction category
    count: int  # failures / turn_refs supporting the signal
    turn_range: tuple[int, int]  # (min, max) seq of the supporting turns
    evidence: str  # short human-readable description


def _errored(turn: Turn) -> bool:
    """Did this tool-result turn fail? The collector maps ``tool_result.is_error`` →
    ``Turn.error``; we also catch error markers that arrive only in the result text."""
    if turn.error:
        return True
    out = turn.tool_output or ""
    return bool(out) and ("is_error" in out[:200] or out.lstrip().startswith("Error"))


def detect_loops(
    turns: list[Turn],
    compaction: BaseCompaction | None = None,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> list[LoopSignal]:
    """Return loop signals for a session. (1) any tool that failed >= ``threshold`` times;
    (2) the compaction's ``loop`` friction (always) and ``retry`` friction with >=
    ``threshold`` turn_refs. Empty list = no loop detected."""
    signals: list[LoopSignal] = []

    # (1) repeated tool failures, grouped by tool name
    fails: dict[str, list[int]] = defaultdict(list)
    for t in turns:
        if t.tool_name and _errored(t):
            fails[t.tool_name].append(t.seq)
    for tool, seqs in sorted(fails.items()):
        if len(seqs) >= threshold:
            signals.append(
                LoopSignal(
                    source="tool_errors",
                    label=tool,
                    count=len(seqs),
                    turn_range=(min(seqs), max(seqs)),
                    evidence=f"{tool} failed {len(seqs)}× (turns {min(seqs)}–{max(seqs)})",
                )
            )

    # (2) loop/retry friction the compactor already identified
    if compaction is not None:
        for fp in compaction.friction_points:
            cat = fp.category.value
            if cat == "loop" or (cat == "retry" and len(fp.turn_refs) >= threshold):
                refs = [int(r) for r in fp.turn_refs if str(r).isdigit()]
                rng = (min(refs), max(refs)) if refs else (0, 0)
                signals.append(
                    LoopSignal(
                        source=f"friction:{cat}",
                        label=cat,
                        count=len(fp.turn_refs) or 1,
                        turn_range=rng,
                        evidence=fp.description[:160],
                    )
                )
    return signals


__all__ = ["LoopSignal", "detect_loops", "DEFAULT_THRESHOLD"]
