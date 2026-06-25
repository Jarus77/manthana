"""Action dispatcher + auto-tag tests (governance: personal-exclusion, consent,
cooldown, audit).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manthana.agent.actions import AUTO_TAG_ACTION, AutoTagHandler, Dispatcher, default_dispatcher
from manthana.agent.actions.base import ActionContext, ActionResult, TriggerEvent
from manthana.agent.store import Store
from manthana.schemas import (
    Action,
    ActionActor,
    ActionOutcome,
    ActionShape,
    ConsentClass,
    ConsentEntry,
    ConsentState,
    EngineeringCompaction,
    FrictionCategory,
    FrictionPoint,
    Mode,
    Outcome,
    Role,
    Session,
    Surface,
    Turn,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(store: Store, *, mode: Mode = Mode.work) -> None:
    store.upsert_session(
        Session(
            id="s1",
            actor="eng@example.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            mode=mode,
            turn_count=2,
        )
    )
    store.add_turns(
        [
            Turn(id="t0", session_id="s1", actor="e", seq=0, role=Role.user, content="do it"),
            Turn(
                id="t1",
                session_id="s1",
                actor="e",
                seq=1,
                role=Role.assistant,
                tool_name="Edit",
                tool_input={"file_path": "a.py"},
            ),
        ]
    )


def _event() -> TriggerEvent:
    return TriggerEvent(type="session_closed", actor="eng@example.com", session_id="s1")


def test_auto_tag_fires_and_writes_tags() -> None:
    store = Store.open_memory()
    _seed(store)
    entries = default_dispatcher(store).dispatch(_event())
    by_action = {e.action_id: e for e in entries}  # default dispatcher now has >1 handler
    assert by_action["auto_tag_sessions"].outcome is ActionOutcome.fired
    tags = store.get_session("s1").tags  # type: ignore[union-attr]
    assert tags["project"] == "demo"
    assert tags["task_type"] == "implementation"  # Edit tool used
    # audit log persisted
    assert store.list_audit(action_id="auto_tag_sessions")[0].outcome is ActionOutcome.fired


def test_auto_tag_includes_outcome_and_friction_from_compaction() -> None:
    store = Store.open_memory()
    _seed(store)
    store.upsert_compaction(
        EngineeringCompaction(
            id="comp-s1",
            session_id="s1",
            actor="e",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            ended_at=_T0,
            duration_seconds=1.0,
            task_intent="t",
            approach="a",
            outcome=Outcome.success,
            friction_points=[FrictionPoint(category=FrictionCategory.loop, description="x")],
        )
    )
    default_dispatcher(store).dispatch(_event())
    tags = store.get_session("s1").tags  # type: ignore[union-attr]
    assert tags["outcome"] == "success"
    assert tags["friction"] == "loop"


def test_personal_mode_session_is_excluded() -> None:
    store = Store.open_memory()
    _seed(store, mode=Mode.personal)
    entries = default_dispatcher(store).dispatch(_event())
    assert entries[0].outcome is ActionOutcome.suppressed
    assert entries[0].trigger_condition == "personal_mode_excluded"
    assert store.get_session("s1").tags == {}  # type: ignore[union-attr]


def test_consent_opt_out_suppresses() -> None:
    store = Store.open_memory()
    _seed(store)
    store.set_consent(
        ConsentEntry(
            id="c1",
            subject="eng@example.com",
            action_category="auto_tag_sessions",
            state=ConsentState.opt_out,
            set_at=_T0,
        )
    )
    entries = default_dispatcher(store).dispatch(_event())
    assert entries[0].outcome is ActionOutcome.suppressed
    assert entries[0].trigger_condition == "consent_opt_out"


def test_cooldown_suppresses_second_fire() -> None:
    store = Store.open_memory()
    _seed(store)
    cooled = Action(
        id="cooled",
        name="Cooled",
        shape=ActionShape.warn,
        actor=ActionActor.engineer,
        consent_class=ConsentClass.opt_out,
        cooldown_seconds=3600,
    )

    class _Handler:
        action = cooled

        def handles(self, event: TriggerEvent) -> bool:
            return event.type == "session_closed"

        def run(self, event: TriggerEvent, ctx: ActionContext) -> ActionResult:
            return ActionResult(ActionOutcome.fired, "fired")

    disp = Dispatcher(store, [_Handler()])
    first = disp.dispatch(_event(), now=_T0)
    second = disp.dispatch(_event(), now=_T0 + timedelta(minutes=10))
    assert first[0].outcome is ActionOutcome.fired
    assert second[0].outcome is ActionOutcome.suppressed
    assert second[0].trigger_condition == "cooldown"


def test_auto_tag_action_metadata() -> None:
    assert AUTO_TAG_ACTION.shape is ActionShape.write
    assert AUTO_TAG_ACTION.consent_class is ConsentClass.silent
    assert AutoTagHandler().handles(TriggerEvent(type="session_start", actor="e")) is False


# ── Loop detection (Phase B) ────────────────────────────────────────────────
def _err_turn(i: int, tool: str = "Bash") -> Turn:
    return Turn(
        id=f"e{i}", session_id="s1", actor="e", seq=i, role=Role.tool,
        tool_name=tool, tool_output="x", error="failed",
    )


def test_detect_loops_flags_repeated_tool_failures() -> None:
    from manthana.agent.actions.loops import detect_loops

    signals = detect_loops([_err_turn(i) for i in range(3)])
    assert len(signals) == 1
    s = signals[0]
    assert s.source == "tool_errors" and s.label == "Bash" and s.count == 3
    assert s.turn_range == (0, 2)


def test_detect_loops_no_false_positive() -> None:
    from manthana.agent.actions.loops import detect_loops

    assert detect_loops([_err_turn(0), _err_turn(1)]) == []  # 2 < threshold 3
    ok = [
        Turn(id=f"o{i}", session_id="s1", actor="e", seq=i, role=Role.tool,
             tool_name="Bash", tool_output="ok")
        for i in range(5)
    ]
    assert detect_loops(ok) == []  # no errors → no loop


def test_detect_loops_from_loop_friction() -> None:
    from manthana.agent.actions.loops import detect_loops

    comp = EngineeringCompaction(
        id="comp-s1", session_id="s1", actor="e", surface=Surface.claude_code, project="demo",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent="t", approach="a",
        outcome=Outcome.partial,
        friction_points=[FrictionPoint(
            category=FrictionCategory.loop, description="stuck retrying the migration",
            turn_refs=["5", "7"])],
    )
    signals = detect_loops([], comp)
    assert len(signals) == 1 and signals[0].source == "friction:loop"


def test_loop_warning_fires_via_dispatcher() -> None:
    store = Store.open_memory()
    _seed(store)
    store.add_turns([_err_turn(i) for i in range(10, 13)])  # 3 Bash failures on s1
    entries = default_dispatcher(store).dispatch(_event())
    warn = {e.action_id: e for e in entries}["loop_warning"]
    assert warn.outcome is ActionOutcome.fired
    assert warn.details["session_id"] == "s1" and warn.details["signal_count"] >= 1


def test_loop_warning_suppressed_on_clean_session() -> None:
    store = Store.open_memory()
    _seed(store)
    entries = default_dispatcher(store).dispatch(_event())
    warn = {e.action_id: e for e in entries}["loop_warning"]
    assert warn.outcome is ActionOutcome.suppressed and warn.trigger_condition == "no_loop"


def test_loop_warning_action_metadata() -> None:
    from manthana.agent.actions import LOOP_WARNING_ACTION

    assert LOOP_WARNING_ACTION.shape is ActionShape.warn
    assert LOOP_WARNING_ACTION.consent_class is ConsentClass.opt_out
