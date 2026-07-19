"""Re-compact the 10 validation sessions with the CURRENT compactor.

Agents no longer call an LLM: compaction is deterministic and local, and the
qualitative fields (task_intent beyond the fallback, approach, outcome, friction)
are written server-side by the enrichment pass on the operator's metered key.
So this harness no longer spends tokens and no longer has a per-call cost to
report — ``call_cost_usd`` is server-side now. What it still measures, per
session: whether the session carried the coding agent's own native compaction
(``native_summary``), the digest's est_cost_usd (API list-price equivalent of the
ORIGINAL session) + total_tokens, and the wall duration of the local pass.

Judging digest QUALITY now requires the enriched digest from the server, not this
script's output — see validation/founder_queries.py.

Run:  uv run python validation/recompact_ten.py
Free (no model calls). Writes validation/recompact_results.json.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from manthana.agent.compact import compact_session
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

    rows: list[dict[str, object]] = []
    with_native = 0
    for label, sid in TEN:
        session = store.get_session(sid)
        if session is None:
            print(f"  SKIP {label}: session {sid} not found")
            continue
        turns = len(store.get_turns(sid))
        t0 = time.monotonic()
        comp = compact_session(store, sid)
        dur = time.monotonic() - t0
        if comp is None:
            print(f"  SKIP {label}: compaction returned None")
            continue
        # ``native_summary`` is the coding agent's OWN compaction, when the session
        # had one — it is what the server prefers over the raw transcript when
        # enriching, so its hit rate across the slate is the number worth watching.
        native = comp.native_summary
        if native:
            with_native += 1
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
            "native_summary_chars": len(native) if native else 0,
            "local_seconds": round(dur, 2),
            "files_touched": len(comp.files_touched),
        }
        rows.append(row)
        print(
            f"  {label:11} src={comp.source:13} turns={turns:5} "
            f"native={'yes' if native else ' no':3} {dur:5.2f}s  "
            f"files={len(comp.files_touched):3} tokens={comp.total_tokens}"
        )

    out = {
        "sessions": len(rows),
        "with_native_summary": with_native,
        "rows": rows,
    }
    dest = Path(__file__).parent / "recompact_results.json"
    dest.write_text(json.dumps(out, indent=2))
    print(
        f"\n{len(rows)} sessions re-compacted locally (free); "
        f"{with_native} carried the agent's own compaction. "
        f"Qualitative fields are written by the server enrichment pass."
    )
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
