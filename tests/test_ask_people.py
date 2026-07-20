"""Person-shaped questions must reach person-shaped data.

The bug these pin: a question naming TWO people was structurally blind. The
filter parser can carry only one actor so it drops the filter when two are named;
the old freshness gate then fired on neither the regex nor the (now absent)
actor, so live activity was never read; and the session drill only triggered when
notes were thin. A founder asking "how do A and B compare" therefore got an
answer built from notes alone, and anyone whose work had not yet been
consolidated into a note was reported as having "no data" — while the wiki's own
front page listed them as active.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore
from manthana.server.ask import _named_actors, ask
from manthana.server.llm import ScriptedProvider
from manthana.skills.embed import HashingEmbedder

_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_EMPTY_FILTER = json.dumps({})
ALICE = "alice@x.com"
BOB = "bob@x.com"


def _comp(cid: str, *, actor: str, project: str, intent: str, days_ago: int = 1):
    at = _NOW - timedelta(days=days_ago)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent=intent,
        approach="did the work",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def _store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.upsert_actor(ALICE, org_id="o1", team_id="t1")
    store.upsert_actor(BOB, org_id="o1", team_id="t1")
    store.ingest_compaction(
        _comp("c1", actor=ALICE, project="ranker", intent="tune the reranker"),
        org_id="o1", team_id="t1",
    )
    store.ingest_compaction(
        _comp("c2", actor=BOB, project="ranker", intent="benchmark the reranker"),
        org_id="o1", team_id="t1",
    )
    return store


def _ask(store: ServerStore, query: str, narrative: str):
    return ask(
        store,
        ServerConfig(jwt_secret="x" * 40, admin_token="adm"),
        org_id="o1",
        query=query,
        provider=ScriptedProvider([_EMPTY_FILTER, narrative]),
        embedder=HashingEmbedder(),
        now=_NOW,
    )


# ── name matching ────────────────────────────────────────────────────────
def test_named_actors_matches_local_part_and_full_id() -> None:
    store = _store()
    assert _named_actors(store, "o1", "do alice and bob overlap?") == [ALICE, BOB]
    assert _named_actors(store, "o1", f"what is {BOB} doing") == [BOB]


def test_named_actors_does_not_fire_on_substrings() -> None:
    # A name embedded in a longer word must not count as a mention, or every
    # question would drag in an unrelated person's sessions.
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.upsert_actor("sam@x.com", org_id="o1", team_id="t1")
    assert _named_actors(store, "o1", "how big is the sample set?") == []
    assert _named_actors(store, "o1", "what is sam doing?") == ["sam@x.com"]


def test_named_actors_empty_when_nobody_is_mentioned() -> None:
    assert _named_actors(_store(), "o1", "how is the ranker doing?") == []


# ── the regression itself ────────────────────────────────────────────────
def test_two_person_question_reaches_both_people_with_no_notes_at_all() -> None:
    # No notes exist, so the ONLY way to answer is live activity + sessions.
    # This is the exact shape that previously returned "no data about <person>".
    store = _store()
    result = _ask(store, "does alice and bob's work correlate?", "Both work on ranker [c1] [c2].")
    assert result.insufficient_data is False
    assert result.drilled is True, "naming people must trigger the session drill"
    assert {"c1", "c2"} <= set(result.compaction_citations)


def test_live_activity_is_consulted_without_any_freshness_wording() -> None:
    # "correlate" matches no freshness word; the old gate would have skipped
    # activity entirely.
    store = _store()
    result = _ask(store, "do alice and bob correlate?", "Alice tunes, Bob benchmarks [c1] [c2].")
    assert result.notes_used == 0
    assert result.compaction_citations, "sessions must back the answer when notes are absent"


def test_a_question_naming_nobody_still_works() -> None:
    store = _store()
    result = _ask(store, "how is the ranker going?", "Work continues on ranker [c1].")
    assert result.insufficient_data is False
