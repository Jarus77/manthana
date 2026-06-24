# Founder / Manager query scoring sheet

Auto-scored: **(a) filter correct** and **(b) citations accurate**. **(c) narrative useful** is for you to score 1–5.

> **Note:** raw auto-run output. Query **#4** ("team time on LLM evaluation") shows FAIL/insufficient
> here because it ran *before* the `_resolve_project` slug-resolve fix; post-fix it returns 10
> sessions / 5 contributors / 6 valid citations. See `SCORES.md` for the graded, post-fix results.

| # | bank | view | query | (a) filter | (b) cites | (c) useful (you) | overall |
|---|------|------|-------|-----------|-----------|------------------|---------|
| 1 | REAL | MANAGER | what did I work on recently? | PASS | PASS | ___ | ___ |
| 2 | REAL | MANAGER | where did I hit the most friction or blockers recent | PASS | PASS | ___ | ___ |
| 3 | REAL | MANAGER | where did I spend time on LLM evaluation or benchmar | PASS | PASS | ___ | ___ |
| 4 | SYNTH | FOUNDER | where did the team spend time on LLM evaluation work | FAIL | N/A | ___ | ___ |
| 5 | SYNTH | FOUNDER | what kept failing recently across the team? | PASS | PASS | ___ | ___ |
| 6 | SYNTH | FOUNDER | which sessions did the team abandon? | PASS | N/A | ___ | ___ |
| 7 | SYNTH | FOUNDER | what is the team building in the text-to-sql project | PASS | PASS | ___ | ___ |
| 8 | SYNTH | FOUNDER | what went wrong in the ASR / speech work? | PASS | PASS | ___ | ___ |
| 9 | SYNTH | FOUNDER | what did Suraj work on this week? | PASS | N/A | ___ | ___ |
| 10 | SYNTH | MANAGER | what did Suraj work on this week? | PASS | N/A | ___ | ___ |

## Per-query detail

### 1. [REAL/MANAGER] what did I work on recently?
- note: broad self rollup; cites several
- (a) filter: **PASS** — filter ok {}
- (b) citations: **PASS** — 4 citations all valid
- citations: ['comp-7f09f08c-a4c3-4360-ab62-6063864bdce9', 'comp-1782ce84-8cc9-44c7-9f70-c17a85fb1111.7', 'comp-2647d07f-646e-444d-b3a6-c16c94f67b70', 'comp-138db3b9-4762-4d01-8633-2764c60b197c.2']
- narrative: You worked across four projects recently:

- **Manthana (project)** — built the full v1 platform from spec through Phase 6, including multi-tenant server, compaction pipeline, and founder query API [comp-138db3b9].
- **DataAgentBench (dab_clone)** — built two agent harnesses (ReAct and SCRIBE) and ran the DAB benchmark on them; SCRIBE had partial results due to column-naming and Git LFS issues blocking most datasets [comp-7f09f08c].
- **BIRD text-to-SQL (bird-bench)** — staged an RL training pipeline for OmniSQL-7B, including baseline evaluation, MGPO advantage weighting in verl, and GRPO/MGPO run scripts [comp-1782ce84].
- **Trajectory eval** — explored the architecture of a trajectory evaluation harness for a text-to-SQL agent [comp-2647d07f].

### 2. [REAL/MANAGER] where did I hit the most friction or blockers recently?
- note: friction question — must NOT collapse to outcome filter
- (a) filter: **PASS** — filter ok {}
- (b) citations: **PASS** — 2 citations all valid
- citations: ['comp-10375d20-b5a4-4e08-8d51-198705175757', 'comp-59896ed2-495e-4661-a0a5-ec85c8318ed9.11']
- narrative: The heaviest friction was in the **data** project [comp-10375d20], which hit 5 separate blockers — Git LFS bandwidth exhaustion blocking 9 datasets, an `ImportError` crashing 9 of 54 validators, a gzip-compressed response breaking token metering, a cost double-counting bug requiring a full redesign, and a false-alarm checkpoint waiter. Close behind is **bird-bench** [comp-1782ce84-5f21], with 5 friction events including a schema serialization change that collapsed model accuracy from 55% to 17% (requiring a full revert) and a false Monitor trigger that required manual polling. The **scribe** project [comp-59896ed2] also saw significant dead-ends — three executor model options (Claude 3.7, two DeepSeek R1 distills) were all unusable before a run even started, and a 117-job multi-stance experiment was abandoned at 23/117 after zero tasks flipped.

