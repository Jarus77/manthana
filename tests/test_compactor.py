"""Compactor tests — the agent's compaction is deterministic and model-free.

The agent must NEVER invoke an LLM (a ``claude -p`` call wrote a transcript that
the watcher captured and compacted, recursing without bound). These tests pin the
deterministic contract: source="pending", grounded task_intent, empty qualitative
fields, and every derived/counted field intact.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.agent.compact import compact_session
from manthana.agent.compactor import Compactor
from manthana.agent.store import Store
from manthana.schemas import (
    EngineeringCompaction,
    Outcome,
    Role,
    Session,
    Surface,
    Turn,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _session() -> Session:
    return Session(
        id="s1",
        actor="eng@example.com",
        surface=Surface.claude_code,
        project="demo",
        started_at=_T0,
        ended_at=_T0 + timedelta(seconds=300),
        turn_count=2,
    )


def _turns() -> list[Turn]:
    return [
        Turn(id="t0", session_id="s1", actor="e", seq=0, role=Role.user, content="fix the parser"),
        Turn(
            id="t1",
            session_id="s1",
            actor="e",
            seq=1,
            role=Role.assistant,
            content="done",
            model="claude-opus-4-8",
            tokens_in=1_000_000,
            tokens_out=0,
        ),
    ]


def test_compact_produces_deterministic_pending_compaction() -> None:
    comp = Compactor().compact(_session(), _turns())
    assert isinstance(comp, EngineeringCompaction)
    assert comp.kind == "engineering"
    # Qualitative fields are UNWRITTEN — the server fills them on its metered key.
    assert comp.source == "pending"
    assert comp.approach == ""
    assert comp.outcome is Outcome.partial
    assert comp.artifacts == []
    assert comp.friction_points == []
    assert comp.languages == [] and comp.frameworks == []
    assert comp.prs_opened == [] and comp.tests_added == []
    assert comp.dead_end_branches == []
    assert comp.reusable_pattern is False
    # task_intent is grounded in the first user turn, not inferred by a model.
    assert comp.task_intent == "fix the parser"
    # Deterministic fields are all present and unchanged.
    assert comp.id == "comp-s1"
    assert comp.session_id == "s1"
    assert comp.actor == "eng@example.com"
    assert comp.surface is Surface.claude_code
    assert comp.project == "demo"
    assert comp.tier_used == "opus"
    assert comp.est_cost_usd == 15.0
    assert comp.total_tokens == 1_000_000
    assert comp.duration_seconds == 300.0
    assert comp.prompt_version == "v2"
    assert comp.created_at is not None
    # Nothing was spent building it.
    assert comp.call_cost_usd is None


def test_compact_never_invokes_a_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The regression guard for the recursion bug: no subprocess, no provider.

    Anything shelling out (``claude -p`` / ``codex exec``) would create a new
    transcript that the watcher then compacts — the unbounded loop we removed.
    """
    import subprocess

    from manthana.agent.llm import provider as prov

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("compaction must never shell out to a model CLI")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(prov, "default_provider", _boom)
    comp = Compactor().compact(_session(), _turns())
    assert comp.source == "pending"
    # The compactor holds no provider at all — the seam is gone, not just unused.
    assert not hasattr(Compactor(), "provider")


def test_native_summary_carried_when_present() -> None:
    comp = Compactor().compact(_session(), _turns(), native_summary="PRIOR STATE: did X")
    assert comp.native_summary == "PRIOR STATE: did X"
    # It's carried for the server, not interpreted here — source stays "pending".
    assert comp.source == "pending"


def test_native_summary_none_when_absent() -> None:
    assert Compactor().compact(_session(), _turns()).native_summary is None


def test_compact_session_persists_to_store() -> None:
    store = Store.open_memory()
    store.upsert_session(_session())
    store.add_turns(_turns())
    comp = compact_session(store, "s1")
    assert comp is not None
    fetched = store.get_compaction("comp-s1")
    assert isinstance(fetched, EngineeringCompaction)
    assert fetched.source == "pending"
    assert fetched.task_intent == "fix the parser"


def test_compact_session_unknown_returns_none() -> None:
    assert compact_session(Store.open_memory(), "ghost") is None


# ── files_touched stays complete: it comes from real tool calls, not a model ──
def test_files_touched_deterministic_from_tool_calls() -> None:
    turns = [
        Turn(id="t0", session_id="s1", actor="e", seq=0, role=Role.assistant,
             tool_name="Edit", tool_input={"file_path": "/repo/src/app.py"}),
        Turn(id="t1", session_id="s1", actor="e", seq=1, role=Role.assistant,
             tool_name="Read", tool_input={"file_path": "/repo/data/train.csv"}),
        # a repeat must not duplicate, and a non-file tool contributes nothing
        Turn(id="t2", session_id="s1", actor="e", seq=2, role=Role.assistant,
             tool_name="Edit", tool_input={"file_path": "/repo/src/app.py"}),
        Turn(id="t3", session_id="s1", actor="e", seq=3, role=Role.assistant,
             tool_name="Bash", tool_input={"command": "ls /repo/secret.py"}),
    ]
    comp = Compactor().compact(_session(), turns)
    assert comp.files_touched == ["/repo/src/app.py", "/repo/data/train.csv"]


# ── the prompt template survives for server-side enrichment ──────────────────
def test_serialize_turns_keeps_head_and_tail_on_long_sessions() -> None:
    from manthana.server.enrich.prompt import serialize_turns

    turns = [
        Turn(id=f"t{i}", session_id="s", actor="e", seq=i, role=Role.user, content=f"turn-{i}")
        for i in range(600)
    ]
    data = json.loads(serialize_turns(turns))
    seqs = {d["seq"] for d in data}
    assert 0 in seqs and 599 in seqs  # head AND the ending are present
    assert -1 in seqs  # elision marker
    assert 300 not in seqs  # a middle turn was elided
    assert len(data) == 250 + 1 + 150
