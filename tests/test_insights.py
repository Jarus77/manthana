"""Engineer self-query: structural insights (no LLM) + grounded ask.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manthana.agent.insights import (
    INSUFFICIENT,
    _since_cutoff,
    ask,
    structural_insights,
)
from manthana.agent.llm import MockProvider
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Outcome, Role, Session, Surface, Turn


def _session(store: Store, sid: str, project: str, started: datetime, turns: int = 1) -> None:
    store.upsert_session(
        Session(
            id=sid, actor="e@x.com", surface=Surface.claude_code, project=project,
            started_at=started, turn_count=turns,
        )
    )
    store.add_turns(
        [Turn(id=f"{sid}-t{i}", session_id=sid, actor="e", seq=i, role=Role.user, content="hi")
         for i in range(turns)]
    )


def _comp(store: Store, cid: str, sid: str, project: str, intent: str,
          outcome: Outcome = Outcome.success) -> None:
    t = datetime(2026, 6, 1, tzinfo=UTC)
    store.upsert_compaction(
        EngineeringCompaction(
            id=cid, session_id=sid, actor="e@x.com", surface=Surface.claude_code, project=project,
            started_at=t, ended_at=t, duration_seconds=1.0, task_intent=intent, approach="a",
            outcome=outcome, est_cost_usd=0.5, tier_used="opus", released=False,
        )
    )


class _Scripted:
    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._r = list(responses)

    def complete(self, prompt: str) -> str:
        return self._r.pop(0) if self._r else ""


# ── _since_cutoff parsing ───────────────────────────────────────────────────
def test_since_cutoff_parsing() -> None:
    now = datetime(2026, 6, 20, tzinfo=UTC)
    assert _since_cutoff("7d", now=now) == now - timedelta(days=7)
    assert _since_cutoff("2w", now=now) == now - timedelta(weeks=2)
    assert _since_cutoff("12h", now=now) == now - timedelta(hours=12)
    assert _since_cutoff(None, now=now) is None
    assert _since_cutoff("garbage", now=now) is None
    assert _since_cutoff("2026-06-01", now=now) == datetime(2026, 6, 1, tzinfo=UTC)


# ── structural insights (no provider / no tokens) ───────────────────────────
def test_structural_by_project_and_outcome() -> None:
    store = Store.open_memory()
    t = datetime(2026, 6, 1, tzinfo=UTC)
    _session(store, "s1", "alpha", t)
    _session(store, "s2", "alpha", t)
    _session(store, "s3", "beta", t)
    _comp(store, "c1", "s1", "alpha", "fix", Outcome.success)
    _comp(store, "c2", "s3", "beta", "build", Outcome.partial)

    s = structural_insights(store)
    assert s.session_count == 3
    assert s.compaction_count == 2
    assert s.by_project == {"alpha": 2, "beta": 1}  # sorted desc by count
    assert s.by_outcome == {"success": 1, "partial": 1}
    assert s.est_cost_usd >= 0.0


def test_structural_since_filters_old_sessions() -> None:
    store = Store.open_memory()
    now = datetime.now(UTC)
    _session(store, "old", "alpha", now - timedelta(days=40))
    _session(store, "new", "beta", now - timedelta(days=1))
    s = structural_insights(store, since="7d")
    assert s.session_count == 1
    assert s.by_project == {"beta": 1}


# ── ask (grounded over compactions) ─────────────────────────────────────────
def test_ask_grounded_cites_compaction() -> None:
    store = Store.open_memory()
    _session(store, "s1", "alpha", datetime(2026, 6, 1, tzinfo=UTC))
    _comp(store, "comp-1", "s1", "alpha", "wrote the parser")
    result = ask(store, "what did I do?", provider=MockProvider("Built the parser [comp-1]."))
    assert result.grounded is True
    assert result.citations == ["comp-1"]


def test_ask_without_compactions_is_insufficient() -> None:
    store = Store.open_memory()
    _session(store, "s1", "alpha", datetime(2026, 6, 1, tzinfo=UTC))  # session but no compaction
    result = ask(store, "what did I do?", provider=MockProvider("anything"))
    assert result.narrative == INSUFFICIENT
    assert result.grounded is False and result.citations == []


def test_ask_source_filter_full_only() -> None:
    store = Store.open_memory()
    _session(store, "s1", "alpha", datetime(2026, 6, 1, tzinfo=UTC))
    _session(store, "s2", "alpha", datetime(2026, 6, 1, tzinfo=UTC))
    _comp(store, "comp-1", "s1", "alpha", "full work")  # source defaults to "full"
    cheap = store.get_compaction("comp-1")  # build a claude_summary one for s2
    assert cheap is not None
    summary_comp = cheap.model_copy(
        update={"id": "comp-2", "session_id": "s2", "source": "claude_summary"}
    )
    store.upsert_compaction(summary_comp)
    # default (all sources) sees both; full-only excludes the summary-derived one
    narr = MockProvider("see [comp-1] and [comp-2]")
    all_src = ask(store, "what did I do?", provider=narr)
    full_only = ask(store, "x", provider=MockProvider("see [comp-1] and [comp-2]"), source="full")
    assert set(all_src.citations) == {"comp-1", "comp-2"}
    assert full_only.citations == ["comp-1"]  # comp-2 (claude_summary) filtered out


def test_ask_applies_parsed_project_filter() -> None:
    store = Store.open_memory()
    _session(store, "s1", "scribe", datetime(2026, 6, 1, tzinfo=UTC))
    _session(store, "s2", "other", datetime(2026, 6, 1, tzinfo=UTC))
    _comp(store, "c-scribe", "s1", "scribe", "scribe work")
    _comp(store, "c-other", "s2", "other", "other work")
    # parse call returns a project filter; narrative call cites the scribe compaction
    provider = _Scripted(['{"project": "scribe"}', "Did the scribe work [c-scribe]."])
    result = ask(store, "what did I do on scribe?", provider=provider)  # type: ignore[arg-type]
    assert result.filtered_to == {"project": "scribe"}
    assert result.citations == ["c-scribe"]  # only the scribe compaction was in scope
