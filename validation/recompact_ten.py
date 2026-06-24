"""Re-compact the 10 validation sessions with the CURRENT compactor, cost-tracked.

The stored digests are stale (pre-fix v1: no total_tokens, no head+tail window, no
call-cost capture). This re-runs each of the same 10 sessions through the real
``claude -p`` provider and records, per session: the input source (full transcript vs
Claude's own summary), the digest's est_cost_usd (API list-price equivalent of the
ORIGINAL session) + total_tokens, and the ACTUAL compaction-call cost + wall duration.

Run:  uv run python validation/recompact_ten.py
Spends real tokens (10 CLI calls). Writes validation/recompact_results.json.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from manthana.agent.compact import compact_session
from manthana.agent.llm import ClaudeCLIProvider
from manthana.agent.store import Store

# The same 10 sessions as the first validation run (verified present in the store).
TEN = [
    ("scribe", "59896ed2-495e-4661-a0a5-ec85c8318ed9.11"),
    ("manthana", "138db3b9-4762-4d01-8633-2764c60b197c.2"),
    ("bird-bench", "1782ce84-8cc9-44c7-9f70-c17a85fb1111.7"),
    ("dab_clone", "7f09f08c-a4c3-4360-ab62-6063864bdce9"),
    ("data", "10375d20-b5a4-4e08-8d51-198705175757"),
    ("harness", "b636d78d-c0c8-4e94-a7c7-52f8d0a8dfa3.96"),
    ("hinglish", "2eddfe69-cd5e-46c8-bfae-a3b77eefe20f.7"),
    ("tts", "a8aeb113-5f21-41cd-8e7d-3a9ecb6d8cf7.12"),
    ("grafting", "4811a97d-8461-4c22-b9b1-d4951bd853ab"),
    ("trajectory", "2647d07f-646e-444d-b3a6-c16c94f67b70"),
]


def main() -> None:
    store = Store.open()
    provider = ClaudeCLIProvider(timeout=300)
    if not provider.available():
        raise SystemExit("claude CLI not available — cannot run the live re-compaction")

    rows: list[dict[str, object]] = []
    total_call_cost = 0.0
    for label, sid in TEN:
        session = store.get_session(sid)
        if session is None:
            print(f"  SKIP {label}: session {sid} not found")
            continue
        turns = len(store.get_turns(sid))
        t0 = time.monotonic()
        comp = compact_session(store, sid, provider=provider)
        dur = time.monotonic() - t0
        if comp is None:
            print(f"  SKIP {label}: compaction returned None")
            continue
        call_cost = comp.call_cost_usd or 0.0
        total_call_cost += call_cost
        row = {
            "label": label,
            "session_id": sid,
            "project": comp.project,
            "turns": turns,
            "source": comp.source,
            "outcome": comp.outcome.value,
            "est_cost_usd": comp.est_cost_usd,
            "total_tokens": comp.total_tokens,
            "tier_used": comp.tier_used,
            "call_cost_usd": round(call_cost, 6),
            "call_seconds": round(dur, 1),
            "files_touched": len(comp.files_touched),
            "friction_points": len(comp.friction_points),
            "usage": provider.last_usage,
        }
        rows.append(row)
        print(
            f"  {label:11} src={comp.source:13} turns={turns:5} "
            f"call=${call_cost:.4f} {dur:5.1f}s  files={len(comp.files_touched):3} "
            f"friction={len(comp.friction_points)} -> {comp.outcome.value}"
        )

    out = {
        "sessions": len(rows),
        "total_call_cost_usd": round(total_call_cost, 4),
        "avg_call_cost_usd": round(total_call_cost / len(rows), 4) if rows else 0.0,
        "rows": rows,
    }
    dest = Path(__file__).parent / "recompact_results.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"\n{len(rows)} sessions re-compacted; total call cost "
          f"${total_call_cost:.4f}; avg ${out['avg_call_cost_usd']}/compaction")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
