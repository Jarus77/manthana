"""Loop-detection warning action (engineer, warn, opt-out) — fires on session close.

Immediate per-session value: if a session shows repeated failed tool calls or the
compactor flagged loop/retry friction, warn the engineer. Pure local signal
([loops.detect_loops]); the dashboard reads the fired audit entries to show a banner.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.schemas import Action, ActionActor, ActionOutcome, ActionShape, ConsentClass

from .base import ActionContext, ActionResult, TriggerEvent
from .loops import detect_loops

LOOP_WARNING_ACTION = Action(
    id="loop_warning",
    name="Loop detection warning",
    shape=ActionShape.warn,
    actor=ActionActor.engineer,
    consent_class=ConsentClass.opt_out,
    description="Warn when a session shows repeated failed tool calls or loop/retry friction.",
)


class LoopWarningHandler:
    """Handler for the loop-warning action."""

    action: Action = LOOP_WARNING_ACTION

    def handles(self, event: TriggerEvent) -> bool:
        return event.type == "session_closed"

    def run(self, event: TriggerEvent, ctx: ActionContext) -> ActionResult:
        if event.session_id is None:
            return ActionResult(ActionOutcome.failed, "no_session_id")
        turns = ctx.store.get_turns(event.session_id)
        compaction = ctx.store.get_compaction(f"comp-{event.session_id}")
        signals = detect_loops(turns, compaction)
        if not signals:
            return ActionResult(ActionOutcome.suppressed, "no_loop")
        details = {
            "session_id": event.session_id,
            "signal_count": len(signals),
            "signals": [
                {
                    "source": s.source,
                    "label": s.label,
                    "count": s.count,
                    "turn_range": list(s.turn_range),
                    "evidence": s.evidence,
                }
                for s in signals
            ],
            "summary": "; ".join(s.evidence for s in signals[:3]),
        }
        return ActionResult(ActionOutcome.fired, "loop_detected", details=details)


__all__ = ["LoopWarningHandler", "LOOP_WARNING_ACTION"]
