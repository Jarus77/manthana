"""Consolidation must KEEP the relationships it pays a model to compute.

Before this, every adjudication built a semantic neighbourhood, asked the model
to label each candidate `supports|contradicts|refines|unrelated`, then converted
each label into a note mutation and dropped the label. A "supports" became an
evidence append indistinguishable from any other; "unrelated" — the cheapest
signal to keep and the most expensive to recompute — was `continue`; and the
candidate set itself was discarded and rebuilt next pass.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    Outcome,
    Surface,
)
from manthana.server import ServerConfig, ServerStore
from manthana.server.consolidate import consolidate_org
from manthana.server.llm import ScriptedProvider
from manthana.skills.embed import HashingEmbedder

_NOW = datetime.now(UTC)


def _comp(cid: str = "c1") -> EngineeringCompaction:
    at = _NOW - timedelta(hours=2)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="a@x.com",
        surface=Surface.claude_code,
        project="bench",
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent="re-run the BIRD benchmark",
        approach="swept the decoder",
        outcome=Outcome.success,
        released=True,
        source="full",
        files_touched=["src/bench.py"],
    )


def _note(nid: str, title: str) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id="o1",
        kind=NoteKind.benchmark,
        title=title,
        body="BIRD execution accuracy sits at 61%.",
        scope="project:bench",
        entities=NoteEntities(projects=["bench"], files=["src/bench.py"]),
        actors=["a@x.com"],
        evidence=["c0"],
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
    )


def _run(store: ServerStore, verdicts: list[dict]) -> None:
    payload = json.dumps({"verdicts": verdicts, "new_notes": []})
    consolidate_org(
        store,
        ScriptedProvider([payload]),
        ServerConfig(jwt_secret="x" * 40, admin_token="adm"),
        org_id="o1",
        limit=10,
        embedder=HashingEmbedder(),
        now=_NOW,
    )


def _store(*notes: KnowledgeNote) -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.ingest_compaction(_comp(), org_id="o1", team_id="t1")
    for n in notes:
        store.upsert_note(n)
    return store


def _relations(store: ServerStore, node: str) -> set[str]:
    return {e.relation for e in store.edges_for("o1", node)}


def test_a_supports_verdict_is_kept_as_a_typed_edge() -> None:
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "supports"}])
    edges = store.edges_for("o1", "kn-1", relations=["supports"])
    assert len(edges) == 1
    e = edges[0]
    assert (e.src_type, e.src_id, e.dst_type, e.dst_id) == ("session", "c1", "note", "kn-1")
    # Evidence is what lets a reader check the edge rather than trust it.
    assert e.evidence_id == "c1"


def test_an_unrelated_verdict_is_kept_as_a_negative_edge() -> None:
    """The cheapest signal to store and the most expensive to recompute: a later
    pass that knows these two were already judged unrelated need not re-ask."""
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "unrelated"}])
    edges = store.edges_for("o1", "kn-1", relations=["unrelated"])
    assert len(edges) == 1
    assert edges[0].weight == 0.0


def test_a_contradiction_is_an_edge_not_only_a_status_flag() -> None:
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "contradicts"}])
    assert "contradicts" in _relations(store, "kn-1")


def test_co_retrieved_notes_are_linked_to_each_other() -> None:
    """The candidate set is itself a graph. Notes retrieved together are
    semantic neighbours; recording that is free here and costs a full
    re-retrieval to recover later."""
    store = _store(_note("kn-1", "BIRD accuracy"), _note("kn-2", "BIRD decoder sweep"))
    _run(store, [{"note_id": "kn-1", "relation": "supports"}])
    co = store.edges_for("o1", "kn-1", relations=["co_adjudicated"])
    assert co, "notes adjudicated together must be linked"
    assert {co[0].src_id, co[0].dst_id} == {"kn-1", "kn-2"}
    assert co[0].evidence_id == "c1"  # the session that put them side by side


def test_edges_are_idempotent_across_reconsolidation() -> None:
    # Re-running the same adjudication must rewrite, never duplicate.
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "supports"}])
    store.add_edges(
        "o1",
        [
            {
                "src_type": "session", "src_id": "c1", "relation": "supports",
                "dst_type": "note", "dst_id": "kn-1", "evidence_id": "c1",
            }
        ],
    )
    assert len(store.edges_for("o1", "kn-1", relations=["supports"])) == 1


def test_edges_are_found_from_either_end() -> None:
    """"What contradicts this" and "what this contradicts" are one question to a
    reader; the API must not make them ask twice."""
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "supports"}])
    assert store.edges_for("o1", "kn-1") and store.edges_for("o1", "c1")


def test_edges_are_org_scoped() -> None:
    store = _store(_note("kn-1", "BIRD accuracy"))
    _run(store, [{"note_id": "kn-1", "relation": "supports"}])
    assert store.edges_for("other-org", "kn-1") == []


# ── phase 2: entity nodes ────────────────────────────────────────────────
def test_consolidated_notes_link_to_their_entities() -> None:
    """`entities.libraries` and `.concepts` have been extracted on every note
    since v1 and read by NOTHING. These edges are their first consumer."""
    from manthana.server.graph import entity_node_id

    store = _store()
    payload = json.dumps(
        {
            "verdicts": [],
            "new_notes": [
                {
                    "kind": "gotcha",
                    "title": "torch 2.5 breaks the harness",
                    "body": "Pin 2.4 until the eval harness is fixed.",
                    "files": ["src/bench.py"],
                    "libraries": ["torch"],
                    "concepts": ["dependency pinning"],
                }
            ],
        }
    )
    consolidate_org(
        store,
        ScriptedProvider([payload]),
        ServerConfig(jwt_secret="x" * 40, admin_token="adm"),
        org_id="o1", limit=10, embedder=HashingEmbedder(), now=_NOW,
    )
    for kind, name in (("library", "torch"), ("concept", "dependency pinning")):
        node = entity_node_id(kind, name)
        assert store.edges_for("o1", node, relations=["mentions"]), f"{kind}:{name} unlinked"


def test_entity_ids_are_case_insensitive() -> None:
    """`Torch` and `torch` must be one node, not two that never meet."""
    from manthana.server.graph import entity_node_id

    assert entity_node_id("library", "Torch") == entity_node_id("library", " torch ")


def test_human_notes_also_get_entity_edges() -> None:
    from manthana.server.graph import entity_node_id
    from manthana.server.teach import create

    store = _store()
    create(
        store, "o1", kind=NoteKind.convention, title="Always pin torch",
        body="Pin the version.", author="a@x.com", project="bench",
    )
    assert store.edges_for("o1", entity_node_id("project", "bench"), relations=["mentions"])


# ── phase 3: persisted co-occurrence ─────────────────────────────────────
def test_cooccurrence_edges_match_what_the_pages_render() -> None:
    """Built by calling the SAME functions the pages use, so the stored graph
    cannot drift from what a reader sees."""
    from manthana.server.graph import cooccurrence_edges, related_people

    comps = [_comp("c1"), _comp("c2")]
    comps[1].actor = "b@x.com"
    edges = cooccurrence_edges(comps, [])
    assert any(e["relation"] == "co_actor" for e in edges)
    rendered = related_people(comps, [], "a@x.com")
    stored = [e for e in edges if e["relation"] == "co_actor"][0]
    assert stored["weight"] == float(rendered[0].weight)


def test_cooccurrence_edges_are_recorded_once_per_pair() -> None:
    # (a, b) and (b, a) are the same relationship, and edges_for matches either
    # end — storing both would double-count every collaboration.
    from manthana.server.graph import cooccurrence_edges

    comps = [_comp("c1"), _comp("c2")]
    comps[1].actor = "b@x.com"
    pairs = [
        frozenset((e["src_id"], e["dst_id"]))
        for e in cooccurrence_edges(comps, [])
        if e["relation"] == "co_actor"
    ]
    assert len(pairs) == len(set(pairs))
