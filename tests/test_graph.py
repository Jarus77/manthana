"""Cross-entity edges (``server/graph.py``) — the wiki's "who works with whom".

Pure functions over lists, so these tests need no store and no app: they pin the
signal weighting, the evidence each edge carries, and the noise filters that
keep a shared README from reading as collaboration.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    Outcome,
    Surface,
)
from manthana.server.graph import (
    VIA_CAP,
    WEIGHT_FILE,
    WEIGHT_NOTE,
    WEIGHT_PROJECT,
    project_neighbors,
    related_people,
    session_related,
)

_NOW = datetime.now(UTC)


def _comp(
    cid: str,
    *,
    actor: str,
    project: str = "bench",
    files: list[str] | None = None,
    days_ago: int = 1,
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
        task_intent="work",
        approach="did the thing",
        outcome=Outcome.success,
        released=True,
        source="full",
        files_touched=files or [],
    )


def _note(nid: str, *, actors: list[str], evidence: list[str] | None = None) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=NoteKind.decision,
        title="a claim",
        body="body",
        scope="project:bench",
        entities=NoteEntities(projects=["bench"]),
        actors=actors,
        evidence=evidence or [],
        created_at=_NOW,
        updated_at=_NOW,
    )


# ── related_people ───────────────────────────────────────────────────────
def test_shared_project_links_two_people_and_explains_why() -> None:
    comps = [_comp("c1", actor="a@x.com"), _comp("c2", actor="b@x.com")]
    edges = related_people(comps, [], "a@x.com")
    assert [e.actor for e in edges] == ["b@x.com"]
    assert edges[0].via_projects == ["bench"]  # the UI can always say why
    assert edges[0].weight == WEIGHT_PROJECT
    assert edges[0].shared_projects == 1


def test_people_on_different_projects_are_not_linked() -> None:
    comps = [_comp("c1", actor="a@x.com"), _comp("c2", actor="b@x.com", project="search")]
    assert related_people(comps, [], "a@x.com") == []


def test_co_citation_in_a_note_links_people_across_projects() -> None:
    # The cross-team case: they never shared a project, but one durable claim
    # was drawn from both their sessions.
    comps = [_comp("c1", actor="a@x.com"), _comp("c2", actor="b@x.com", project="design")]
    notes = [_note("kn-1", actors=["a@x.com", "b@x.com"])]
    edges = related_people(comps, notes, "a@x.com")
    assert [e.actor for e in edges] == ["b@x.com"]
    assert edges[0].weight == WEIGHT_NOTE
    # The edge must be explainable in words, not just by id.
    assert [(r.id, r.title) for r in edges[0].via_notes] == [("kn-1", "a claim")]


def test_shared_files_link_people_and_rank_below_shared_projects() -> None:
    comps = [
        _comp("c1", actor="a@x.com", project="bench", files=["src/core.py"]),
        _comp("c2", actor="b@x.com", project="bench"),  # shared project only
        _comp("c3", actor="c@x.com", project="other", files=["src/core.py"]),  # files only
    ]
    edges = related_people(comps, [], "a@x.com")
    assert [e.actor for e in edges] == ["b@x.com", "c@x.com"]  # project outranks files
    assert edges[1].via_files == ["src/core.py"]
    assert edges[1].weight == WEIGHT_FILE


def test_generic_files_do_not_manufacture_collaboration() -> None:
    comps = [
        _comp("c1", actor="a@x.com", project="bench", files=["README.md", "package.json"]),
        _comp("c2", actor="b@x.com", project="other", files=["README.md", "package.json"]),
    ]
    assert related_people(comps, [], "a@x.com") == []


def test_signals_add_up_and_edges_are_stable() -> None:
    comps = [
        _comp("c1", actor="a@x.com", files=["src/core.py"]),
        _comp("c2", actor="b@x.com", files=["src/core.py"]),
    ]
    notes = [_note("kn-1", actors=["a@x.com", "b@x.com"])]
    edge = related_people(comps, notes, "a@x.com")[0]
    assert edge.weight == WEIGHT_PROJECT + WEIGHT_NOTE + WEIGHT_FILE
    # Same inputs, same order — the panel must not reshuffle between reloads.
    assert related_people(comps, notes, "a@x.com") == related_people(comps, notes, "a@x.com")


def test_shared_counts_are_true_totals_not_the_capped_display_sample() -> None:
    # The panel renders at most VIA_CAP items but must still say how many there
    # are — under-reporting the strongest links is worse than not showing them.
    comps = [_comp("c1", actor="a@x.com"), _comp("c2", actor="b@x.com")]
    notes = [_note(f"kn-{i}", actors=["a@x.com", "b@x.com"]) for i in range(VIA_CAP + 3)]
    edge = related_people(comps, notes, "a@x.com")[0]
    assert len(edge.via_notes) == VIA_CAP  # display is capped…
    assert edge.shared_notes == VIA_CAP + 3  # …the count is not
    assert edge.weight == WEIGHT_PROJECT + WEIGHT_NOTE * (VIA_CAP + 3)


def test_unknown_actor_has_no_edges() -> None:
    assert related_people([_comp("c1", actor="a@x.com")], [], "nobody@x.com") == []


def test_actor_is_never_related_to_themselves() -> None:
    comps = [_comp("c1", actor="a@x.com"), _comp("c2", actor="a@x.com")]
    assert related_people(comps, [_note("kn-1", actors=["a@x.com"])], "a@x.com") == []


# ── project_neighbors ────────────────────────────────────────────────────
def test_project_neighbors_come_from_shared_contributors() -> None:
    comps = [
        _comp("c1", actor="a@x.com", project="bench"),
        _comp("c2", actor="a@x.com", project="search"),
        _comp("c3", actor="z@x.com", project="unrelated"),
    ]
    edges = project_neighbors(comps, "bench")
    assert [e.project for e in edges] == ["search"]
    assert edges[0].via_actors == ["a@x.com"]


def test_project_with_no_sessions_has_no_neighbors() -> None:
    assert project_neighbors([_comp("c1", actor="a@x.com")], "ghost") == []


# ── session_related ──────────────────────────────────────────────────────
def test_session_links_to_the_notes_it_produced_and_its_neighbours() -> None:
    subject = _comp("c1", actor="a@x.com", project="bench")
    org = [
        subject,
        _comp("c2", actor="a@x.com", project="bench"),  # same person
        _comp("c3", actor="b@x.com", project="bench"),  # same project, someone else
    ]
    notes = [_note("kn-1", actors=["a@x.com"], evidence=["c1"])]
    links = session_related(subject, notes, org)
    assert [n.id for n in links.notes] == ["kn-1"]
    assert [c.id for c in links.same_actor] == ["c2"]
    assert [c.id for c in links.same_project] == ["c3"]
    assert subject.id not in {c.id for c in links.same_actor + links.same_project}
