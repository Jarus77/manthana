# Manthana Org Wiki — Two Wikis + Founder Q&A (v1, LOCKED)

Status: **locked and implemented** (2026-07-20). Supersedes the open questions in
`manthana-knowledge-consolidation.md` §2–4 for the org side; that document remains
the design rationale for the `KnowledgeNote` shape and the consolidation operator.

## 1. Why

Manthana's centre of gravity moved from the individual engineer to the **founder**
running ~10 people. That founder's real problem is not analytics; it is that the
context needed to run the company lives in ten people's heads and in thousands of
agent sessions nobody re-reads. The product answer is a **Wikipedia for the org**:
browsable, cited, versioned, and editable by humans — where correcting something
once makes it true everywhere.

There are **two** wikis, deliberately different:

| | Personal wiki | Org wiki |
|---|---|---|
| Runs | Engineer's laptop (`manthana dashboard`) | Org server (`/ui/home`) |
| Sees | **Everything** — incl. personal-mode + unreleased | Released compactions only |
| Content | Live projections of their own sessions | KnowledgeNotes + live rollups |
| LLM | **Never** (projections + cached embeddings) | Consolidation + Q&A |
| Audience | The engineer | Founder (and, later, the whole team) |

## 2. Locked decisions

1. **Pages are projections, not documents.** The atomic unit is the typed
   `KnowledgeNote`; a page (Org home / Project / Person) is compiled on read from
   notes + live compaction rollups. Revision history, citations, and editorial
   control therefore fall out of the note model rather than needing page storage.
2. **Auto-publish, revert later.** AI-mined notes go live immediately as
   `candidate` with an "unreviewed" badge. No approval queue — a review gate that
   nobody services produces an empty wiki, which is worse than an imperfect one.
3. **No k-anonymity in this layer.** The target segment is a consented ~10-person
   startup where the flagship question ("what is Suraj working on, and what did he
   decide?") is inherently person-shaped; a floor of 4 makes it unanswerable.
   Person pages are first-class. The k-anon pipeline in `founder.py` is untouched
   legacy for the original contract — **no new code threads `k_anon_floor`**.
4. **Human notes outrank AI notes — the one law of the layer.** A `source="human"`
   note may be *disputed* by new evidence (badge + conflicting-session list) but
   **never superseded or rewritten** by the consolidator. Enforced in code at
   `consolidate.apply_verdicts` (a `refines` verdict against a human note is
   downgraded to `contradicts`), not in the prompt.
5. **Teaching v1 = web UI editing.** Four verbs: edit, create, confirm, revert.
   Every one produces a human-authored version, and `revert` appends rather than
   rewinds so the mistake and its correction both stay on the record.
6. **Freshness is never a note.** "Who is active", "what is X working on", project
   status = live rollups over recent compactions, recomputed on read. A persisted
   answer to a freshness question is stale the moment it is written; this split is
   what stops the wiki rotting.
7. **Personal wiki is zero-LLM.** Projections + the existing cached
   `compaction_vector` index only. Preserves the "agents on laptops never call an
   LLM" invariant (which exists because a `claude -p` call created a session that
   was itself captured and compacted), and makes browsing free and offline-capable.
8. **Shared projections, separate UIs.** Projection logic lives in the Apache-2.0
   `skills` package (`skills/projections.py`) so both the Apache agent and the AGPL
   server import it — the same license-bleed pattern already used by
   `skills/retrieval.py`. Each side renders its own HTML.

### Scope cuts (deliberate, not backlog debt)
- **No consent/audit workstream.** The shipped personal-mode sync gate
  (`agent/sync.py::eligible_for_sync`, guarded by `tests/test_personal_mode_invariant.py`)
  is the security boundary and was not touched. Note *versioning* exists because it
  powers teaching and revision history, not as an audit feature.
- **No "collaboratively built docs/dashboards/apps."** Roadmap language; no v1 work item.
- Cut: reflection/"sleep" pass, staleness-via-git, note `links` population, graph
  expansion, MCP teach path, notifications, replay-eval, FAQ/demand mining.

## 3. What was built

**Schema** — `schemas/src/manthana/schemas/knowledge.py`: `KnowledgeNote` (kinds
`decision|convention|gotcha|failure_pattern|procedure_ref|faq|benchmark`; `scope`;
`entities`; `evidence[compaction ids]`; `actors[]` derived from evidence — this is
what powers Person pages without entity resolution; `source`; `status`;
`confirmed_by`/`disputed_by`; `version` + `supersedes`/`superseded_by`; optional
`metric`/`value`). Enums in `schemas/enums.py`; JSON Schema mirrored via
`manthana-schemas-export`.

