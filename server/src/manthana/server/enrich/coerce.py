"""Defensive coercion of model output into schema-valid compaction fields.

Recovered verbatim (behavior-for-behavior) from the agent-side compactor that
used to make the LLM call, so the enrichment pass degrades exactly the way the
old pipeline did rather than crashing on malformed output:

  * ``_extract_json`` scans JSON out of surrounding prose / ``` fences.
  * ``_str_list`` excludes bools (``bool`` is a subclass of ``int``) so
    ``True``/``False`` never become the strings "True"/"False".
  * ``as_outcome`` maps an unknown outcome to ``partial`` (the neutral value)
    instead of raising.
  * ``as_friction`` DROPS a friction point whose category is not in the enum,
    keeping the rest — one bad item never voids the list.
  * ``looks_like_path`` gates model-suggested file paths so a dataset
    description ("patents (5.4GB)") never lands in ``files_touched``.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import re
from typing import Any

from manthana.schemas import FrictionCategory, FrictionPoint, Outcome

_PATHISH = re.compile(r"\.[A-Za-z0-9]{1,8}$")  # ends in a short file extension


def extract_json(raw: str) -> dict[str, Any]:
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


def str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        # bool is a subclass of int — exclude it so True/False don't become strings.
        return [
            str(v) for v in value if isinstance(v, str | int | float) and not isinstance(v, bool)
        ]
    return []


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def looks_like_path(value: str) -> bool:
    """A heuristic gate for LLM-listed files: a real path/filename, not a dataset
    description like "patents (5.4GB)" or "Mongo: articles_db (articles=127600)"."""
    value = value.strip()
    if not value or len(value) > 200 or any(c in value for c in " \t()=:"):
        return False
    return "/" in value or bool(_PATHISH.search(value))


def merge_files(deterministic: list[str], llm_files: list[str]) -> list[str]:
    """Tool-call-derived files first (AUTHORITATIVE — the agent extracted these from
    real tool calls, never from model output); then append only model-listed paths
    that look real and aren't already present under the same basename (catches data
    files opened via Bash/python that no file tool recorded).

    The deterministic list is never reordered, filtered, or replaced.
    """
    bases = {basename(f) for f in deterministic}
    extra: list[str] = []
    for f in llm_files:
        if not looks_like_path(f):
            continue
        base = basename(f)
        if base in bases:
            continue
        bases.add(base)  # de-dupe within the model's own list too
        extra.append(f)
    return [*deterministic, *extra]


def as_outcome(value: Any) -> Outcome:
    if isinstance(value, str):
        try:
            return Outcome(value.lower())
        except ValueError:
            return Outcome.partial
    return Outcome.partial


def as_friction(value: Any) -> list[FrictionPoint]:
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
                turn_refs=str_list(item.get("turn_refs")),
            )
        )
    return points


__all__ = [
    "extract_json",
    "str_list",
    "basename",
    "looks_like_path",
    "merge_files",
    "as_outcome",
    "as_friction",
]
