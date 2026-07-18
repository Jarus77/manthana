"""v0 compaction prompt template.

A fixed template plus the session's normalized turns serialized as compact JSON;
the model is instructed to return a single ``EngineeringCompaction``-shaped JSON
object (decisions doc). Treated as a v0 prompt to refine after the first ~20 real
compactions. Turn content is bounded to keep the prompt size sane.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json

from manthana.schemas import Session, Turn

PROMPT_VERSION = "v2"

_MAX_TURNS = 400
_HEAD_TURNS = 250
_TAIL_TURNS = 150  # _HEAD_TURNS + _TAIL_TURNS == _MAX_TURNS; always keep the ENDING
_MAX_CHARS = 600

_INSTRUCTIONS = """\
You are Manthana's compactor. Summarize ONE engineering session into a structured
digest. Read the turns (a JSON array of {seq, role, text, tool}) and return ONLY a
single JSON object — no prose, no code fences — with EXACTLY these keys:

  task_intent: string  (what the engineer set out to do)
  approach: string  (how they went about it, 1-3 sentences. NAME the exact data
      sources / files / datasets accessed — specific CSV/file names, tables — and
      the key tools, libraries, and commands used, e.g. pandas, grep, the column
      filtered on. For a surprising or counterintuitive RESULT, add one clause on
      the likely mechanism, caveat, or confound — not just the result.)
  artifacts: string[]  (concrete things produced — name each file/output, and for
      a short answer include the actual value, e.g. "answer.txt: 27 states")
  outcome: "success" | "partial" | "abandoned"
  reusable_pattern: boolean  (is there a generalizable pattern worth reusing?)
  friction_points: array of { "category": one of
      ["loop","tool_error","abandon","retry","deadend"], "description": string,
      "turn_refs": string[] }  (turn seq numbers as strings; [] if unknown)
  files_touched: string[]  (file PATHS read or written — source files AND data files
      like CSVs/.db/.parquet consulted. Give the path or filename ONLY, e.g.
      "data/train.csv"; NOT descriptions, sizes, or table names like "patents (5.4GB)")
  prs_opened: string[]
  tests_added: string[]
  dead_end_branches: string[]
  languages: string[]
  frameworks: string[]

Ground every field in the turns — never invent a file name, number, or fact not
present. Use [] for unknowns. Output JSON only.
"""


def _turn_repr(turn: Turn) -> dict[str, object]:
    text = turn.content or turn.tool_output or ""
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "…"
    item: dict[str, object] = {"seq": turn.seq, "role": str(turn.role), "text": text}
    if turn.tool_name:
        item["tool"] = turn.tool_name
    return item


_SUMMARY_TAIL_TURNS = 40  # when a surface-native summary exists, keep only recent turns


def serialize_turns(turns: list[Turn]) -> str:
    if len(turns) <= _MAX_TURNS:
        items = [_turn_repr(t) for t in turns]
    else:
        # Keep the HEAD and the TAIL — the ending carries the outcome and late
        # friction, which a flat first-N window silently drops. Only the middle is
        # elided; the token budget is unchanged (_HEAD + _TAIL == _MAX_TURNS).
        head = [_turn_repr(t) for t in turns[:_HEAD_TURNS]]
        tail = [_turn_repr(t) for t in turns[-_TAIL_TURNS:]]
        elided = {
            "seq": -1,
            "role": "system",
            "text": f"[… {len(turns) - _HEAD_TURNS - _TAIL_TURNS} middle turns elided …]",
        }
        items = [*head, elided, *tail]
    return json.dumps(items, ensure_ascii=False)


def build_prompt(session: Session, turns: list[Turn], *, claude_summary: str | None = None) -> str:
    header = (
        f"Session: project={session.project} surface={session.surface} "
        f"turns={session.turn_count}"
    )
    if claude_summary:
        # Cheap path: the surface already has a running summary, so feed that +
        # only the most recent turns instead of the whole transcript.
        tail = turns[-_SUMMARY_TAIL_TURNS:]
        return (
            f"{_INSTRUCTIONS}\n{header}\n\n"
            f"PRIOR_SUMMARY (the coding surface's compaction of earlier context):\n"
            f"{claude_summary}\n\nRECENT_TURNS:\n{serialize_turns(tail)}\n"
        )
    return f"{_INSTRUCTIONS}\n{header}\n\nTURNS:\n{serialize_turns(turns)}\n"


__all__ = ["build_prompt", "serialize_turns", "PROMPT_VERSION"]
