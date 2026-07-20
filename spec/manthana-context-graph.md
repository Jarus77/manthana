# Manthana Context Graph — design

Status: **proposed**, 2026-07-20. Nothing here is built yet.

## 1. The finding that motivates this

Manthana already computes a knowledge graph, pays an LLM to label its edges, and
then throws the edges away.

For every enriched digest, `consolidate.retrieve_candidates`
(`server/consolidate.py:112`) builds a real semantic neighbourhood — cosine ≥
0.25 over cached note embeddings, unioned with entity overlap, capped at 12
candidates. That neighbourhood goes into one adjudication call which returns, per
candidate, a **typed relation**: `supports | contradicts | refines | unrelated`
(`consolidate.py:177`).

Then `apply_verdicts` (`consolidate.py:332-398`) converts each relation into a
*mutation* and discards the relation itself:

| verdict | what is kept | what is lost |
|---|---|---|
| `supports` | the compaction id, appended to `evidence` | **the fact that it was a support** — indistinguishable afterwards from any other evidence |
| `contradicts` | compaction id in `disputed_by` + `status=disputed` | survives only because it lands in a differently-named list |
| `refines` | `supersedes` on the new version | flattened into a version chain; the semantic relation is gone |
| `unrelated` | nothing — `continue` | the negative edge, which is the cheapest signal to have and the most expensive to recompute |

The candidate set is discarded too. So the system pays for graph construction on
every pass and keeps none of it.

Meanwhile `KnowledgeNote.links[]` (`schemas/knowledge.py:57`) has existed since
v1 as a declared seam with, verifiably, **no writer anywhere in the repo**. And
`NoteEntities.libraries` / `.concepts` are extracted by the LLM, stored, and read
by nothing.

Everything else that looks like a graph is recomputed per page render:
`server/graph.py` derives person↔person and project↔project edges from
co-occurrence, with no persistence, and `session_related` concedes in its own
docstring that the reverse-evidence lookup is a full scan "because notes store
evidence as a list rather than a join table".

**So this design is mostly about keeping what already exists, not computing
something new.**

## 2. What the graph is for

Not visualisation. A force-directed blob of 1600 nodes answers no question. The
graph earns its place by serving three reader needs the current wiki cannot:

1. **"What else should I read?"** — traversal from any entry to genuinely
   related ones, with the relation named. Today the only note→note link is a
   version chain.
2. **"Is this contested?"** — contradiction as a first-class, queryable edge
   rather than a status flag whose reasoning is gone.
3. **"What is this cluster of work actually about?"** — topics as durable
   objects, so a project page can say *what the project is* rather than quoting
   a session's raw first prompt.

## 3. Model

Three node types, all of which already exist as data:

- **Note** (`knowledge_note`) — a durable claim.
- **Session** (`released_compaction`) — evidence.
- **Entity** — a file, library or concept, currently trapped inside
  `NoteEntities` JSON. Promoted to a node so "everything we know about
  `torch`" becomes answerable.

Plus one new derived type:

- **Topic** — a cluster of notes, named. Replaces the guessed project
  description with something grounded (§6).

### Edge table

One new table, `knowledge_edge`, and it is the whole storage change:

```
src_type, src_id, dst_type, dst_id, relation, weight, evidence_id, created_at, org_id
```

`relation ∈ supports | contradicts | refines | supersedes | mentions | co_actor
| co_project | derived_from`. Indexed on `(org_id, src_type, src_id)` and
`(org_id, dst_type, dst_id)` so traversal is two indexed lookups instead of the
Python scans `graph.py` does today.

The critical property: **every edge carries `evidence_id`** — the compaction or
note that justifies it. An edge nobody can check is a claim the wiki cannot
defend, and the existing UI convention (every connection states its "via")
already depends on this.

## 4. How edges get written

**Phase 1 — stop discarding (no new cost).** Change `apply_verdicts` to emit an
edge alongside each mutation. `supports`/`contradicts`/`refines` become typed
note↔session edges, and — the genuinely new information — the *candidate set*
becomes `mentions` edges between the notes that were adjudicated together. Also
persist `unrelated` as a negative edge so later passes can skip re-asking.

This is a pure win: the data is already in memory at
`consolidate.py:359-377`, correctly typed, already paid for.

**Phase 2 — promote entities.** Write `mentions` edges from notes to their
`entities.files/libraries/concepts`. Gives `libraries` and `concepts` a first
reader and makes "what touches this file" a lookup rather than a scan.

**Phase 3 — persist the co-occurrence edges** `graph.py` recomputes per render
(`co_actor`, `co_project`), keeping the same weights so behaviour is unchanged
and only the cost moves.

## 5. Clustering

`skills/cluster.py` already implements what is needed: greedy non-overlapping
community detection at cosine ≥ 0.75, unknown-k, with k-means explicitly
rejected. Its core `community_detection` (`cluster.py:77`) takes bare vectors and
returns index lists — **no domain coupling at all**. Note vectors already exist
and are cached (`vectors.ensure_note_vectors`).

So topic discovery is: run the existing clusterer over note vectors instead of
compaction vectors. One caveat the code itself warns about — compaction vectors
are embedded from `task_intent + approach` and note vectors from `title + body`;
the two spaces are not comparable and must never be mixed in one run.

Naming a cluster is one cheap LLM call over its member titles. That name is a
**Topic node**, cached and human-editable through the existing teach verbs — the
same "AI proposes, humans correct, correction wins" contract as notes.

## 6. What this fixes in the product

- **Project and person descriptions.** A project page can render its topics
  instead of quoting a raw first prompt mid-word. This is the honest fix for
  the "descriptions don't make sense" complaint: the answer is not "write a
  better prompt", it is "stop deriving a description from a single session".
- **The taxonomy problem.** Kind-as-navigation collapses at ~1600 entries
  because kind is a property, not a route. Topics are a route: finite, named,
  and about something.
- **Ask.** Retrieval can traverse — pull a note's neighbourhood, not just its
  cosine top-k — which is exactly the gap behind the failed
  "do A and B correlate" query.

## 7. Cost and scale

Phase 1 is free: the data is in memory and discarded. Phase 2 is a table write
per note. Phase 3 moves existing computation from render time to write time.
Clustering is O(n²) capped at 2000 items, run periodically, not per request.

The real scaling limit is elsewhere and worth stating: `retrieval.rank_scored`
is a **brute-force linear scan** over an in-memory dict, with vectors stored as
JSON blobs. That is fine at startup scale and is the first thing to break at wiki
scale — an ANN index is a separate piece of work from this design.

## 8. Deliberately excluded

- **A graph visualisation.** Until traversal proves useful in text, a canvas is
  decoration.
- **Cross-org edges.** Every edge is org-scoped; tenant isolation is not
  negotiable for a convenience feature.
- **Inferring edges from an LLM in a dedicated pass.** Everything in phases 1-3
  reuses computation already happening. A standalone edge-inference pass should
  only be considered once the free edges prove insufficient.
