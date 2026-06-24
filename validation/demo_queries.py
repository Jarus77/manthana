"""Live founder + manager demo over the seeded synthetic org.

Run `validation/seed_demo_org.py` first. This drives the REAL server pipeline with
the `claude` CLI (no API key) at the **production k-anon floor of 4**, and shows the
whole story:

  1. founder aggregate  — "where did the team spend time on LLM-eval?"   → cited
  2. founder friction   — "what kept failing recently?"                   → cited
  3. founder per-person — "what did Suraj work on this week?"   → INSUFFICIENT (privacy)
  4. MANAGER per-person — same question, manager view          → grounded, cited, LOGGED

(Engineer self-view — "show me MY abandoned sessions" — is demoed separately on your
own real data: `uv run manthana ask "show my abandoned sessions"`.)

Run:  uv run python validation/demo_queries.py
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from manthana.agent.llm.provider import ClaudeCLIProvider
from manthana.server import ServerConfig, ServerStore
from manthana.server.founder import run_query

DB_URL = "sqlite:///./manthana-demo.db"
ORG = "acme-demo"


def _show(title: str, result) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print(f"filter: {result.filter}")
    if result.insufficient_data:
        print("=> INSUFFICIENT DATA (k-anon: won't single out individuals / not enough people)")
        return
    r = result.rollup
    print(f"rollup: sessions={r.session_count} contributors={r.distinct_contributors} "
          f"tokens={r.total_tokens:,}")
    print(f"\n{result.narrative}\n")
    print(f"citations ({len(result.citations)}): {result.citations}")


def main() -> None:
    if not Path("manthana-demo.db").exists():
        raise SystemExit("run `uv run python validation/seed_demo_org.py` first")
    store = ServerStore.open(DB_URL)
    config = ServerConfig(
        jwt_secret="x" * 40, admin_token="adm", manager_token="mgr", k_anon_floor=4
    )
    p = ClaudeCLIProvider()
    print(f"provider={p.name} available={p.available()}  org={ORG}  k_anon_floor=4")

    # 1-2: founder aggregates (k-anon enforced)
    _show(
        "FOUNDER · aggregate — where did the team spend time on LLM evaluation?",
        run_query(store, config, org_id=ORG,
                  query="where did the team spend time on LLM evaluation work?", provider=p),
    )
    _show(
        "FOUNDER · friction — what kept failing recently across the team?",
        run_query(store, config, org_id=ORG,
                  query="what kept failing recently across the team?", provider=p),
    )

    # 3: founder per-person — REFUSED by k-anon (the privacy guarantee)
    q = "what did Suraj work on this week?"
    _show(f"FOUNDER · per-person — {q}  (expect: refused)",
          run_query(store, config, org_id=ORG, query=q, provider=p))

    # 4: manager per-person — ALLOWED + audited
    _show(f"MANAGER · per-person — {q}  (allowed, LOGGED)",
          run_query(store, config, org_id=ORG, query=q, provider=p, allow_individual=True))
    store.record_founder_query(
        org_id=ORG, query=q, insufficient=False, citations=[], individual=True
    )
    print("\n(audit) manager lookup recorded as an individual query — see /v1/admin/audit")


if __name__ == "__main__":
    main()
