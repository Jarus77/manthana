"""Shared zero-LLM page projections (the live half of the note-vs-rollup split).

Both wikis compute "state of project X" / "what is <person> working on" from
these pure functions over compaction lists — never from persisted notes — so the
numbers are identical on the laptop and the server and can't go stale.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manthana.schemas import (
    EngineeringCompaction,
    FrictionCategory,
    FrictionPoint,
    Outcome,
    Surface,
)
from manthana.skills.projections import (
    activity_rollup,
    filter_since,
    project_rollups,
    session_card,
    session_cards,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _comp(
    cid: str,
    *,
    project: str = "search",
    actor: str = "suraj@x.com",
    at: datetime = _T0,
    outcome: Outcome = Outcome.success,
    intent: str = "tune the reranker",
    cost: float = 0.5,
    tokens: int = 1000,
    source: str = "full",  # these fixtures model ENRICHED digests
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        source=source,
        started_at=at,
        ended_at=at + timedelta(hours=1),
        duration_seconds=3600.0,
        task_intent=intent,
        approach="iterated on thresholds",
        outcome=outcome,
        est_cost_usd=cost,
        total_tokens=tokens,
        friction_points=[
            FrictionPoint(
                category=FrictionCategory.retry, description="flaky eval", turn_refs=["3"]
            )
        ],
        artifacts=["eval.md"],
        files_touched=["rerank.py"],
        languages=["python"],
        released=True,
    )


def test_project_rollups_groups_and_orders() -> None:
    comps = [
        _comp("c1", project="search", at=_T0, outcome=Outcome.partial),
        _comp("c2", project="search", actor="mira@x.com", at=_T0 + timedelta(days=2),
              intent="ship bm25 fallback"),
        _comp("c3", project="infra", at=_T0 + timedelta(days=1)),
    ]
    rollups = project_rollups(comps)
    assert [r.project for r in rollups] == ["search", "infra"]  # most recently active first
    search = rollups[0]
    assert search.sessions == 2
    assert search.actors == ["mira@x.com", "suraj@x.com"]
    assert search.outcome_mix == {"success": 1, "partial": 1}
    assert search.top_intent == "ship bm25 fallback"  # most recent session's intent
    assert search.est_cost_usd == 1.0
    assert search.total_tokens == 2000


def test_session_card_projects_full_compaction() -> None:
    card = session_card(_comp("c1"))
    # The wiki renders the digest itself — every substantive field must survive.
    assert card.task_intent == "tune the reranker"
    assert card.approach == "iterated on thresholds"
    assert card.outcome == "success"
    assert card.friction == ["retry: flaky eval"]
    assert card.files_touched == ["rerank.py"]
    assert card.artifacts == ["eval.md"]
    assert card.languages == ["python"]
    assert card.est_cost_usd == 0.5 and card.total_tokens == 1000
    assert card.released is True and card.hold is False


def test_session_cards_newest_first() -> None:
    cards = session_cards([_comp("c1", at=_T0), _comp("c2", at=_T0 + timedelta(days=1))])
    assert [c.id for c in cards] == ["c2", "c1"]


def test_activity_rollup_is_the_live_person_answer() -> None:
    comps = [
        _comp("c1", actor="suraj@x.com", project="search", at=_T0, intent="tune the reranker"),
        _comp("c2", actor="suraj@x.com", project="bench", at=_T0 + timedelta(days=1),
              intent="run BIRD benchmark", outcome=Outcome.partial),
        _comp("c3", actor="mira@x.com", project="infra", at=_T0 + timedelta(days=2)),
    ]
    acts = activity_rollup(comps)
    assert [a.actor for a in acts] == ["mira@x.com", "suraj@x.com"]  # recency order
    suraj = acts[1]
    assert suraj.sessions == 2
    assert suraj.projects == ["bench", "search"]  # most recent project first
    assert suraj.intents[0] == "run BIRD benchmark"
    assert suraj.outcome_mix == {"success": 1, "partial": 1}


def test_filter_since() -> None:
    comps = [_comp("c1", at=_T0), _comp("c2", at=_T0 + timedelta(days=5))]
    assert [c.id for c in filter_since(comps, _T0 + timedelta(days=1))] == ["c2"]
    assert len(filter_since(comps, None)) == 2


# ── junk project slugs ───────────────────────────────────────────────────
def test_junk_project_slugs_are_not_projects() -> None:
    """`unknown`/`project`/empty are the compactor's fallback when a session had
    no repo context. They are an ABSENCE of a project, and listing them collects
    unrelated work from everyone into one bucket that reads like a real effort."""
    from manthana.skills.projections import is_real_project, project_rollups

    assert is_real_project("rel-bench") is True
    for junk in ("", "unknown", "project", "Projects", " TMP ", "untitled"):
        assert is_real_project(junk) is False, junk

    comps = [
        _comp("c1", project="rel-bench"),
        _comp("c2", project="unknown"),
        _comp("c3", project="project"),
    ]
    assert [r.project for r in project_rollups(comps)] == ["rel-bench"]


def test_actor_activity_drops_junk_projects() -> None:
    from manthana.skills.projections import activity_rollup

    comps = [_comp("c1", project="rel-bench"), _comp("c2", project="unknown")]
    assert activity_rollup(comps)[0].projects == ["rel-bench"]


# ── pending digests carry raw prompts, not summaries ─────────────────────
def test_top_intent_skips_pending_digests() -> None:
    """A pending digest's `task_intent` is the engineer's literal first prompt.
    Quoted as a project's "latest work" it reads as gibberish and breaks
    mid-word, so the projection reaches past it to the newest real summary."""
    from manthana.skills.projections import project_rollups

    comps = [
        _comp("c-new", at=_T0 + timedelta(hours=2), intent="raw pasted prompt", source="pending"),
        _comp("c-old", at=_T0, intent="tune the reranker", source="full"),
    ]
    assert project_rollups(comps)[0].top_intent == "tune the reranker"


def test_top_intent_is_empty_when_everything_is_pending() -> None:
    # Empty renders honestly as an em dash; a raw prompt would not.
    from manthana.skills.projections import project_rollups

    comps = [_comp("c1", intent="raw pasted prompt", source="pending")]
    assert project_rollups(comps)[0].top_intent == ""


def test_activity_intents_exclude_pending() -> None:
    from manthana.skills.projections import activity_rollup

    comps = [
        _comp("c-new", at=_T0 + timedelta(hours=2), intent="raw prompt", source="pending"),
        _comp("c-old", at=_T0, intent="tune the reranker", source="full"),
    ]
    assert activity_rollup(comps)[0].intents == ["tune the reranker"]


def test_activity_intents_empty_when_all_pending() -> None:
    from manthana.skills.projections import activity_rollup

    assert activity_rollup([_comp("c1", intent="raw", source="pending")])[0].intents == []
