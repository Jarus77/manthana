# Knowledge Consolidation Layer — Design Hypothesis (v0, exploration)

Status: **exploration / hypothesis** — not locked. Written 2026-07-16 to frame the next core-engine phase: the consolidated, self-evolving knowledge base (per-engineer + org-level) built on top of session compactions.

## 1. Where the pipeline stops today

Current shape: raw transcript → Turns → Sessions → one `EngineeringCompaction` per session → release/redact → org server → read-time retrieval (semantic rank + coverage), read-time topics/threads (recomputed per query, never persisted), skill mining (`SKILL.md` + provenance).

What does **not** exist: any persisted record that merges *multiple distinct sessions* into one evolving unit of knowledge. Every session is digested independently; consolidation is compute-on-read clustering. There is no dedup/merge, no supersede/version chain, no staleness handling, no materialized org rollup.

The compaction corpus is **episodic memory**. The missing layer is **semantic memory** — durable facts distilled from episodes. (Cognitive analogy: hippocampal episodes → cortical consolidation via replay. The design below is literally that loop.)

## 2. Core hypothesis: the shape is *typed notes + emergent links*, not a triple-store graph

Do not bet the format on a fine-grained knowledge graph up front. Reasons:

- The primary consumers are **LLM harnesses** (Claude Code via memory files/MCP, Manthana's own ask/founder narrative). Harnesses consume *token-budgeted text with citations*, not Cypher queries.
- Triple extraction from noisy dev transcripts is brittle; entity resolution across engineers is the hardest sub-problem and delivers value only after everything else works.
- Field evidence: GraphRAG's usable output is its *community summaries* (text), not raw triples; agent-memory systems (Claude Code memory dirs, Letta/MemGPT, A-MEM/Zettelkasten-style) have converged on **notes/documents with typed links**.
- A graph still *emerges* for free: notes carry entity anchors (file paths, libraries, projects) and `[[note-links]]`. That gives graph traversal when needed without making the graph the storage format.

**Unit of knowledge = `KnowledgeNote`** — a small, typed, cited, versioned document:

```
KnowledgeNote
  id, kind: decision | convention | gotcha | failure_pattern | procedure_ref | faq
  title, body (markdown, ~<300 tokens)
  scope: personal | project:<slug> | org
  entities: {files[], libraries[], projects[], concepts[]}   # anchors
  links: [note ids]                                          # zettelkasten edges
  evidence: [compaction ids]                                 # provenance, drill-down path
  contributors: int (count only at org scope — k-anon at note granularity)
  confidence, status: candidate | established | disputed | stale | superseded
  version, superseded_by, created_at, last_confirmed_at
```

Kinds map to what sessions actually contain: decisions ("chose X over Y because Z"), conventions/environment facts ("tests need X env var"), gotchas, failure patterns (from friction_points/dead_ends — knowing what *didn't* work is half the value), procedure_ref (pointer to a mined SKILL.md — skills stay as-is), and **faq** (see §4).

## 3. The consolidation operator (the new core machinery)

Event-driven, incremental — runs when a new compaction lands (agent-side) or is ingested (server-side):

1. **Retrieve** candidate related notes: semantic rank (reuse `skills/retrieval.py::rank`) + entity overlap (shared files/libraries).
2. **Adjudicate** (one LLM call): for each candidate note, the new compaction *supports / contradicts / refines / is unrelated*; plus "does this session contain a note-worthy fact not covered?"
3. **Apply** deterministically:
   - supports → append evidence, bump confidence, update `last_confirmed_at`
   - contradicts → mark `disputed` (surface, don't silently pick a winner)
   - refines → rewrite body, `version++`, keep old text in version chain
   - new fact → create `candidate` note (1 evidence)

Plus a periodic **reflection pass** (the "sleep" job, like `compact_settled`): cluster unconsolidated compactions (reuse `skills/cluster.py`) and propose notes from recurrences the incremental path missed.

**Lifecycle / decay** — the self-evolution rules:
- `candidate` → `established`: ≥3 distinct sessions (personal) / ≥4 distinct contributors (org) — reuse the existing, dogfood-validated thresholds (1-contributor skills scored 2/5).
- `stale`: entity anchors invalidated (cited file changed/deleted — checkable via git) or no confirmation within a horizon.
- Never delete: `superseded` + version chain, evidence preserved. Trust properties carry over: org notes synthesize **only released compactions**, redaction already applied upstream, contributor count enforced per note, manager-only drill to evidence.

**Org structure**: NOT a folder-per-engineer tree. Knowledge is about the *codebase/domain*, so the org KB is organized by project/topic; the engineer is metadata (a projection/filter), not the hierarchy. Per-engineer KB = personal notes + their slice of org notes. Same schema both sides, two stores — mirroring the existing agent/server split exactly.

**Store vs views**: the note store is canonical; harness surfaces are disposable *renders*:
- token-budgeted per-project markdown digest (top established notes) → injectable as Claude Code memory / CLAUDE.md supplement,
- MCP tools `search_knowledge` / `get_note` / drill note → evidence compactions → raw turns (extends existing `mcp_server.py`),
- founder narrative gets notes as pre-consolidated, already-k-anon context.

## 4. The "mine user questions" idea — use it as the demand signal + eval set

Mining only user prompts from transcripts is genuinely valuable, but as the **demand side**, not the KB itself: repeated questions tell you *what knowledge is needed*; the sessions' outcomes are the supply. Two uses:
1. **faq notes**: a question asked ≥N times across sessions/engineers becomes a note pairing the canonical question with the best evidenced answer. A repeated question with *no* good answer = an explicit knowledge gap (org signal in itself).
2. **The eval set** (§5) — this is how we discover the right shape instead of guessing.

## 5. How to find the shape empirically (research plan)

Don't pick the representation by argument — pick it by measurement. We have the perfect setup: a timestamped real corpus.

1. **Demand mining**: extract + cluster all user questions from the existing corpus → taxonomy of what engineers/founders actually ask. (Implements the user-question instinct directly.)
2. **Replay eval** (the key instrument): replay the corpus chronologically. At each real user question asked at time *t*, ask: *could the KB built from sessions before t have answered it, grounded?* Metric = **repeated-question prevention rate** + groundedness + token cost. A self-evolving KB's quality is exactly the fraction of incoming questions answerable from prior consolidated knowledge.
3. **Representation bake-off** on the same corpus, same eval:
   - A: status quo (per-session digests + semantic rank) — baseline
   - B: + consolidated typed notes (this doc)
   - C: B + entity-link graph expansion at retrieval time
   Measure answer quality (LLM-judge + spot human), citation groundedness, staleness behavior, tokens/query, consolidation cost.
4. Decision rule: adopt the cheapest representation whose eval wins are material. If C ≈ B, the graph stays an index, not a layer.

This is also a publishable result (fits the planned compaction-fidelity / skill-distillation papers): "measuring self-evolving org memory by replayed-question prevention."

## 6. Known challenges (ranked)

1. **Merge correctness** — LLM adjudication can wrongly merge distinct facts or hallucinate refinements. Mitigation: deterministic apply-step, version chains, evidence always cited, disputed-not-overwritten.
2. **Staleness vs code drift** — knowledge about code rots when code changes. Entity anchors + git invalidation is the lever; this is largely unsolved in the literature (research contribution).
3. **Contradiction resolution** — newer ≠ truer. Surface `disputed` to humans rather than auto-resolving.
4. **Privacy at synthesis granularity** — a note blends contributors; k-anon must hold per note (contributor count on the note), not just per query.
5. **Eval difficulty** — hence the replay eval as the primary instrument, built *before* the layer.
6. **Cost** — one adjudication call per new compaction (~compaction-scale, ~$0.2 each measured); reflection passes batched.

## 7. Suggested build order

1. Demand mining + replay-eval harness over the existing corpus (no new schema — pure measurement; validates the whole direction cheaply).
2. `KnowledgeNote` schema + agent-side store + incremental consolidation operator (personal scope first).
3. Renders: per-project digest markdown + MCP `search_knowledge`.
4. Org-side consolidation on released corpus with per-note k-anon.
5. Bake-off (§5.3) → lock representation → then, only if C wins, invest in entity resolution/graph.