**Storage** — three additive tables in `server/tables.py` (`knowledge_note`,
`knowledge_note_vector`, `consolidation_state`), so `create_all` upgrades existing
DBs. `consolidation_state` is **inverted** vs `enrichment_state`: it writes a
`done` row per processed compaction, because `source` flips on enrichment and
cannot double as the consolidation marker. Purge strips purged ids from note
`evidence`/`disputed_by` in the same transaction; an AI note left with no evidence
goes `stale` while a human note keeps standing on its author's authority.

**Consolidation** — `server/consolidate.py`, mirroring `enrich/enricher.py`
(per-org `MeteredProvider`, quota defers the org cleanly, the pass never raises,
bounded per-org and whole-pass). One cheap adjudication call per enriched digest
against ≤12 candidate notes (cosine ∪ entity overlap) → deterministic apply.
Promotion `candidate → established` at ≥3 evidence sessions or ≥2 distinct actors
(gentler than the k-anon-era ≥4 contributors, which no longer applies). Flag
`enable_consolidation`, default off; admin pair `/v1/admin/consolidation[/run]`.

**Pages / Q&A / UI** — `server/pages.py` (zero-LLM projections, computed on read;
no page cache — the vector cache stays the only cache), `server/ask.py` (notes-first
retrieval, live-activity context for freshness questions, session drill only when
notes are thin, human notes tagged `AUTHORITATIVE`, uncited narrative withheld),
`server/teach.py` (the four verbs), `server/wiki_ui.py` (all routes under `/ui/...`
— **required**, because the console cookie is scoped `path='/ui'`). `ui.py`'s
`session_for`/`scope_org` were lifted to module level so both consoles share one
tenant-isolation implementation.

**Personal wiki** — `agent/dashboard/app.py`: home is now projects; the session
list and its controls moved to `/sessions`; `/project/{slug}` renders full
compaction cards; `/search` is rank-only semantic search (no narrative → no model
call). Privacy badges are computed with the shipped `session_is_syncable` gate
rather than a reimplementation, so a badge can never disagree with what syncs.

## 3a. Engineer console logins (added 2026-07-20)

Three console roles now share one cookie login, resolved by `ui.py::session_for`
into a `ConsoleSession(role, org_id, actor)`:

| | admin | founder | **engineer** |
|---|---|---|---|
| Orgs | all | own | own |
| Wiki: read + ask | ✓ | ✓ | ✓ |
| Wiki: **teach** (edit/add/confirm/revert) | ✓ | ✓ | **✓** |
| Oversight (`/ui`, sessions, cost, digest, topics, mining, audit) | ✓ | ✓ | ✗ → `/ui/home` |
| Mint engineer logins | ✓ | ✓ | ✗ |

- **Scope `engineer` is separate from scope `agent` on purpose.** A sync
  credential sitting in a config file on a laptop must not also be a browser
  login, and the console needs a *human* identity to attribute edits to. Each
  `verify_*` rejects the other scopes; `tests/test_engineer_console.py` pins both
  directions.
- **Engineer notes are authored under the engineer's own actor id**, so the
  revision history shows which colleague corrected a claim. This is what turns
  teaching from founder curation into a team behaviour.
- **Founders mint their own team's logins** (`POST /v1/engineer-tokens`, or the
  "Team access to the wiki" panel on `/ui`) — routing every hire through the
  operator would leave the shared context with one reader. Minting is scoped to
  the caller's own org; a founder asking for another org silently gets their own.
- Engineers hitting an oversight page are **redirected to `/ui/home`**, not shown
  a permission error: from their side those pages are simply not part of the
  product.
- Tokens are signed JWTs and are **not stored server-side**; the mint page says so
  rather than letting a founder assume they can retrieve one later.
- New config: `MANTHANA_SERVER_PUBLIC_URL` (e.g. `https://api.latentspaces.in`) —
  used **only** to print the shareable login link. A wrong value yields a bad
  link, never a security hole.

## 4. Known v1 limitations

- `faq` exists in the enum but nothing populates it (demand mining is agent-side).
- Org notes are not pulled down to the laptop; the personal wiki has no notes.
- Benchmark deltas are best-effort: a delta renders only when the superseded
  predecessor carries a parseable `value`. The feed never gates on that working.

## 5. Verification

- `tests/test_projections.py`, `test_knowledge_store.py`, `test_consolidate.py`,
  `test_wiki_pages.py`, `test_ask_notes.py`, `test_teach.py`, `test_wiki_ui.py`,
  `test_personal_wiki.py`, `test_engineer_console.py`.
- Demo: `uv run python validation/seed_demo_org.py && uv run python validation/seed_demo_notes.py`
  (deterministic scripted adjudication; `--live` for real Haiku), then boot the
  server against `manthana-demo.db` and open `/ui/home`. The seed anchors its dates
  to the **run date** (`MANTHANA_DEMO_NOW=YYYY-MM-DD` pins it) because the feeds are
  time-windowed — fixed past dates made a fresh demo look empty.
