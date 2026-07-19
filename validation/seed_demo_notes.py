"""Seed the org WIKI for the demo org: run consolidation over the demo digests.

Layers on top of ``seed_demo_org.py`` (same ``manthana-demo.db``, same
``acme-demo`` org). Two steps:

  1. **Mark the seeded digests enriched.** The seeder writes complete qualitative
     fields but leaves ``source="pending"`` (that is what a real agent emits), and
     consolidation deliberately ignores pending digests — there would be nothing
     qualitative to consolidate. Flipping them to ``"full"`` via ``save_enriched``
     is exactly the state the enrichment pass would leave them in, without
     spending a model call to re-derive fields the seeder already wrote.
  2. **Run the consolidation pass.** By default with a deterministic
     ``ScriptedProvider`` so the demo needs no API key and produces the same wiki
     every time; ``--live`` runs the real Haiku adjudication instead.

Run:  uv run python validation/seed_demo_org.py && uv run python validation/seed_demo_notes.py
      uv run python validation/seed_demo_notes.py --live   # real model

SPDX-License-Identifier: AGPL-3.0-or-later
"""

# ruff: noqa: E501 - demo content strings are intentionally long, single-line for readability
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime

from manthana.server import ServerConfig, ServerStore
from manthana.server.consolidate import consolidate_org
from manthana.server.llm import LLMProvider, MockProvider, make_consolidate_provider
from manthana.server.metering import MeteredProvider
from seed_demo_org import DB_URL, NOW, ORG  # type: ignore[import-not-found]

# One scripted adjudication per project: the durable knowledge a founder would
# actually want on a project page — a decision with its rationale, a gotcha, and
# a benchmark number that can later MOVE (so the home feed shows a delta).
_SCRIPT: dict[str, dict[str, object]] = {
    "llm-eval": {
        "new_notes": [
            {
                "kind": "decision",
                "title": "Evals run headless through `claude -p`, one isolated session per query",
                "body": "Each eval query gets its own session so context never leaks between tasks, and the DB is mounted read-only. Chosen after shared-session runs contaminated later answers with earlier tasks' context.",
                "concepts": ["eval-harness"],
            },
            {
                "kind": "benchmark",
                "title": "DABStep accuracy",
                "body": "Latest full DABStep run scored 41% on hard tasks with Opus 4.8.",
                "metric": "dabstep_hard_accuracy",
                "value": "41%",
            },
            {
                "kind": "gotcha",
                "title": "Cost greps false-positive on credit errors",
                "body": "Grepping run logs for 'credit' matches prompt text, not real billing failures. Check the meter's structured output instead of the transcript.",
            },
        ]
    },
    "text-to-sql": {
        "new_notes": [
            {
                "kind": "decision",
                "title": "Schema linking runs before generation, not inside the prompt",
                "body": "Retrieving the candidate tables first and passing only those keeps prompts small enough to stay on the cheaper tier without hurting execution accuracy.",
                "concepts": ["schema-linking"],
            },
            {
                "kind": "benchmark",
                "title": "BIRD execution accuracy",
                "body": "BIRD dev-set execution accuracy is 61% with the current schema-linking pipeline.",
                "metric": "bird_exec_accuracy",
                "value": "61%",
            },
        ]
    },
    "asr": {
        "new_notes": [
            {
                "kind": "gotcha",
                "title": "Long-audio chunking must overlap or words are lost at boundaries",
                "body": "Fixed-window chunking drops words that straddle a boundary. Use a 2s overlap and de-duplicate on the overlap region.",
            },
            {
                "kind": "convention",
                "title": "All ASR eval audio is resampled to 16kHz mono before scoring",
                "body": "Mixed sample rates silently changed WER between runs. Normalise on ingest so numbers are comparable across experiments.",
            },
        ]
    },
    "infra": {
        "new_notes": [
            {
                "kind": "decision",
                "title": "Deploys are one-way: roll forward, never roll back the database",
                "body": "Schema changes are additive so a bad deploy is fixed by shipping again rather than reversing a migration.",
            },
            {
                "kind": "failure_pattern",
                "title": "Background jobs die silently when the pass swallows its own exception",
                "body": "A background pass that catches everything to stay alive also hides real failures. Always log the exception and surface a counter, or the job looks healthy while doing nothing.",
            },
        ]
    },
    "data-pipeline": {
        "new_notes": [
            {
                "kind": "convention",
                "title": "Every pipeline stage writes a manifest with row counts",
                "body": "Silent row loss between stages was invisible until the end. A per-stage manifest makes the drop obvious at the stage that caused it.",
            },
        ]
    },
}

# A later session MOVES the BIRD number. This must be a `refines` verdict, not a
# new note: the home feed reads a benchmark delta off the supersedes chain, so
# 61% → 64% only shows if the new number is a VERSION of the old claim. (A
# same-titled new note would be deduped into a plain "supports".)
_REFINE_TITLE = "BIRD execution accuracy"
_REFINE_BODY = (
    "BIRD dev-set execution accuracy is now 64% after fixing the harness's "
    "stale-prediction cache."
)
_REFINE_VALUE = "64%"

