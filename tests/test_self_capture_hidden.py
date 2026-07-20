"""Manthana's own compaction calls must not appear as work on the wiki.

`purge.is_structural_junk` already existed but was wired only into the admin
purge path, so a digest describing the compactor itself — "Summarize a single
engineering session into a structured JSON digest" — showed up on the front page
as a colleague's project. These tests pin it as a DISPLAY filter on the wiki
projections, and pin the conservatism that makes hiding safe: a genuine session
ABOUT the compactor keeps its files and project, so it is never caught.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_engineer_token
from manthana.server.llm import MockProvider
from manthana.server.pages import discovery_feed
from manthana.server.storage import InMemoryObjectStore

_NOW = datetime.now(UTC)
ENG = "suraj@x.com"
_JUNK_INTENT = "Summarize a single engineering session into a structured JSON digest"


def _comp(
    cid: str,
    *,
    intent: str,
    project: str = "bench",
    outcome: Outcome = Outcome.success,
    files: list[str] | None = None,
) -> EngineeringCompaction:
    at = _NOW - timedelta(hours=1)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=ENG,
        surface=Surface.claude_code,
        project=project,
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent=intent,
        approach="ran the compactor" if files is None else "edited the compactor",
        outcome=outcome,
        released=True,
        source="full",
        files_touched=files or [],
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.upsert_actor(ENG, org_id="o1", team_id="t1")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


def test_a_compaction_of_manthana_itself_is_hidden_from_the_feed() -> None:
    store = _store(
        _comp("real", intent="ship the reranker"),
        # The shape seen in production: no files, no project, abandoned.
        _comp("junk", intent=_JUNK_INTENT, project="unknown", outcome=Outcome.abandoned),
    )
    feed = discovery_feed(store, "o1")
    assert [s.id for s in feed.stream] == ["real"]


def test_real_work_ON_the_compactor_is_never_hidden() -> None:
    """The conservatism that makes a display filter defensible: an engineer
    genuinely improving the compactor touches files and carries a project, so
    the text signal alone can never hide their work."""
    store = _store(
        _comp(
            "genuine",
            intent="fix a bug in the Manthana compactor's JSON digest",
            project="manthana",
            files=["agent/compactor/compactor.py"],
        )
    )
    assert [s.id for s in discovery_feed(store, "o1").stream] == ["genuine"]


def test_junk_is_hidden_from_the_org_wide_session_browser_too() -> None:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = _store(
        _comp("real", intent="ship the reranker"),
        _comp("junk", intent=_JUNK_INTENT, project="unknown", outcome=Outcome.abandoned),
    )
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    client.post(
        "/ui/api/wiki/login",
        json={"token": issue_engineer_token(config.jwt_secret, org_id="o1", actor=ENG)},
    )
    ids = {s["id"] for s in client.get("/ui/api/wiki/sessions").json()["items"]}
    assert ids == {"real"}


def test_hidden_is_not_deleted() -> None:
    """Hiding is the weaker act a heuristic justifies. The row stays in the
    store and stays addressable, so nothing is lost and a false positive costs
    visibility rather than data."""
    store = _store(
        _comp("junk", intent=_JUNK_INTENT, project="unknown", outcome=Outcome.abandoned)
    )
    assert store.get_compaction("junk", "o1") is not None
    assert len(store.query_compactions(org_id="o1")) == 1
