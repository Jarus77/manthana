"""Org-wiki page projections — notes joined to live compaction rollups.

The split under test: freshness ("what is X working on", project status) always
comes from recent compactions, never from a note; durable claims come from
notes. Also covers the benchmark-delta best-effort rule and the fact that no
k-anonymity gate applies on this path.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
    Outcome,
    Surface,
)
from manthana.server import ServerStore
from manthana.server.pages import note_page, org_home, person_page, project_page

_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _comp(
    cid: str,
    *,
    actor: str = "suraj@x.com",
    project: str = "bench",
    days_ago: int = 1,
    intent: str = "run the BIRD benchmark",
    outcome: Outcome = Outcome.success,
) -> EngineeringCompaction:
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
        approach="swept temperature",
        outcome=outcome,
        est_cost_usd=1.0,
        total_tokens=500,
        released=True,
        source="full",
    )


def _note(
    nid: str,
    *,
    kind: NoteKind = NoteKind.decision,
    title: str = "Pin torch 2.4",
    body: str = "2.5 breaks the eval harness.",
    project: str = "bench",
    scope: str | None = None,
    actors: list[str] | None = None,
    evidence: list[str] | None = None,
    status: NoteStatus = NoteStatus.candidate,
    source: NoteSource = NoteSource.ai,
    days_ago: int = 1,
    **kw: object,
) -> KnowledgeNote:
    at = _NOW - timedelta(days=days_ago)
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=kind,
        title=title,
        body=body,
        scope=scope if scope is not None else (f"project:{project}" if project else "org"),
        entities=NoteEntities(projects=[project] if project else []),
        actors=actors if actors is not None else ["suraj@x.com"],
        evidence=evidence if evidence is not None else ["c1"],
        status=status,
        source=source,
        created_at=at,
        updated_at=at,
        **kw,  # type: ignore[arg-type]
    )


def _store(*comps: EngineeringCompaction) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Org")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")
    return store


# ── org home ─────────────────────────────────────────────────────────────
def test_home_feed_projects_people_and_notes() -> None:
    store = _store(
        _comp("c1", project="bench", days_ago=1),
        _comp("c2", project="search", actor="mira@x.com", days_ago=2, intent="ship bm25"),
        _comp("c-old", project="legacy", days_ago=60),  # outside the 7d window
    )
    store.upsert_note(_note("kn-1", kind=NoteKind.decision, days_ago=1))
    store.upsert_note(_note("kn-old", kind=NoteKind.decision, title="Ancient", days_ago=60))

    feed = org_home(store, "o1", now=_NOW)
    assert [p.project for p in feed.projects] == ["bench", "search"]  # 'legacy' aged out
    assert [a.actor for a in feed.people] == ["suraj@x.com", "mira@x.com"]
    assert [n.id for n in feed.new_decisions] == ["kn-1"]  # only this week's
    assert feed.unreviewed == 2  # both candidate notes, regardless of window


def test_benchmark_delta_only_when_predecessor_parses() -> None:
    store = _store(_comp("c1"))
    v1 = _note("kn-1", kind=NoteKind.benchmark, title="BIRD accuracy",
               body="61%", days_ago=5, metric="exec_accuracy", value="61%")
    store.upsert_note(v1)
    v2 = _note("kn-2", kind=NoteKind.benchmark, title="BIRD accuracy",
               body="64%", days_ago=1, metric="exec_accuracy", value="64%").model_copy(
        update={"version": 2, "supersedes": "kn-1"}
    )
    store.supersede_note("kn-1", v2, "o1")

    feed = org_home(store, "o1", now=_NOW)
    assert len(feed.benchmarks) == 1
    delta = feed.benchmarks[0]
    assert delta.previous_value == "61%" and delta.note.value == "64%" and delta.moved

    # A note with no parseable predecessor still shows — the feed is never gated
    # on structured extraction working.
    store.upsert_note(_note("kn-3", kind=NoteKind.benchmark, title="MMLU", body="—", days_ago=1))
    feed = org_home(store, "o1", now=_NOW)
    plain = next(d for d in feed.benchmarks if d.note.id == "kn-3")
    assert plain.previous_value is None and not plain.moved


# ── project page ─────────────────────────────────────────────────────────
def test_project_page_groups_notes_and_includes_org_scoped() -> None:
    store = _store(_comp("c1", project="bench"), _comp("c2", project="other"))
    store.upsert_note(_note("kn-1", kind=NoteKind.decision, project="bench"))
    store.upsert_note(_note("kn-2", kind=NoteKind.gotcha, title="Stale cache", project="bench"))
    # An org-scoped note that names the project applies to it too.
    store.upsert_note(
        _note("kn-3", kind=NoteKind.convention, title="Ruff 100 cols",
              project="bench", scope="org")
    )
    store.upsert_note(_note("kn-other", kind=NoteKind.decision, project="other"))

    page = project_page(store, "o1", "bench", now=_NOW)
    kinds = {kind: [n.id for n in notes] for kind, notes in page.sections}
    assert kinds[NoteKind.decision] == ["kn-1"]
    assert kinds[NoteKind.gotcha] == ["kn-2"]
    assert kinds[NoteKind.convention] == ["kn-3"]
    assert page.note_count == 3  # 'other' project's note excluded
    assert page.rollup is not None and page.rollup.sessions == 1
    assert [c.id for c in page.sessions] == ["c1"]


def test_project_page_excludes_superseded_notes() -> None:
    store = _store(_comp("c1"))
    store.upsert_note(_note("kn-1"))
    v2 = _note("kn-2", body="revised").model_copy(update={"version": 2, "supersedes": "kn-1"})
    store.supersede_note("kn-1", v2, "o1")
    page = project_page(store, "o1", "bench", now=_NOW)
    assert [n.id for _k, notes in page.sections for n in notes] == ["kn-2"]


def test_project_page_quiet_project_has_no_rollup() -> None:
    store = _store(_comp("c1", project="bench", days_ago=90))
    page = project_page(store, "o1", "bench", now=_NOW)
    assert page.rollup is None  # nothing in the window
    assert [c.id for c in page.sessions] == ["c1"]  # but history still browsable


# ── person page (first-class; no k-anon gate) ────────────────────────────
def test_person_page_is_live_activity_plus_their_notes() -> None:
    store = _store(
        _comp("c1", actor="suraj@x.com", project="bench", days_ago=1),
        _comp("c2", actor="suraj@x.com", project="search", days_ago=2, intent="tune reranker"),
        _comp("c3", actor="mira@x.com", project="infra", days_ago=1),
    )
    store.upsert_note(_note("kn-mine", actors=["suraj@x.com"]))
    store.upsert_note(_note("kn-theirs", title="Other", actors=["mira@x.com"]))

    page = person_page(store, "o1", "suraj@x.com", now=_NOW)
    assert page.activity is not None
    # "What is Suraj working on" is answered LIVE, newest intent first.
    assert page.activity.sessions == 2
    assert page.activity.intents[0] == "run the BIRD benchmark"
    assert page.activity.projects == ["bench", "search"]
    # A single contributor is enough — no k-anonymity floor on this path.
    assert [n.id for n in page.notes] == ["kn-mine"]
    assert [c.id for c in page.sessions] == ["c1", "c2"]


# ── note page ────────────────────────────────────────────────────────────
def test_note_page_resolves_evidence_and_disputes() -> None:
    store = _store(_comp("c1"), _comp("c2"))
    store.upsert_note(
        _note("kn-1", evidence=["c1", "c-purged"], status=NoteStatus.disputed,
              disputed_by=["c2"])
    )
    found = note_page(store, "o1", "kn-1")
    assert found is not None
    note, evidence, disputing = found
    assert note.id == "kn-1"
    assert [c.id for c in evidence] == ["c1"]  # unresolvable ids simply drop out
    assert [c.id for c in disputing] == ["c2"]
    assert note_page(store, "o1", "kn-ghost") is None
