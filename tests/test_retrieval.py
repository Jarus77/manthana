"""Stage-1 query engine: semantic ranking + coverage (no silent truncation).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime

from manthana.agent.insights import ask
from manthana.agent.llm import MockProvider
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore
from manthana.server.founder import run_query
from manthana.server.llm import ScriptedProvider
from manthana.skills.embed import HashingEmbedder
from manthana.skills.retrieval import Coverage, rank

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _Item:
    def __init__(self, id: str, text: str) -> None:
        self.id = id
        self.text = text


def _comp(
    cid: str, intent: str, *, released: bool = False, actor: str = "e@x.com"
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=cid, actor=actor, surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0,
        task_intent=intent, approach="a", outcome=Outcome.success, released=released,
    )


# ── skills.rank: ordering + coverage ─────────────────────────────────────────
def test_rank_orders_relevant_first_and_truncates() -> None:
    emb = HashingEmbedder()
    items = [
        _Item("a", "postgres database migration and indexes"),
        _Item("b", "react frontend css styling"),
        _Item("c", "postgres query planner tuning"),
    ]
    vecs = {it.id: emb.embed([it.text])[0] for it in items}
    ranked, cov = rank("postgres database", items, vecs, emb, k=2)
    assert ranked[0].id in {"a", "c"}  # postgres items rank above the react one
    assert [it.id for it in ranked] != ["b"]  # react not first
    assert cov == Coverage(matched=3, used=2) and cov.truncated is True


def test_rank_no_truncation_within_budget() -> None:
    emb = HashingEmbedder()
    items = [_Item("a", "alpha"), _Item("b", "beta")]
    vecs = {it.id: emb.embed([it.text])[0] for it in items}
    _, cov = rank("alpha", items, vecs, emb, k=5)
    assert cov.matched == 2 and cov.used == 2 and cov.truncated is False


# ── engineer ask: coverage threaded through ──────────────────────────────────
def test_ask_reports_coverage() -> None:
    store = Store.open_memory()
    for i in range(3):
        store.upsert_compaction(_comp(f"comp-{i}", f"work item {i} postgres"))
    # MockProvider returns the same text for parse ({} → no filter) and narrative.
    result = ask(
        store, "what postgres work did I do?",
        provider=MockProvider("Did postgres work [comp-0]."), embedder=HashingEmbedder(),
    )
    assert result.coverage is not None and result.coverage.matched == 3
    assert result.citations == ["comp-0"]


# ── server: coverage + the index only ever holds released compactions ────────
def test_founder_query_coverage_and_released_only_index() -> None:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=1)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    for i in range(2):
        store.ingest_compaction(
            _comp(f"c{i}", f"intent {i}", released=True), org_id="o1", team_id="t1"
        )
    result = run_query(
        store, config, org_id="o1", query="what happened?",
        provider=ScriptedProvider(["{}", "summary [c0]"]),
    )
    assert result.coverage is not None and result.coverage.matched == 2
    # the index was populated ONLY from released compactions (the only ones the
    # server can ingest) — never unreleased/personal.
    assert set(store.vector_meta("o1")) == {"c0", "c1"}