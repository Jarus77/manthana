"""Founder-narrative groundedness harness.

Loads the 10 validated compactions into an in-memory ServerStore, runs the REAL
founder pipeline (filter-parse + narrative) with the `claude` CLI as the provider
(no API key needed), and verifies every citation the narrative emits actually
exists in the visible set — i.e. is the narrative grounded, or does it cite things
it shouldn't / miss things it should?

k_anon_floor is set to 1 here (all 10 compactions share one actor) so data flows;
the privacy floor (4) has its own dedicated tests. This harness validates the
filter→query→citation logic the founder UI depends on.

Usage: uv run python validation/founder_check.py
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from manthana.agent.llm.provider import ClaudeCLIProvider
from manthana.schemas import EngineeringCompaction
from manthana.server import ServerConfig, ServerStore
from manthana.server.founder import run_query

DB = str(Path.home() / ".manthana" / "manthana.db")

SLATE_IDS = [
    "comp-59896ed2-495e-4661-a0a5-ec85c8318ed9.11",
    "comp-138db3b9-4762-4d01-8633-2764c60b197c.2",
    "comp-1782ce84-8cc9-44c7-9f70-c17a85fb1111.7",
    "comp-7f09f08c-a4c3-4360-ab62-6063864bdce9",
    "comp-10375d20-b5a4-4e08-8d51-198705175757",
    "comp-b636d78d-c0c8-4e94-a7c7-52f8d0a8dfa3.96",
    "comp-2eddfe69-cd5e-46c8-bfae-a3b77eefe20f.7",
    "comp-a8aeb113-5f21-41cd-8e7d-3a9ecb6d8cf7.12",
    "comp-4811a97d-8461-4c22-b9b1-d4951bd853ab",
    "comp-2647d07f-646e-444d-b3a6-c16c94f67b70",
]

QUERIES = [
    "what is the team working on this week?",
    "what went wrong or failed recently across the team?",
]


def load_compactions() -> list[EngineeringCompaction]:
    con = sqlite3.connect(DB)
    out = []
    for cid in SLATE_IDS:
        row = con.execute("SELECT data FROM compaction WHERE id=?", (cid,)).fetchone()
        if not row:
            print(f"  (missing {cid})")
            continue
        data = json.loads(row[0])
        data["released"] = True  # mark released so the server accepts ingest
        out.append(EngineeringCompaction.model_validate(data))
    con.close()
    return out


def main() -> None:
    comps = load_compactions()
    print(f"loaded {len(comps)} compactions\n")

    store = ServerStore.open("sqlite://")
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=1)
    store.create_org("o1", "Acme")
    for c in comps:
        store.ingest_compaction(c, org_id="o1", team_id="t1")

    by_id = {c.id: c for c in comps}
    provider = ClaudeCLIProvider()
    print(f"provider: {provider.name}, available={provider.available()}\n")

    for q in QUERIES:
        print("=" * 78)
        print(f"QUERY: {q}")
        print("=" * 78)
        # NOTE: parse once via run_query; the CLI is non-deterministic, so a separate
        # parse_filter call would print a filter that differs from the one actually used.
        result = run_query(store, config, org_id="o1", query=q, provider=provider)
        print(f"PARSED FILTER (as used): {result.filter}")
        if result.insufficient_data:
            print("=> insufficient_data (rollup below floor or provider failure)")
            print(f"   rollup: {result.rollup}")
            continue
        print(f"\nROLLUP: {result.rollup}")
        print(f"\nNARRATIVE:\n{result.narrative}\n")
        print(f"CITATIONS ({len(result.citations)}): {result.citations}")

        # Verify: each citation exists and the narrative's claim matches the comp.
        print("\nCITATION VERIFICATION:")
        for cit in result.citations:
            c = by_id.get(cit)
            if not c:
                # try prefix (the matcher allows unique-prefix citations)
                cand = [x for x in by_id if x.startswith(cit)]
                c = by_id[cand[0]] if len(cand) == 1 else None
            if c:
                print(f"  ✅ {cit[:30]:30} -> {c.project}: {c.task_intent[:70]}")
            else:
                print(f"  ❌ {cit[:30]:30} -> NO MATCHING COMPACTION (ungrounded!)")
        print()


if __name__ == "__main__":
    main()
