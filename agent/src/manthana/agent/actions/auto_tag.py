"""Auto-tag sessions (engineer, write, silent) — the v1 live action.

Trigger: every closed session. Action: write project / task-type / outcome /
friction tags into the local store (decisions doc). Silent + default-on.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.schemas import Action, ActionActor, ActionOutcome, ActionShape, ConsentClass, Turn

from .base import ActionContext, ActionResult, TriggerEvent

AUTO_TAG_ACTION = Action(
    id="auto_tag_sessions",
    name="Auto-tag sessions",
    shape=ActionShape.write,
    actor=ActionActor.engineer,
    consent_class=ConsentClass.silent,
    description="Write project/task/outcome/friction tags to the local store on close.",
)

_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
_OPS_TOOLS = {"Bash"}


def _task_type(turns: list[Turn]) -> str:
    tools = {t.tool_name for t in turns if t.tool_name}
    if tools & _WRITE_TOOLS:
        return "implementation"
    if tools & _OPS_TOOLS:
        return "ops"
    if tools:
        return "exploration"
    return "conversation"


def compute_tags(ctx: ActionContext, session_id: str) -> dict[str, str] | None:
    session = ctx.store.get_session(session_id)
    if session is None:
        return None
    turns = ctx.store.get_turns(session_id)
    tags: dict[str, str] = {"project": session.project, "task_type": _task_type(turns)}

    compaction = ctx.store.get_compaction(f"comp-{session_id}")
    if compaction is not None:
        tags["outcome"] = str(compaction.outcome)
        if compaction.friction_points:
            tags["friction"] = ",".join(
                sorted({fp.category.value for fp in compaction.friction_points})
            )
    return tags


class AutoTagHandler:
    """Handler for the auto-tag action."""

    action: Action = AUTO_TAG_ACTION

    def handles(self, event: TriggerEvent) -> bool:
        return event.type == "session_closed"

    def run(self, event: TriggerEvent, ctx: ActionContext) -> ActionResult:
        if event.session_id is None:
            return ActionResult(ActionOutcome.failed, "no_session_id")
        tags = compute_tags(ctx, event.session_id)
        if tags is None:
            return ActionResult(ActionOutcome.failed, "session_not_found")
        ctx.store.update_session_tags(event.session_id, tags)
        return ActionResult(ActionOutcome.fired, "session_closed", details={"tags": tags})


__all__ = ["AutoTagHandler", "AUTO_TAG_ACTION", "compute_tags"]