_NOTE_ID_RE = re.compile(r"\[(kn-[0-9a-f]+)\] kind=\S+.*?\n\s*title: (.+)")


class DemoProvider:
    """Deterministic adjudication: the first session in a project lays down that
    project's notes, the second refines the benchmark, and the rest agree — so
    the wiki looks lived-in and re-running the pass never multiplies notes."""

    name = "demo"

    def __init__(self) -> None:
        self.seen: Counter[str] = Counter()
        self.calls = 0

    @staticmethod
    def _find_note(prompt: str, title: str) -> str | None:
        """Pull a candidate note's id out of the prompt by its title."""
        for note_id, found in _NOTE_ID_RE.findall(prompt):
            if found.strip() == title:
                return note_id
        return None

    def complete(self, prompt: str) -> str:
        self.calls += 1
        project = ""
        for line in prompt.splitlines():
            if line.strip().startswith("project:"):
                project = line.split(":", 1)[1].strip()
                break
        self.seen[project] += 1
        nth = self.seen[project]
        if nth == 1 and project in _SCRIPT:
            return json.dumps(_SCRIPT[project])
        if project == "text-to-sql":
            note_id = self._find_note(prompt, _REFINE_TITLE)
            if note_id is not None and nth == 2:
                return json.dumps(
                    {
                        "verdicts": [
                            {
                                "note_id": note_id,
                                "relation": "refines",
                                "updated_body": _REFINE_BODY,
                                "value": _REFINE_VALUE,
                            }
                        ],
                        "new_notes": [],
                    }
                )
        # Everything after: support whatever already exists for this project, so
        # notes accrue evidence and get promoted to "established" naturally.
        supports = [
            {"note_id": nid, "relation": "supports"}
            for nid, _title in _NOTE_ID_RE.findall(prompt)
        ]
        return json.dumps({"verdicts": supports, "new_notes": []})


def _mark_enriched(store: ServerStore) -> int:
    """Flip seeded digests from ``pending`` to ``full`` — the state the enrichment
    pass would leave them in. The seeder already wrote every qualitative field."""
    moved = 0
    for c in store.query_compactions(org_id=ORG, limit=100_000):
        if c.source != "pending":
            continue
        c.source = "full"
        if store.save_enriched(c, org_id=ORG):
            moved += 1
    return moved


def _provider(live: bool, config: ServerConfig, store: ServerStore) -> LLMProvider:
    if not live:
        return DemoProvider()
    inner = make_consolidate_provider(config)
    if isinstance(inner, MockProvider):
        print("!! --live asked for a real model but none is configured "
              "(set MANTHANA_SERVER_LLM=anthropic + ANTHROPIC_API_KEY); using the mock")
    cap = store.get_org_quota(ORG) or config.llm_monthly_cap_usd
    return MeteredProvider(inner, store, ORG, cap)


def main() -> None:
    live = "--live" in sys.argv
    store = ServerStore.open(DB_URL)
    if store.get_org(ORG) is None:
        raise SystemExit(
            f"org {ORG!r} not found in {DB_URL} — run validation/seed_demo_org.py first"
        )
    config = ServerConfig.from_env() if live else ServerConfig(
        jwt_secret="x" * 40, admin_token="demo-admin-token"
    )

    moved = _mark_enriched(store)
    print(f"marked {moved} digest(s) enriched (source: pending → full)")

    provider = _provider(live, config, store)
    total = 0
    # Loop until the backlog is drained; each pass is bounded per org.
    while store.list_unconsolidated(ORG, limit=1):
        stats = consolidate_org(
            store, provider, config, org_id=ORG,
            limit=config.consolidate_batch_per_org, now=NOW or datetime.now(UTC),
        )
        if not (stats.consolidated or stats.failed):
            break  # nothing progressed (quota / all abandoned) — stop rather than spin
        total += stats.consolidated
        print(f"  pass: {stats.as_dict()}")

    notes = store.query_notes(ORG)
    by_kind = Counter(str(n.kind) for n in notes)
    by_status = Counter(str(n.status) for n in notes)
    print(f"consolidated {total} digest(s) → {len(notes)} live note(s)")
    print("  by kind:  ", dict(by_kind))
    print("  by status:", dict(by_status))
    print()
    print("Next: boot the server against the demo DB and open the wiki —")
    print(f"  MANTHANA_SERVER_DB_URL={DB_URL} \\")
    print("  MANTHANA_SERVER_JWT_SECRET=$(openssl rand -hex 32) \\")
    print("  MANTHANA_SERVER_ADMIN_TOKEN=demo-admin-token \\")
    print("  uv run uvicorn manthana.server.app:build_default_app --factory --port 8000")
    print("  → http://127.0.0.1:8000/ui/home  (sign in with the admin token)")


if __name__ == "__main__":
    main()
