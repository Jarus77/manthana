"""The engineer's personal wiki (local dashboard: projects → sessions → search).

The defining property, and what separates it from the org wiki: it sees
EVERYTHING on this laptop — personal-mode and unreleased sessions included — and
it never calls a model to show it. Badges must state honestly what would and
would not leave the machine, computed with the shipped sync gate.

Hermetic like test_dashboard.py: file-backed store in tmp_path, no real ~/.claude
read, MANTHANA_DATA_HOME redirected.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from manthana.agent.dashboard import create_app
from manthana.agent.store import Store
from manthana.schemas import (
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


@pytest.fixture(autouse=True)
def _isolated_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path / "datahome"))


def _session(
    store: Store, sid: str, *, project: str = "bench", mode: Mode = Mode.work
) -> None:
    store.upsert_session(
        Session(
            id=sid,
            actor="eng@example.com",
            surface=Surface.claude_code,
            project=project,
            started_at=_T0,
            turn_count=1,
            mode=mode,
        )
    )
    store.add_turns(
        [Turn(id=f"{sid}-t0", session_id=sid, actor="e", seq=0, role=Role.user, content="hi")]
    )


def _compaction(
    store: Store,
    cid: str,
    sid: str,
    *,
    project: str = "bench",
    intent: str = "run the BIRD benchmark",
    released: bool = False,
    hold: bool = False,
    days: int = 0,
    source: str = "full",  # these fixtures model ENRICHED digests
) -> None:
    at = _T0 + timedelta(days=days)
    store.upsert_compaction(
        EngineeringCompaction(
            id=cid,
            session_id=sid,
            actor="eng@example.com",
            surface=Surface.claude_code,
            project=project,
            started_at=at,
            ended_at=at,
            duration_seconds=60.0,
            task_intent=intent,
            source=source,
            approach="swept temperature, fixed the harness",
            outcome=Outcome.success,
            est_cost_usd=0.5,
            total_tokens=1200,
            tier_used="opus",
            released=released,
            hold=hold,
            friction_points=[
                FrictionPoint(
                category=FrictionCategory.retry, description="flaky eval", turn_refs=["3"]
            )
            ],
            artifacts=["eval.md"],
            files_touched=["eval/harness.py"],
        )
    )


def _build(tmp_path: Path) -> tuple[TestClient, Store]:
    store = Store.open(tmp_path / "manthana.db")
    # No provider is passed: the wiki paths must work with no model available.
    return TestClient(create_app(store, skills_dir=tmp_path)), store


# ── projects home ────────────────────────────────────────────────────────
def test_home_lists_projects_not_a_flat_session_log(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1", project="bench")
    _session(store, "s2", project="search")
    _compaction(store, "c1", "s1", project="bench", days=0)
    _compaction(store, "c2", "s1", project="bench", days=2)
    _compaction(store, "c3", "s2", project="search", intent="ship bm25", days=1)

    body = client.get("/").text
    assert "My projects" in body
    assert "/project/bench" in body and "/project/search" in body
    assert "2400" in body or "2,400" in body  # bench's two sessions' tokens
    assert "ship bm25" in body  # each project's most recent work


def test_home_is_empty_but_helpful_with_no_compactions(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    body = client.get("/").text
    assert "no compactions yet" in body


# ── project page: full compaction cards ──────────────────────────────────
def test_project_page_renders_the_whole_compaction(tmp_path: Path) -> None:
    # The wiki shows the digest itself — not a re-summarization of it — so every
    # substantive field the engineer recorded must be on the page.
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c1", "s1")
    body = client.get("/project/bench").text
    assert "run the BIRD benchmark" in body
    assert "swept temperature, fixed the harness" in body  # approach
    assert "retry: flaky eval" in body  # friction
    assert "eval/harness.py" in body  # files touched
    assert "eval.md" in body  # artifacts
    assert "1,200 tokens" in body
    assert "/drill/c1" in body  # raw turns are one click away


def test_project_page_orders_newest_first(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c-old", "s1", intent="older work", days=0)
    _compaction(store, "c-new", "s1", intent="newer work", days=5)
    body = client.get("/project/bench").text
    assert body.index("newer work") < body.index("older work")


# ── the privacy badges (computed with the shipped sync gate) ─────────────
def test_personal_sessions_are_visible_locally_and_badged_as_local_only(
    tmp_path: Path,
) -> None:
    # This is the whole point of the personal wiki: work that will NEVER sync is
    # still first-class here.
    client, store = _build(tmp_path)
    _session(store, "s-personal", mode=Mode.personal)
    _compaction(store, "c1", "s-personal", intent="my side project")
    body = client.get("/project/bench").text
    assert "my side project" in body  # visible
    assert "personal · stays local" in body


def test_badges_match_release_and_hold_state(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c-rel", "s1", intent="released work", released=True)
    _compaction(store, "c-hold", "s1", intent="held work", hold=True)
    _compaction(store, "c-pending", "s1", intent="pending work")
    body = client.get("/project/bench").text
    assert "released to org" in body
    assert "held · stays local" in body
    assert "local · pending release" in body


def test_orphan_compaction_fails_closed_to_local(tmp_path: Path) -> None:
    # An unknown owning session is excluded by eligible_for_sync; the badge must
    # say the same rather than implying the work would leave the laptop.
    client, store = _build(tmp_path)
    _compaction(store, "c1", "s-missing", released=True)
    body = client.get("/project/bench").text
    assert "stays local" in body


# ── local search: ranked, and free ───────────────────────────────────────
def test_search_ranks_without_calling_a_model(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c1", "s1", intent="fix the retry backoff")
    _compaction(store, "c2", "s1", intent="write release notes")
    # create_app got no provider at all — if this path needed a model it would fail.
    body = client.get("/search?q=retry backoff").text
    assert "fix the retry backoff" in body
    assert "compaction(s)" in body  # coverage statement, never a silent truncation


def test_search_without_a_query_just_shows_the_form(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c1", "s1")
    body = client.get("/search").text
    assert "search your work" in body
    assert "run the BIRD benchmark" not in body  # no results until asked


def test_search_covers_personal_work_too(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s-personal", mode=Mode.personal)
    _compaction(store, "c1", "s-personal", intent="secret side project")
    body = client.get("/search?q=side project").text
    assert "secret side project" in body


# ── navigation ───────────────────────────────────────────────────────────
def test_nav_exposes_both_wiki_and_control_plane(tmp_path: Path) -> None:
    client, store = _build(tmp_path)
    _session(store, "s1")
    _compaction(store, "c1", "s1")
    body = client.get("/").text
    # The wiki (projects, search) and the control plane (sessions, compactions)
    # are both reachable from home — one app, two jobs.
    for href in ("/project/bench", "/search", "/sessions", "/compactions", "/topics"):
        assert href in body, href