### 3. [REAL/MANAGER] where did I spend time on LLM evaluation or benchmark work?
- note: semantic; should cite the eval/benchmark sessions, not all 10
- (a) filter: **PASS** — filter ok {}
- (b) citations: **PASS** — 6 citations all valid
- citations: ['comp-10375d20-b5a4-4e08-8d51-198705175757', 'comp-7f09f08c-a4c3-4360-ab62-6063864bdce9', 'comp-1782ce84-8cc9-44c7-9f70-c17a85fb1111.7', 'comp-4811a97d-8461-4c22-b9b1-d4951bd853ab', 'comp-59896ed2-495e-4661-a0a5-ec85c8318ed9.11', 'comp-2647d07f-646e-444d-b3a6-c16c94f67b70']
- narrative: The bulk of your LLM evaluation time went into **DataAgentBench (DAB)**: you built a full benchmark harness for Claude Code against 54 queries [comp-10375d20], then extended it with ReAct and SCRIBE multi-role harnesses and ran comparative evaluations across both architectures [comp-7f09f08c]. You also worked on **BIRD text-to-SQL RL training**, establishing OmniSQL-7B baselines and staging a GRPO/MGPO pipeline [comp-1782ce84]. Separately, you ran a **model-swap experiment on SCRIBE** to isolate architecture vs. model contributions to benchmark score, testing DeepSeek, Kimi, and Claude variants [comp-59896ed2], and retrieved/aggregated **DABStep benchmark scores** split by difficulty [comp-4811a97d]. A smaller session explored a **trajectory evaluation harness** for a text-to-SQL agent [comp-2647d07f].

### 4. [SYNTH/FOUNDER] where did the team spend time on LLM evaluation work?
- note: project aggregate across 5 engineers
- (a) filter: **FAIL** — project: want~'llm-eval' got 'LLM evaluation'  (full: {'project': 'LLM evaluation'})
- (b) citations: **N/A** — refused
- citations: []
- narrative: (refused)

### 5. [SYNTH/FOUNDER] what kept failing recently across the team?
- note: friction question — must NOT collapse to outcome=abandoned
- (a) filter: **PASS** — filter ok {}
- (b) citations: **PASS** — 15 citations all valid
- citations: ['demo-infra-tarun-0', 'demo-infra-rohan-0', 'demo-llm-eval-tarun-1', 'demo-data-pipeline-dev-0', 'demo-data-pipeline-neha-0', 'demo-data-pipeline-arjun-0', 'demo-infra-rohan-1', 'demo-infra-vikram-1', 'demo-infra-kavya-1', 'demo-text-to-sql-arjun-0', 'demo-text-to-sql-suraj-0', 'demo-data-pipeline-dev-1', 'demo-data-pipeline-isha-1', 'demo-llm-eval-suraj-0', 'demo-llm-eval-priya-0']
- narrative: Three patterns dominated recent friction across the team:

**API & tool errors** hit llm-eval repeatedly — the Anthropic API rate limit interrupted long eval runs mid-flight on multiple sessions [demo-llm-eval-tarun-1, demo-llm-eval-suraj-0, demo-llm-eval-priya-0], and a file edit failure on `verl_grpo_baseline.py` blocked the text-to-sql fine-tuning work [demo-text-to-sql-arjun-0, demo-text-to-sql-suraj-0].

