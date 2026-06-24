"""Seed a SYNTHETIC demo org for the founder/manager demo — deterministic, no LLM.

Creates ~12 named engineers across 5 projects with varied outcomes, realistic
friction, tokens/cost, and dates spread over the last ~4 weeks, into an ISOLATED
demo database (``sqlite:///./manthana-demo.db``). Every actor is on the
``@acme.demo`` domain so it's obviously synthetic; this never touches your real
local store. Each project has >=4 contributors so founder aggregates clear the
k-anonymity floor.

Run:  uv run python validation/seed_demo_org.py
SPDX-License-Identifier: AGPL-3.0-or-later
"""

# ruff: noqa: E501 - demo content strings are intentionally long, single-line for readability
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from manthana.schemas import (
    EngineeringCompaction,
    FrictionCategory,
    FrictionPoint,
    Outcome,
    Surface,
)
from manthana.server import ServerStore

DB_PATH = Path("manthana-demo.db")
DB_URL = f"sqlite:///./{DB_PATH.name}"
ORG = "acme-demo"
TEAM = "platform"
NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

# project -> contributors (>=4 each so founder aggregates clear k-anon floor 4)
PROJECTS: dict[str, list[str]] = {
    "llm-eval": ["suraj", "tarun", "atharva", "priya", "rohan"],
    "text-to-sql": ["suraj", "vikram", "neha", "arjun"],
    "asr": ["atharva", "kavya", "dev", "isha", "priya"],
    "infra": ["tarun", "rohan", "vikram", "kavya"],
    "data-pipeline": ["neha", "arjun", "dev", "isha"],
}

# per-project content pools (intent, approach, friction) — cycled by index.
CONTENT: dict[str, dict[str, list]] = {
    "llm-eval": {
        "intent": [
            "Evaluate Claude vs GPT on the DABStep data-analysis benchmark and log per-task cost",
            "Run cross-model ablations (Opus 4.7 vs 4.8) on the DAB submission and compare accuracy",
            "Build a leaderboard submission with traces for the KramaBench eval suite",
        ],
        "approach": [
            "Wrote a headless harness driving `claude -p`, per-query isolated sessions, "
            "read-only DB hardening, and a pass-through token meter",
            "Cleaned leaked specs from prompts, hardened the sandbox preamble, ran configs "
            "through the ablation script",
        ],
        "friction": [
            (FrictionCategory.tool_error, "Anthropic API rate limit interrupted a long eval run; resumed on continue"),
            (FrictionCategory.deadend, "Cost grep false-positives suggested credit errors that did not exist"),
            (FrictionCategory.retry, "Spec extraction hit the token cap; bumped max_tokens and added a forced-save fallback"),
        ],
    },
    "text-to-sql": {
        "intent": [
            "Launch full-parameter MGPO RL fine-tuning of OmniSQL-7B for the BIRD-bench track on Modal",
            "Convert the GRPO baseline from LoRA to full-parameter FSDP for a fair comparison",
        ],
        "approach": [
            "Worked in the verl framework on H100s with a custom SQL reward and a reward-variance "
            "frontier filter generating the RL training pool",
            "Reconfigured offload + micro-batch sizes so the baseline matches the MGPO run",
        ],
        "friction": [
            (FrictionCategory.tool_error, "Edit to verl_grpo_baseline.py failed on a trailing comment; grepped exact lines before re-editing"),
            (FrictionCategory.retry, "Background run monitors timed out at the 1h cap and had to be re-armed"),
        ],
    },
    "asr": {
        "intent": [
            "Ship the v3 Hinglish ASR model (Srota), publish a gated HF model card and a public demo",
            "Diagnose why the base model transliterates English into Devanagari and quantify the gap",
        ],
        "approach": [
            "Parallelized planner->executor->reviewer subagents, pushed weights to the HF Hub, built a "
            "Gradio Space on free CPU",
            "Tokenized Hinglish strings with the released BPE model and measured vocab composition",
        ],
        "friction": [
            (FrictionCategory.deadend, "Figures inside the gated repo were invisible to users without access; HF gating is all-or-nothing"),
            (FrictionCategory.retry, "Reviewer caught a WER delta inconsistency; both numbers fixed and the chart regenerated"),
        ],
    },
    "infra": {
        "intent": [
            "Stabilize the CI pipeline and cut flaky integration-test failures",
            "Containerize the service and wire docker compose for the full stack",
        ],
        "approach": [
            "Raised the wait budget, isolated the StaticPool thread race by moving tests to a file-based store",
            "Wrote a slim Dockerfile, added healthchecks, published the image to GHCR",
        ],
        "friction": [
            (FrictionCategory.tool_error, "Flaky DB timeout on the integration suite under parallel runs"),
            (FrictionCategory.loop, "Repeated ruff/pyright failures on long inline f-strings; wrapped the lines"),
        ],
    },
    "data-pipeline": {
        "intent": [
            "Recover lost LFS datasets and validate the parquet ETL end to end",
            "Build the curation pipeline that joins task difficulty with scores and cost",
        ],
        "approach": [
            "Recovered files via the media.githubusercontent bypass with exact byte-size validation",
            "Scanned all result dirs, joined difficulty with score, summed cost into a report",
        ],
        "friction": [
            (FrictionCategory.deadend, "Git LFS budget exceeded; 10 of 17 datasets were pointer stubs, capping scope to file DBs"),
            (FrictionCategory.abandon, "Most result dirs were single-task probes lacking results.json; only two full splits qualified"),
        ],
    },
}

