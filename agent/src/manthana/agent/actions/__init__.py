"""Action dispatcher + handlers for the Manthana agent.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from ..store import Store
from .auto_tag import AUTO_TAG_ACTION, AutoTagHandler
from .base import ActionContext, ActionHandler, ActionResult, TriggerEvent
from .dispatcher import Dispatcher


def default_dispatcher(store: Store) -> Dispatcher:
    """A dispatcher with the v1 built-in handlers registered."""
    return Dispatcher(store, [AutoTagHandler()])


def tag_all(store: Store, *, dispatcher: Dispatcher | None = None, actor: str | None = None) -> int:
    """Fire ``session_closed`` for every stored session; return entries logged."""
    dispatcher = dispatcher or default_dispatcher(store)
    count = 0
    for session in store.list_sessions(limit=100_000):
        event = TriggerEvent(
            type="session_closed", actor=actor or session.actor, session_id=session.id
        )
        count += len(dispatcher.dispatch(event))
    return count


__all__ = [
    "Dispatcher",
    "default_dispatcher",
    "tag_all",
    "TriggerEvent",
    "ActionResult",
    "ActionContext",
    "ActionHandler",
    "AutoTagHandler",
    "AUTO_TAG_ACTION",
]
