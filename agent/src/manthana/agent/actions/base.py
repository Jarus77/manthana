"""Action dispatcher seam — core types.

Most actions are v1.5+; v1 ships the dispatcher plus one handler (auto-tag) so
future actions register against existing infrastructure rather than requiring
schema migrations (decisions doc: architectural seams).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from manthana.schemas import Action, ActionOutcome

if TYPE_CHECKING:
    from manthana.agent.store import Store


@dataclass
class TriggerEvent:
    """Something happened that actions may react to."""

    type: str  # e.g. "session_closed", "session_start", "error_repeated"
    actor: str
    session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """What a handler did (logged to the audit log by the dispatcher)."""

    outcome: ActionOutcome
    trigger_condition: str
    confidence: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ActionContext:
    """Resources handlers may use."""

    def __init__(self, store: Store) -> None:
        self.store = store


@runtime_checkable
class ActionHandler(Protocol):
    action: Action

    def handles(self, event: TriggerEvent) -> bool:
        """Whether this handler reacts to the event."""
        ...

    def run(self, event: TriggerEvent, ctx: ActionContext) -> ActionResult:
        """Perform the action and return a result."""
        ...


__all__ = ["TriggerEvent", "ActionResult", "ActionContext", "ActionHandler"]
