# Compaction quality — empirical validation (10 real sessions)

_Run 2026-06-20 against the real corpus (471 sessions). Compactions generated via
`claude -p` (Opus). Worksheet: [worksheet.md](worksheet.md); harness:
[score_compactions.py](score_compactions.py)._

> **Update 2026-06-20:** defects **#1, #2, #3, #4 are all FIXED** (spec §30; 202 tests
> green). Verified on real data: #1 cost priced per-turn + `total_tokens` surfaced +
> relabeled "API-list-equiv"; #2 scribe 46/46→1 flagged slice (corpus 196→5); #3
> `files_touched` recall 2/21→21/21 (scribe), 1/7→7/7 (harness); #4 long sessions now
> keep head+tail so the ending is never dropped. The **founder "what went wrong?" path
> is also FIXED** (friction + query now reach the narrative; verified live with a
> grounded 3-citation failure summary). The engineer-dashboard cost page now shows
> tokens + an "API-list-equiv" label. **All validation defects are now resolved.**

## Headline verdict

**The hard part works; the mechanical layer is buggy.** The *qualitative* fields
the LLM writes — `task_intent`, `approach`, `friction_points` descriptions — are
genuinely high quality: specific, technical, grounded, and they capture
counterintuitive results with mechanisms (e.g. data: "the agent autonomously wrote a
Python script calling Sonnet to classify ~6,696 articles — an LLM-as-tool sub-call
the CLI's self-reported cost misses"; TTS: "every Devanagari word collapses to UNK
… the 12k BPE vocab contains zero Hindi tokens"). This is shippable reasoning.

**But four mechanical/architectural defects make it NOT design-partner-ready yet** —
and all four hurt exactly the downstream consumers (founder rollup, skill mining):

## Evidence table

| project | turns | path | files recall | friction refs | outcome | cost (Opus list) | faithful? |
|---|---|---|---|---|---|---|---|
| scribe | 1384 | summary | 2/21 | empty | partial | $791 | over-scoped to whole file |
| manthana | 902 | summary | 22/82 | partial | success | $431 | ✅ accurate |
| bird-bench | 634 | summary | 6/21 | ✅ | partial | $641 | over-scoped |
| dab_clone | 558 | **full ⚠tail** | 36/50 | ✅ | partial | $441 | ✅ but missed ending |
| data | 507 | **full ⚠tail** | 11/13 | ✅ | partial | $135 | ✅ but missed crash+recovery |
| harness | 355 | summary | 1/7 | ✅ | success | $266 | summary-scoped |
| hinglish | 316 | full | 7/7 | ✅ | success | $253 | ✅ excellent |
| TTS | 67 | summary | 2/2 | ✅ | success | $43 | ✅ accurate |
| grafting | 32 | full | n/a* | ✅ | success | $3.9 | ✅ accurate |
| trajectory | 23 | full | 4/7 | none | success | $3.6 | ✅ accurate |

\* grafting touched files only via Bash/Read-scripts, which the harness metric
doesn't count — see measurement caveat below.

## The four defects (priority order)

### 1. 🔴 `est_cost_usd` is meaningless for a subscription user
Every session is priced at **Opus API list rates**, dominated by **cumulative
cache-read** tokens (scribe = 451M cache-read → ~$676 of its $791). The arithmetic
is correct, but you run on a **Claude subscription**, not pay-per-token — so the
figure is ~50–100× your actual spend. A founder seeing "$791 for one session" or an
inflated org total loses trust instantly.
**Fix:** detect subscription / report token-volume or "API-list-equivalent" label;
and stop pricing all turns at the *last-seen* model (mixed-model sessions mis-price).

### 2. 🔴 Cumulative summary bleeds whole-file scope onto each sessionize slice
`read_summary()` is **file-scoped** (newest cumulative summary of the entire
`.jsonl`), but it's fed to a single **slice**. One scribe file splits into **46
slices, all flagged** — so `watch --compact-summarized` would emit **46
near-duplicate compactions**, each describing the whole multi-day arc, each with a
huge cost. Skill-mining sees false recurrence; founder rollup sees 46× the same
narrative. scribe `.11`'s `task_intent` is the entire arc (leakage + ablations + LFS
recovery + paper + blog) while its turns just finish a blog.
**Fix:** scope the summary to the slice (only summaries whose boundary falls within
the slice), or compact at the **file** level not the slice level, or dedupe slices
of one file into one compaction.