_OUTCOMES = [Outcome.success, Outcome.success, Outcome.partial, Outcome.success, Outcome.abandoned]


def _build() -> list[EngineeringCompaction]:
    comps: list[EngineeringCompaction] = []
    idx = 0
    for project, engineers in PROJECTS.items():
        pool = CONTENT[project]
        for eng in engineers:
            for n in range(2):  # 2 sessions per (engineer, project)
                started = NOW - timedelta(days=(idx * 2) % 26, hours=(idx * 5) % 24)
                fr_cat, fr_desc = pool["friction"][idx % len(pool["friction"])]
                outcome = _OUTCOMES[idx % len(_OUTCOMES)]
                tokens = 180_000 + (idx % 9) * 45_000
                cid = f"demo-{project}-{eng}-{n}"
                comps.append(
                    EngineeringCompaction(
                        id=cid,
                        session_id=cid,
                        actor=f"{eng}@acme.demo",
                        surface=Surface.claude_code,
                        project=project,
                        started_at=started,
                        ended_at=started + timedelta(minutes=40 + idx % 90),
                        duration_seconds=float(2400 + (idx % 90) * 60),
                        task_intent=pool["intent"][idx % len(pool["intent"])],
                        approach=pool["approach"][idx % len(pool["approach"])],
                        outcome=outcome,
                        friction_points=[
                            FrictionPoint(category=fr_cat, description=fr_desc, turn_refs=[str(10 + n)])
                        ],
                        tier_used="opus",
                        est_cost_usd=round(tokens * 1.5 / 1e6, 4),
                        total_tokens=tokens,
                        files_touched=[f"src/{project.replace('-', '_')}/run_{n}.py"],
                        languages=["python"],
                        released=True,
                        released_at=started,
                    )
                )
                idx += 1
    return comps


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()  # clean, reproducible slate
    store = ServerStore.open(DB_URL)
    store.create_org(ORG, "Acme (DEMO)")
    comps = _build()
    for c in comps:
        store.ingest_compaction(c, org_id=ORG, team_id=TEAM)
    actors = sorted({c.actor for c in comps})
    print(f"seeded {len(comps)} synthetic compactions into {DB_URL}")
    print(f"org={ORG} team={TEAM}  engineers={len(actors)}  projects={len(PROJECTS)}")
    print("engineers:", ", ".join(a.split('@')[0] for a in actors))
    # quick sanity: contributors per project (all should be >=4 for k-anon)
    from collections import defaultdict

    per_proj: dict[str, set] = defaultdict(set)
    for c in comps:
        per_proj[c.project].add(c.actor)
    print("contributors/project:", {p: len(s) for p, s in per_proj.items()})


if __name__ == "__main__":
    main()
