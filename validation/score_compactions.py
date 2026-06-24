"""Empirical compaction-quality harness.

For each given session id, pull the compaction and the session's raw turns from
the local store and lay the LLM-produced fields next to ground truth extracted
from the transcript:

  - files_touched      vs real file tools (Edit/Write/Read/MultiEdit/NotebookEdit)
  - est_cost_usd/tier  vs an independent token sum + the rate table actually used
  - outcome            vs how the session actually ends (last turns)
  - friction_points    vs real error/tool-error signal; turn_refs validated to exist
  - task_intent        vs the first user turn (the literal ask)

Objective checks are computed here; subjective "did it match my memory" calls are
left for the human. Output is a markdown worksheet.

Usage: uv run python validation/score_compactions.py
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB = os.environ.get("MANTHANA_DB", str(Path.home() / ".manthana" / "manthana.db"))

# (project, session_id) — the validation slate.
SLATE = [
    ("scribe", "59896ed2-495e-4661-a0a5-ec85c8318ed9.11"),
    ("manthana", "138db3b9-4762-4d01-8633-2764c60b197c.2"),
    ("bird-bench", "1782ce84-8cc9-44c7-9f70-c17a85fb1111.7"),
    ("dab_clone", "7f09f08c-a4c3-4360-ab62-6063864bdce9"),
    ("data", "10375d20-b5a4-4e08-8d51-198705175757"),
    ("harness", "b636d78d-c0c8-4e94-a7c7-52f8d0a8dfa3.96"),
    ("hinglish", "2eddfe69-cd5e-46c8-bfae-a3b77eefe20f.7"),
    ("TTS", "a8aeb113-5f21-41cd-8e7d-3a9ecb6d8cf7.12"),
    ("grafting_v2", "4811a97d-8461-4c22-b9b1-d4951bd853ab"),
    ("trajectory_eval", "2647d07f-646e-444d-b3a6-c16c94f67b70"),
]

FILE_TOOLS = {"Edit", "Write", "Read", "MultiEdit", "NotebookEdit"}
MAX_TURNS_PROMPT = 400  # mirrors compactor/prompt.py _MAX_TURNS


@dataclass
class Truth:
    turn_count: int = 0
    max_seq: int = 0
    files: set[str] = field(default_factory=set)  # absolute/raw paths
    first_user: str = ""
    last_turns: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_w: int = 0
    cache_r: int = 0
    model: str | None = None
    error_turns: int = 0
    tool_error_turns: int = 0


def _base(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def load_turns(con: sqlite3.Connection, sid: str) -> Truth:
    rows = con.execute(
        "SELECT seq, role, data FROM turn WHERE session_id=? ORDER BY seq", (sid,)
    ).fetchall()
    t = Truth(turn_count=len(rows))
    last_buf: list[str] = []
    for seq, role, raw in rows:
        d = json.loads(raw)
        t.max_seq = max(t.max_seq, seq)
        t.tokens_in += d.get("tokens_in") or 0
        t.tokens_out += d.get("tokens_out") or 0
        t.cache_w += d.get("cache_creation_tokens") or 0
        t.cache_r += d.get("cache_read_tokens") or 0
        if d.get("model"):
            t.model = d["model"]
        if d.get("error"):
            t.error_turns += 1
        out = d.get("tool_output") or ""
        if isinstance(out, str) and ("is_error" in out or "Error" in out[:80]):
            t.tool_error_turns += 1
        name = d.get("tool_name")
        ti = d.get("tool_input") or {}
        if name in FILE_TOOLS:
            fp = ti.get("file_path") or ti.get("notebook_path")
            if fp:
                t.files.add(fp)
        content = d.get("content") or ""
        if role == "user" and content and not t.first_user and not content.startswith("<"):
            t.first_user = content[:300]
        snippet = (content or out)[:160].replace("\n", " ")
        if snippet:
            last_buf.append(f"[{seq} {role}] {snippet}")
    t.last_turns = last_buf[-6:]
    return t


def load_compaction(con: sqlite3.Connection, sid: str) -> dict | None:
    row = con.execute(
        "SELECT data FROM compaction WHERE session_id=? ORDER BY started_at DESC LIMIT 1",
        (sid,),
    ).fetchone()
    return json.loads(row[0]) if row else None


def rates_for(model: str | None) -> dict:
    try:
        from manthana.agent.cost.rates import get_rates  # type: ignore

        return get_rates(model)
    except Exception:
        return {}


def fmt(n: int) -> str:
    return f"{n:,}"


def section(project: str, sid: str, comp: dict | None, t: Truth) -> str:
    lines: list[str] = []
    src = (comp or {}).get("source", "—")
    mid_elided = t.max_seq > MAX_TURNS_PROMPT and src != "claude_summary"
    lines.append(f"## {project} — `{sid[:24]}`")
    lines.append("")
    if comp is None:
        lines.append("> **NO COMPACTION FOUND** (compaction step did not produce a row).")
        lines.append("")
        return "\n".join(lines)
    # head+tail window (250+150): the ENDING is always kept; only the middle is elided.
    tail_note = (
        f"head+tail kept, ~{t.max_seq - MAX_TURNS_PROMPT} middle turns elided (ending intact)"
        if mid_elided
        else "full fidelity to compactor"
    )
    lines.append(
        f"- **turns:** {t.turn_count} (max seq {t.max_seq}) · **path:** `{src}` · " + tail_note
    )
    lines.append("")

    # task_intent vs literal first ask
    lines.append("### task_intent")
    lines.append(f"- **compaction:** {comp.get('task_intent','')}")
    lines.append(f"- **first user turn (literal ask):** {t.first_user or '(none captured)'}")
    lines.append("- **faithfulness (mine):** _TBD_   **matches my memory (you):** ☐")
    lines.append("")

    # approach
    lines.append("### approach")
    lines.append(f"- **compaction:** {comp.get('approach','')}")
    lines.append("- **faithfulness (mine):** _TBD_   **matches my memory (you):** ☐")
    lines.append("")

    # outcome vs ending
    lines.append("### outcome")
    lines.append(f"- **compaction:** `{comp.get('outcome','')}`")
    lines.append("- **how the session actually ends (last turns):**")
    for s in t.last_turns:
        lines.append(f"    - {s}")
    lines.append("- **right answer? (mine):** _TBD_   **(you):** ☐")
    lines.append("")

    # files_touched — precision/recall vs real file tools (by basename)
    listed = {f for f in comp.get("files_touched", [])}
    listed_base = {_base(f) for f in listed}
    real_base = {_base(f) for f in t.files}
    hit = listed_base & real_base
    missing = real_base - listed_base
    halluc = listed_base - real_base
    rec = f"{len(hit)}/{len(real_base)}" if real_base else "n/a"
    lines.append("### files_touched")
    lines.append(
        f"- **listed:** {len(listed)} · **real file-tool targets in transcript:** {len(real_base)} "
        f"· **recall:** {rec} · **not-in-transcript (possible halluc/inferred):** {len(halluc)}"
    )
    if missing:
        lines.append(f"- **MISSED (real, not listed):** {', '.join(sorted(missing)[:25])}")
    if halluc:
        lines.append(f"- **listed but not a file-tool target:** {', '.join(sorted(halluc)[:25])}")
    lines.append("")

    # friction
    fps = comp.get("friction_points", [])
    valid_seqs = set()
    for fp in fps:
        for r in fp.get("turn_refs", []):
            try:
                valid_seqs.add(int(r))
            except (ValueError, TypeError):
                pass
    bad_refs = sorted(s for s in valid_seqs if s > t.max_seq)
    lines.append("### friction_points")
    lines.append(
        f"- **compaction has:** {len(fps)} · **real error signal:** "
        f"{t.error_turns} errored turns, {t.tool_error_turns} tool-error turns"
    )
    for fp in fps:
        lines.append(
            f"    - `{fp.get('category','')}` — {fp.get('description','')[:160]} "
            f"(refs: {fp.get('turn_refs', [])})"
        )
    if bad_refs:
        lines.append(f"- **⚠ INVALID turn_refs (> max seq {t.max_seq}):** {bad_refs}")
    lines.append("- **real frustrations captured? (you):** ☐")
    lines.append("")

    # cost — three distinct numbers; don't conflate them
    r = rates_for(t.model)
    call = comp.get("call_cost_usd")
    call_str = f"${call:.4f}" if call is not None else "—"
    lines.append("### cost (three distinct numbers)")
    lines.append(
        f"- **call_cost_usd (REAL spend of THIS compaction call):** {call_str}"
    )
    lines.append(
        f"- **est_cost_usd (API list-price *equivalent* of the ORIGINAL session, "
        f"cache-read dominated — NOT real spend):** ${comp.get('est_cost_usd')} · tier "
        f"`{comp.get('tier_used')}` · model `{t.model}`"
    )
    lines.append(
        f"- **total_tokens (magnitude):** {fmt(comp.get('total_tokens') or 0)} "
        f"(digest) · independent transcript sum: in={fmt(t.tokens_in)} out={fmt(t.tokens_out)} "
        f"cache_w={fmt(t.cache_w)} cache_r={fmt(t.cache_r)}"
    )
    if r:
        lines.append(f"- **rates used ($/Mtok):** {r}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    con = sqlite3.connect(DB)
    out: list[str] = ["# Compaction validation worksheet", "", f"_db: {DB}_", ""]
    summary: list[str] = [
        "| project | turns | path | window | files recall | friction | outcome "
        "| call cost | est (list-equiv) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for project, sid in SLATE:
        t = load_turns(con, sid)
        comp = load_compaction(con, sid)
        out.append(section(project, sid, comp, t))
        if comp:
            src = comp.get("source", "—")
            elided = t.max_seq > MAX_TURNS_PROMPT and src != "claude_summary"
            window = "head+tail" if elided else "full"
            real_base = {_base(f) for f in t.files}
            listed_base = {_base(f) for f in comp.get("files_touched", [])}
            rec = f"{len(listed_base & real_base)}/{len(real_base)}" if real_base else "n/a"
            call = comp.get("call_cost_usd")
            call_s = f"${call:.4f}" if call is not None else "—"
            summary.append(
                f"| {project} | {t.turn_count} | {src} | {window} | {rec} | "
                f"{len(comp.get('friction_points', []))} | {comp.get('outcome')} | "
                f"{call_s} | ${comp.get('est_cost_usd')} |"
            )
        else:
            summary.append(
                f"| {project} | {t.turn_count} | — | — | — | NO COMPACTION | — | — | — |"
            )
    final = "\n".join(out[:4] + summary + ["", ""] + out[4:])
    Path("validation/worksheet.md").write_text(final)
    print(final)


if __name__ == "__main__":
    main()
