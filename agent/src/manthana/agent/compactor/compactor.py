"""The compactor: a session + its turns -> a validated EngineeringCompaction.

The LLM provides the qualitative fields (intent, approach, outcome, friction,
artifacts, files/languages/frameworks); Manthana fills the deterministic fields
from its own data (ids, timestamps, duration, and cost/tier from the cost module
— never trusting the LLM for cost). Parsing is defensive: malformed/empty LLM
output degrades to a grounded fallback instead of crashing the pipeline.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from manthana.schemas import (
    EngineeringCompaction,
    FrictionCategory,
    FrictionPoint,
    Outcome,
    Session,
    Turn,
)

from ..cost import estimate_cost
from ..llm import LLMProvider
from .prompt import PROMPT_VERSION, build_prompt


def _extract_json(raw: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from model output.

    Tries the whole string, then scans each ``{`` and uses ``raw_decode`` so
    surrounding prose or ```json fences (and stray braces in that prose) don't
    break extraction.
    """
    text = raw.strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        # bool is a subclass of int — exclude it so True/False don't become strings.
        return [
            str(v) for v in value if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
    return []


# Claude Code tool names + Codex's `apply_patch` (synthesized by the Codex
# collector from `patch_apply_end`, which is where Codex records real file edits).
_FILE_TOOLS = frozenset(
    {"Edit", "Write", "Read", "MultiEdit", "NotebookEdit", "apply_patch"}
)
_PATHISH = re.compile(r"\.[A-Za-z0-9]{1,8}$")  # ends in a short file extension


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def files_from_turns(turns: list[Turn]) -> list[str]:
    """File paths actually read/written, from the turns' own tool calls.

    Authoritative and complete (covers the whole session, not just the prompt
    window), so files_touched no longer starves on the summary path or drops the
    tail on long sessions. Order-preserving, de-duplicated.
    """
    seen: dict[str, None] = {}
    for turn in turns:
        if turn.tool_name in _FILE_TOOLS and turn.tool_input:
            fp = turn.tool_input.get("file_path") or turn.tool_input.get("notebook_path")
            if isinstance(fp, str) and fp:
                seen.setdefault(fp, None)
    return list(seen)


def _looks_like_path(value: str) -> bool:
    """A heuristic gate for LLM-listed files: a real path/filename, not a dataset
    description like "patents (5.4GB)" or "Mongo: articles_db (articles=127600)"."""
    value = value.strip()
    if not value or len(value) > 200 or any(c in value for c in " \t()=:"):
        return False
    return "/" in value or bool(_PATHISH.search(value))


def _merge_files(turns: list[Turn], llm_files: list[str]) -> list[str]:
    """Deterministic tool-call files first (authoritative); then add only LLM-listed
    paths that look real and aren't already present (catches data files opened via
    Bash/python that no file tool recorded)."""
    deterministic = files_from_turns(turns)
    bases = {_basename(f) for f in deterministic}
    extra = [f for f in llm_files if _looks_like_path(f) and _basename(f) not in bases]
    return deterministic + extra


def _as_outcome(value: Any) -> Outcome:
    if isinstance(value, str):
        try:
            return Outcome(value.lower())
        except ValueError:
            return Outcome.partial
    return Outcome.partial


def _as_friction(value: Any) -> list[FrictionPoint]:
    points: list[FrictionPoint] = []
    if not isinstance(value, list):
        return points
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            category = FrictionCategory(str(item.get("category", "")).lower())
        except ValueError:
            continue
        points.append(
            FrictionPoint(
                category=category,
                description=str(item.get("description", "")),
                turn_refs=_str_list(item.get("turn_refs")),
            )
        )
    return points


def _fallback_intent(turns: list[Turn]) -> str:
    for turn in turns:
        if turn.role.value == "user" and turn.content:
            return turn.content[:200]
    return "unknown"


def _duration_seconds(session: Session) -> float:
    if session.ended_at is not None:
        return max(0.0, (session.ended_at - session.started_at).total_seconds())
    return 0.0


class Compactor:
    """Produces an EngineeringCompaction from a session and its turns."""

    def __init__(self, provider: LLMProvider, prompt_version: str = PROMPT_VERSION) -> None:
        self.provider = provider
        self.prompt_version = prompt_version

    def compact(
        self, session: Session, turns: list[Turn], *, claude_summary: str | None = None
    ) -> EngineeringCompaction:
        raw = self.provider.complete(build_prompt(session, turns, claude_summary=claude_summary))
        data = _extract_json(raw)
        cost = estimate_cost(turns)
        used_summary = bool(claude_summary)
        return EngineeringCompaction(
            id=f"comp-{session.id}",
            session_id=session.id,
            actor=session.actor,
            surface=session.surface,
            project=session.project,
            started_at=session.started_at,
            ended_at=session.ended_at or session.started_at,
            duration_seconds=_duration_seconds(session),
            task_intent=str(data.get("task_intent") or _fallback_intent(turns)),
            approach=str(data.get("approach") or ""),
            artifacts=_str_list(data.get("artifacts")),
            outcome=_as_outcome(data.get("outcome")),
            friction_points=_as_friction(data.get("friction_points")),
            reusable_pattern=bool(data.get("reusable_pattern", False)),
            tier_used=cost.tier,
            est_cost_usd=cost.usd,
            total_tokens=cost.total_tokens,
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            cache_write_tokens=cost.cache_write_tokens,
            cache_read_tokens=cost.cache_read_tokens,
            created_at=datetime.now(UTC),  # when this digest was built (auto-release window)
            prompt_version=(
                f"{self.prompt_version}-summary" if used_summary else self.prompt_version
            ),
            source="claude_summary" if used_summary else "full",
            files_touched=_merge_files(turns, _str_list(data.get("files_touched"))),
            prs_opened=_str_list(data.get("prs_opened")),
            tests_added=_str_list(data.get("tests_added")),
            dead_end_branches=_str_list(data.get("dead_end_branches")),
            languages=_str_list(data.get("languages")),
            frameworks=_str_list(data.get("frameworks")),
        )


__all__ = ["Compactor"]
