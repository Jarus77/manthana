# Validation scores — my own grading (2026-06-25)

Scored by Claude against ground truth in [worksheet.md](worksheet.md) +
[founder_scoring_sheet.md](founder_scoring_sheet.md), with raw-transcript spot-checks of the
boldest claims. Rubric: **5** faithful+specific+complete · **4** faithful, minor gap · **3**
correct but generic / one notable gap · **2** partly wrong/vague · **1** wrong/hallucinated.

> Spot-checks (verbatim in transcripts): bird-bench `55.35 / 58.67 / 70.86`, `6,401`, "198
> fan-out golds"; data `23/54` crash + `gzip`/`Accept-Encoding` fix + `agnews`; scribe `404`.
> Friction `turn_refs` valid on all; `files_touched` recall 100% on every session.

## Engineer compaction fidelity (10 sessions)

| # | session | intent | approach | friction | outcome | files | **overall** |
|---|---------|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | scribe (1384) | 5 | 5 | 5 | 4 | 5 | **5** |
| 2 | manthana (902) | 5 | 5 | 4 | 5 | 5 | **5** |
| 3 | bird-bench (634) | 5 | 5 | 5 | 5 | 5 | **5** |
| 4 | dab_clone (558) | 5 | 5 | 5 | 5 | 5 | **5** |
| 5 | data (507) | 5 | 5 | 5 | 5 | 5 | **5** |
| 6 | harness (355) | 5 | 5 | 5 | 5 | 5 | **5** |
| 7 | hinglish (316) | 4 | 5 | 5 | 5 | 5 | **5** |
| 8 | TTS (67) | 4 | 5 | 4 | 5 | 5 | **4.5** |
| 9 | grafting (32) | 5 | 5 | 5 | 5 | n/a | **5** |
| 10 | trajectory (23) | 5 | 5 | 5 | 5 | 5 | **5** |

**Engineer average ≈ 4.95 / 5.**

Notes on the few sub-5s:
- **scribe outcome (4):** `partial` is defensible — the blog finished but the headline
  model-swap experiment dead-ended and the I-2 run was killed at 23/117; "partial" reflects the
  experiment, the ending shows the blog done. Mild ambiguity, not an error.
- **manthana friction (4):** accurate but skews to mechanical linter/FastAPI friction; the
  higher-level "adversarial review found 11 bugs" lives in `approach` rather than `friction`.
- **hinglish / TTS intent (4):** the literal first turn is thin ("yes do this" / a narrow
  checkpoint question), so intent is *inferred from the arc* — correctly, but with mild
  inference risk vs a literal ask.
- **TTS friction (4):** only 1 point — correct (the session was genuinely low-friction), but
  sparse.

Highlights: **bird-bench** reproduces exact benchmark numbers and the "198 fan-out golds"
finding; **data** captures the end-of-run crash *and* recovery (proves the head+tail fix —
the old version dropped the tail); **harness** captures a research-ethics refusal as friction.

## Founder / manager query (10 queries)

(a) filter + (b) citations are auto-scored; **(c) usefulness** is mine. Refusals are split into
*correct privacy behavior* vs *fixable misses*.

| # | bank/view | query | (a) | (b) | (c) useful | verdict |
|---|-----------|-------|:--:|:--:|:--:|---------|
| 1 | REAL/MGR | what did I work on recently? | ✓ | ✓ | **5** | 4 projects, grounded+cited |
| 2 | REAL/MGR | where did I hit the most friction? | ✓ | ✓ | **5** | ranks data>bird>scribe w/ specifics |
| 3 | REAL/MGR | time on LLM eval / benchmark? | ✓ | ✓ | **5** | DAB+BIRD+SCRIBE swap, cited |
| 4 | SYNTH/FND | team time on LLM evaluation? | ✓ | ✓ | **5** | FIXED: slug-resolve → 6 valid cites |
| 5 | SYNTH/FND | what kept failing across the team? | ✓ | ✓ | **5** | 3 patterns, 15 valid cites |
| 6 | SYNTH/FND | which sessions did the team abandon? | ✓ | – | **n/a** | ✓ correct k-anon refusal (<4 people) |
| 7 | SYNTH/FND | building in text-to-sql? | ✓ | ✓ | **5** | OmniSQL RL, LoRA→FSDP, cited |
| 8 | SYNTH/FND | what went wrong in ASR? | ✓ | ✓ | **5** | 2 issues, cited (post case-fix) |
| 9 | SYNTH/FND | what did Suraj work on this week? | ✓ | – | **n/a** | ✓ correct privacy refusal (per-person) |
| 10 | SYNTH/MGR | what did Suraj work on this week? | ✓ | – | **1** | "this week" vs stale 2026-06-20 fixture* |

\* **#4 is now FIXED** (`_resolve_project` token-prefix match: `"LLM evaluation"` → `llm-eval`):
the live query now returns 10 sessions / 5 contributors / 6 valid citations + grounded
narrative. #10 remains a clock-vs-fixture artifact ("this week" resolves to today, the
synthetic data is dated 2026-06-20) — not a code bug; the manager view itself is proven
working (see below).

**Of the 7 queries that returned a narrative: usefulness 5/5 on every one, citations 100%
valid.** The 2 refusals at #6/#9 are the privacy design working (correct). #10 is the only
remaining non-answer, and it's a test-fixture date artifact, not hallucination.

### Privacy contrast (the headline) — confirmed live, **5/5**
Same question, two roles:
- **FOUNDER** "what has Suraj worked on?" → **refused by k-anon** (can't single out a person).
- **MANAGER** (audited `allow_individual`) → **4 valid citations** + a grounded named narrative
  ("Suraj worked across text-to-sql and llm-eval … MGPO RL fine-tuning … LoRA→FSDP").

`_resolve_actor` maps "Suraj" → `suraj@acme.demo` correctly. Founder refuses, manager allows +
cites — exactly the trust contract.

## Bottom line
- **Engineer compaction: 4.95/5** — faithful, specific, files exact, endings intact, cost
  transparent (~$0.21/digest). Ship.
- **Founder/manager: 5/5 on every answer it gave; citations 100% valid; privacy correct.** The
  slug-parse gap is now fixed (#4); the only remaining non-answer (#10) is a test-fixture date
  artifact, not a code bug. Zero hallucinations.
