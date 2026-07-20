"""Cross-entity edges — who works with whom, and why.

The wiki's discovery promise is that no page is a dead end: from a person you
can reach the people they share work with, from a project its sibling projects,
from a session the knowledge it produced. That needs a notion of "related",
computed from what the org already recorded rather than from a model.

Three co-occurrence signals, weighted by how much they imply real collaboration:

  * **shared project** — both released sessions against the same project slug in
    the window. Strongest: it is the org's own unit of work.
  * **co-cited in a note** — both appear in a KnowledgeNote's ``actors``, i.e.
    the consolidator drew one durable claim from both their sessions.
  * **shared files** — both touched the same path. Weakest and noisiest (a
    shared README means little), but it is the only signal that crosses project
    boundaries, which is exactly the cross-team link a founder wants to see.

Every edge carries its ``via_*`` evidence so the UI can render *why* two people
are connected — an unexplained edge is a claim the reader cannot check.

Pure functions over already-fetched lists, in the style of
``skills.projections``: no store, no I/O, no LLM. Callers pass the compactions
and notes they loaded for the page anyway, so an edge costs no extra query.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from manthana.schemas import KnowledgeNote

#: Signal weights. Deliberately coarse — the ordering is what matters, and a
#: tuned score would imply a precision this data does not have.
WEIGHT_PROJECT = 3
WEIGHT_NOTE = 2
WEIGHT_FILE = 1

#: How many "via" items to keep per edge. The panel explains an edge; it does
#: not enumerate it.
VIA_CAP = 5

#: Paths this common say nothing about collaboration.
_GENERIC_FILES = {
    "readme.md",
    "readme",
    "changelog.md",
    "package.json",
    "package-lock.json",
    "uv.lock",
    "poetry.lock",
    "pyproject.toml",
    ".gitignore",
}


def _is_generic(path: str) -> bool:
    return path.rsplit("/", 1)[-1].lower() in _GENERIC_FILES


@dataclass(frozen=True)
class NoteRef:
    """A note referenced from an edge — id AND title.

    The title is not decoration: an edge explained as "kn-d8f2c98adc69" tells a
    reader nothing they can act on, so the id alone would make the panel
    unreadable exactly where it is trying to justify itself.
    """

    id: str
    title: str


@dataclass(frozen=True)
class PersonEdge:
    """One collaborator link, with the evidence that produced it.

    The ``via_*`` lists are capped at ``VIA_CAP`` for display, so they are NOT
    the counts: the ``shared_*`` fields carry the true totals. A panel that read
    ``len(via_notes)`` would silently under-report every strong link — the exact
    edges a reader most wants to trust — as "5 shared notes".

    No rendered ``reason`` string lives here: how an edge is phrased is the
    client's business, and this shape is also consumed by non-browser callers.
    """

    actor: str  # the person on the OTHER end (the subject is implied by the caller)
    weight: int
    shared_projects: int = 0
    shared_notes: int = 0
    shared_files: int = 0
    via_projects: list[str] = field(default_factory=list)
    via_notes: list[NoteRef] = field(default_factory=list)
    via_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectEdge:
    """A sibling project, linked by the people who worked on both."""

    project: str
    weight: int
    via_actors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionLinks:
    """Where a single session leads: the knowledge it produced and its neighbours."""

    notes: list[KnowledgeNote] = field(default_factory=list)  # notes citing it as evidence
    disputes: list[KnowledgeNote] = field(default_factory=list)  # notes it contradicts
    same_actor: list[Any] = field(default_factory=list)  # compactions
    same_project: list[Any] = field(default_factory=list)  # compactions


def _by_actor(compactions: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for c in compactions:
        if c.actor:
            out[c.actor].append(c)
    return out


def related_people(
    compactions: list[Any],
    notes: list[KnowledgeNote],
    actor: str,
    *,
    limit: int = 8,
) -> list[PersonEdge]:
    """Who ``actor`` shares work with, strongest link first.

    ``compactions`` should be the org's sessions over the page window (one query
    the caller already makes) and ``notes`` the org's live notes. Both are
    scanned in Python — at a ten-person startup that is a few hundred rows, the
    same posture ``pages.py`` takes everywhere else.
    """
    by_actor = _by_actor(compactions)
    mine = by_actor.get(actor)
    if not mine:
        return []

    my_projects = {c.project for c in mine if c.project}
    my_files = {f for c in mine for f in (c.files_touched or []) if not _is_generic(f)}

    projects: dict[str, set[str]] = defaultdict(set)
    files: dict[str, set[str]] = defaultdict(set)
    for other, items in by_actor.items():
        if other == actor:
            continue
        for c in items:
            if c.project and c.project in my_projects:
                projects[other].add(c.project)
            for f in c.files_touched or []:
                if f in my_files:
                    files[other].add(f)

    shared_notes: dict[str, list[NoteRef]] = defaultdict(list)
    for note in notes:
        if actor not in note.actors:
            continue
        for other in note.actors:
            if other != actor:
                shared_notes[other].append(NoteRef(id=note.id, title=note.title))

    edges = []
    for other in set(projects) | set(files) | set(shared_notes):
        n_projects = len(projects.get(other, ()))
        n_notes = len(shared_notes.get(other, ()))
        n_files = len(files.get(other, ()))
        edges.append(
            PersonEdge(
                actor=other,
                weight=(
                    WEIGHT_PROJECT * n_projects
                    + WEIGHT_NOTE * n_notes
                    + WEIGHT_FILE * n_files
                ),
                shared_projects=n_projects,
                shared_notes=n_notes,
                shared_files=n_files,
                via_projects=sorted(projects.get(other, ()))[:VIA_CAP],
                via_notes=shared_notes.get(other, [])[:VIA_CAP],
                via_files=sorted(files.get(other, ()))[:VIA_CAP],
            )
        )
    # Name breaks ties so the panel is stable across reloads.
    edges.sort(key=lambda e: (-e.weight, e.actor))
    return edges[:limit]


def project_neighbors(
    compactions: list[Any], project: str, *, limit: int = 6
) -> list[ProjectEdge]:
    """Other projects worked on by the same people — how work actually flows
    across a small org, which the project list alone never shows."""
    contributors = {c.actor for c in compactions if c.project == project and c.actor}
    if not contributors:
        return []
    others: dict[str, set[str]] = defaultdict(set)
    for c in compactions:
        if c.project and c.project != project and c.actor in contributors:
            others[c.project].add(c.actor)
    edges = [
        ProjectEdge(project=name, weight=len(actors), via_actors=sorted(actors))
        for name, actors in others.items()
    ]
    edges.sort(key=lambda e: (-e.weight, e.project))
    return edges[:limit]


def session_related(
    compaction: Any,
    notes: list[KnowledgeNote],
    org_compactions: list[Any],
    *,
    limit: int = 5,
) -> SessionLinks:
    """What a session connects to: the notes it fed, and the nearest sessions by
    the same person and on the same project.

    The reverse evidence lookup (session -> notes) is a scan, because notes
    store evidence as a list rather than a join table; at this scale that is
    cheaper than the index would be.
    """
    cid = compaction.id
    return SessionLinks(
        notes=[n for n in notes if cid in n.evidence][:limit],
        disputes=[n for n in notes if cid in n.disputed_by][:limit],
        same_actor=[
            c
            for c in org_compactions
            if c.actor == compaction.actor and c.id != cid
        ][:limit],
        same_project=[
            c
            for c in org_compactions
            if c.project
            and c.project == compaction.project
            and c.actor != compaction.actor
            and c.id != cid
        ][:limit],
    )


# ── persisted edge builders (context-graph phases 2 and 3) ───────────────
#
# These EMIT edge records for `store.add_edges`; they do not write. Keeping them
# beside the read-time functions is deliberate — the persisted co-occurrence
# edges are produced by calling `related_people`/`project_neighbors` themselves,
# so there is exactly one definition of what "works with" means and no chance of
# the stored graph and the rendered page disagreeing.


def entity_node_id(kind: str, name: str) -> str:
    """Stable id for an entity node, e.g. ``file:src/core.py``.

    Entities are not rows anywhere — they live inside a note's JSON — so the id
    IS the identity. Normalising case here means `Torch` and `torch` are one
    node rather than two that never meet.
    """
    return f"{kind}:{name.strip().lower()}"


def entity_edges(note: Any) -> list[dict[str, Any]]:
    """`mentions` edges from a note to the files, libraries, concepts and
    projects it names.

    This gives ``entities.libraries`` and ``entities.concepts`` their first
    reader in the system: the consolidator has been extracting them since v1 and
    nothing has ever looked at them.
    """
    ents = note.entities
    pairs = [
        *(("file", v) for v in ents.files),
        *(("library", v) for v in ents.libraries),
        *(("concept", v) for v in ents.concepts),
        *(("project", v) for v in ents.projects),
    ]
    return [
        {
            "src_type": "note",
            "src_id": note.id,
            "relation": "mentions",
            "dst_type": "entity",
            "dst_id": entity_node_id(kind, name),
            "weight": 1.0,
            "evidence_id": note.id,
        }
        for kind, name in pairs
        if name and name.strip()
    ]


def cooccurrence_edges(compactions: list[Any], notes: list[Any]) -> list[dict[str, Any]]:
    """Persisted `co_actor` / `co_project` edges.

    Computed by calling the very functions the pages render from, so the stored
    graph cannot drift from what a reader sees. Weight carries the same score,
    and `via` evidence is flattened into `evidence_id` so a stored edge remains
    checkable — an edge nobody can check is a claim the wiki cannot defend.
    """
    out: list[dict[str, Any]] = []
    actors = sorted({c.actor for c in compactions if c.actor})
    for actor in actors:
        for edge in related_people(compactions, notes, actor):
            # One direction only: (a, b) and (b, a) are the same relationship,
            # and `edges_for` matches either end anyway.
            if actor < edge.actor:
                out.append(
                    {
                        "src_type": "person", "src_id": actor,
                        "relation": "co_actor",
                        "dst_type": "person", "dst_id": edge.actor,
                        "weight": float(edge.weight),
                        "evidence_id": ",".join(edge.via_projects[:3]),
                    }
                )
    for project in sorted({c.project for c in compactions if c.project}):
        for edge in project_neighbors(compactions, project):
            if project < edge.project:
                out.append(
                    {
                        "src_type": "project", "src_id": project,
                        "relation": "co_project",
                        "dst_type": "project", "dst_id": edge.project,
                        "weight": float(edge.weight),
                        "evidence_id": ",".join(edge.via_actors[:3]),
                    }
                )
    return out


__all__ = [
    "NoteRef",
    "PersonEdge",
    "ProjectEdge",
    "SessionLinks",
    "VIA_CAP",
    "WEIGHT_FILE",
    "WEIGHT_NOTE",
    "WEIGHT_PROJECT",
    "cooccurrence_edges",
    "entity_edges",
    "entity_node_id",
    "project_neighbors",
    "related_people",
    "session_related",
]