### 3. 🟠 `files_touched` collapses on the summary path + is polluted with non-files
Summary-path recall 2/21, 1/7, 6/21 vs full-path 7/7, 36/50, 11/13. Claude's prose
summary doesn't enumerate files, so the field starves. Worse, the model dumps
**non-files** into it: "patents (5.4GB)", "DABStep dev set (450 tasks)", "Mongo:
articles_db (articles=127600)". Anything querying "what files" on summarized
sessions will be unreliable.
**Fix:** populate `files_touched` deterministically from the turns' tool calls
(Edit/Write/Read/MultiEdit) instead of asking the LLM; keep the LLM list as a
secondary signal. Tighten the schema instruction to exclude datasets/descriptions.

### 4. 🟠 Full-path >400-turn sessions silently drop their ending
`_MAX_TURNS=400` takes `turns[:400]`. dab_clone (558) and data (507) lost their
tails — data's compaction never saw the run **crash at 23/54 and the recovery**
(turns 497–506), so `outcome=partial` is right by luck, not by evidence. End-of-
session friction/outcome is structurally invisible.
**Fix:** include a head **and tail** window (e.g. first 250 + last 150), or summarize
the middle; never drop the ending.

### Also: friction `turn_refs` empty on the summary path
Descriptions are great but unanchored (scribe: 5 points, all refs `[]`). Full-path
sessions ground refs fine. Tie to the summary-scope fix.

## Measurement caveats (honest)
- `files_touched` "recall" counts only Edit/Write/Read/MultiEdit/NotebookEdit targets;
  files touched via Bash (`cat`, `python script.py`) or referenced in prose are not
  counted, so recall **understates** real coverage and "not-in-transcript"
  **over-counts**. The summary-vs-full *relative* gap still holds.
- `est_cost_usd` "validation" can't compare to real billed spend (subscription); it
  validates the token inputs + rate semantics, which is where the defect is.

## What needs YOUR memory (subjective column in worksheet.md)
For each session: does `task_intent`/`approach` match what you *actually* set out to
do, and are the `friction_points` your *real* frustrations? My faithfulness column
says "is it supported by the transcript"; only you can say "is it what was in my
head." Best full-path exemplar to sanity-check first: **hinglish** and **data**.

## Ship decision
- **Reasoning quality:** design-partner-ready. ✅
- **Blockers before a design partner:** #1 (cost) and #2 (slice scope) are
  trust-breaking and must be fixed; #3 and #4 are quality fixes that should follow.
- None require a rewrite — all four are targeted changes in `cost/`, `compact.py` +
  collector summary-scoping, and `compactor/prompt.py`.

---

# Founder narrative — groundedness (2 real queries via `claude` CLI)

_Harness: [founder_check.py](founder_check.py). 10 compactions ingested into an
in-memory server; `k_anon_floor=1` so single-actor data flows (the floor=4 privacy
gate has its own tests). Narrative + filter-parse run on the real `claude` CLI._

### ✅ "what is the team working on this week?" — works well
- **Filter parse correct:** "this week" → `since=2026-06-15 until=2026-06-21`.
- **Rollup correct:** 5 sessions in-window, by_project/by_outcome accurate (3 success,
  2 partial).
- **Narrative:** accurate, specific, genuinely useful to a founder.
- **Citations: 5/5 valid** — every cited id maps to the right compaction; no
  hallucinated citations. The exact-or-unique-prefix matcher holds. The "grounded +
  cited" promise **delivers** for "what is X doing" queries.

### 🔴 "what went wrong or failed recently?" — returns "insufficient data"
The single most important founder question is **structurally unanswerable**:
1. The parser collapsed "went wrong / failed" → `outcome='abandoned'` only. None of the
   sessions are abandoned (they're success/partial) → 0 rows → insufficient_data. It
   misses `partial`, and "failure" isn't a clean outcome value anyway.
2. **`friction_points` — the actual failure content — is never queryable and never fed
   to the narrative.** The narrative brief sends only `{id, project, intent, outcome}`
   (run_query line ~217); `approach`/`friction_points`/`artifacts` are dropped. So even
   with matching data, the founder cannot learn *what* went wrong.

It fails *safe* (no hallucination — the design holds), but it's a usability dead-end on
a core need.
**Fix:** map failure-type queries to a friction/partial path; include `friction_points`
in the query surface and the narrative brief.

### 🔴 Cost bug surfaces at the worst layer
The rollup reports **`total_cost_usd=$1,521.85`** for 5 single-engineer sessions in one
week — the founder dashboard would show absurd spend. Same root cause as compaction
finding #1; most damaging exactly here.

## Founder verdict
Citation/grounding machinery is **solid** for "what are people doing." But "what's going
wrong" — arguably the #1 founder question — is unanswerable until `friction_points`
enters the query+narrative path, and the cost rollup is untrustworthy until #1 is fixed.