**Infrastructure linter loops** were a recurring blocker in the containerization work — ruff/pyright kept failing on long inline f-strings, requiring repeated fix cycles across multiple engineers [demo-infra-rohan-1, demo-infra-vikram-1, demo-infra-kavya-1]; separately, flaky DB timeouts under parallel runs plagued the CI stabilization effort [demo-infra-tarun-0, demo-infra-rohan-0].

**Missing or incomplete data** killed scope in data-pipeline sessions — the Git LFS budget was exceeded, leaving 10 of 17 datasets as pointer stubs [demo-data-pipeline-dev-0, demo-data-pipeline-neha-0, demo-data-pipeline-arjun-0], and most result directories lacked `results.json`, reducing the curation pipeline to only two qualifying splits [demo-data-pipeline-dev-1, demo-data-pipeline-isha-1].

### 6. [SYNTH/FOUNDER] which sessions did the team abandon?
- note: explicit outcome restriction → outcome=abandoned
- (a) filter: **PASS** — filter ok {'outcome': 'abandoned'}
- (b) citations: **N/A** — refused
- citations: []
- narrative: (refused)

### 7. [SYNTH/FOUNDER] what is the team building in the text-to-sql project?
- note: project aggregate
- (a) filter: **PASS** — filter ok {'project': 'text-to-sql'}
- (b) citations: **PASS** — 5 citations all valid
- citations: ['demo-text-to-sql-arjun-0', 'demo-text-to-sql-suraj-0', 'demo-text-to-sql-vikram-1', 'demo-text-to-sql-neha-1', 'demo-text-to-sql-suraj-1']
- narrative: The team is fine-tuning OmniSQL-7B on the BIRD-bench benchmark using reinforcement learning, specifically MGPO (GRPO) training launched on Modal [demo-text-to-sql-arjun-0, demo-text-to-sql-suraj-0]. They subsequently converted the baseline from LoRA to full-parameter FSDP training to enable a fair comparison between approaches [demo-text-to-sql-vikram-1, demo-text-to-sql-neha-1, demo-text-to-sql-suraj-1].

### 8. [SYNTH/FOUNDER] what went wrong in the ASR / speech work?
- note: project-scoped friction; project≈asr, outcome null
- (a) filter: **PASS** — filter ok {'project': 'ASR'}
- (b) citations: **PASS** — 6 citations all valid
- citations: ['demo-asr-kavya-1', 'demo-asr-dev-1', 'demo-asr-isha-1', 'demo-asr-priya-0', 'demo-asr-atharva-0', 'demo-asr-kavya-0']
- narrative: Two issues surfaced across the ASR work. First, during the base-model diagnosis sessions, a reviewer caught a WER delta inconsistency that required both numbers to be corrected and the chart regenerated [demo-asr-kavya-1, demo-asr-dev-1, demo-asr-isha-1]. Second, when shipping the v3 Hinglish model (Srota), the team hit a dead end with Hugging Face gating: figures inside the gated repo were invisible to users without access because HF gating is all-or-nothing, with no way to make assets selectively public [demo-asr-priya-0, demo-asr-atharva-0, demo-asr-kavya-0].

### 9. [SYNTH/FOUNDER] what did Suraj work on this week?
- note: FOUNDER per-person → k-anon refuses
- (a) filter: **PASS** — filter ok {'actor': 'Suraj'}
- (b) citations: **N/A** — refused
- citations: []
- narrative: (refused)

### 10. [SYNTH/MANAGER] what did Suraj work on this week?
- note: MANAGER per-person → allowed + audited
- (a) filter: **PASS** — filter ok {'actor': 'suraj@acme.demo'}
- (b) citations: **N/A** — refused
- citations: []
- narrative: (refused)

## Auto-score summary
- (a) filter correct: 9/10
- (b) citations accurate: 6/6 (of queries that returned citations)
