"""Founder query: structured-filter-first, narrative-second (decisions doc).

Pipeline: NL query -> LLM-parsed structured filter -> SQL over released
compactions (org-scoped) -> k-anonymity floor -> grounded narrative whose every
claim cites compaction ids. Grounding is non-optional: a query that yields too
few contributors (k-anon) or a narrative with no citations returns
"insufficient data" rather than an ungrounded/hallucinated answer.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .config import ServerConfig
from .llm import LLMProvider
from .store import ServerStore

INSUFFICIENT = "insufficient data"


class FounderFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    team_id: str | None = None
    project: str | None = None
    outcome: str | None = None
    actor: str | None = None
    surface: str | None = None
    since: str | None = None  # ISO-8601
    until: str | None = None


@dataclass
class Rollup:
    session_count: int
    distinct_contributors: int
    by_project: dict[str, int]
    by_outcome: dict[str, int]
    total_cost_usd: float


@dataclass
class FounderResult:
    filter: FounderFilter
    rollup: Rollup | None
    narrative: str
    citations: list[str]
    insufficient_data: bool


_PARSE_PROMPT = (
    "Parse this founder question into a JSON filter with keys: team_id, project, "
    "outcome (success|partial|abandoned), actor, surface (claude_code|codex), "
    "since (ISO date), until (ISO date). Use null for anything unspecified. "
    "Return ONLY the JSON object.\nQuestion: {query}"
)

_NARRATIVE_PROMPT = (
    "Write a 2-4 sentence summary for a founder based ONLY on this data. Cite the "
    "specific compaction id in [square brackets] for EVERY claim; do not invent "
    "facts. If the data does not support a claim, omit it.\n"
    "Rollup: {rollup}\nCompactions: {compactions}\n"
)


def _extract_json(raw: str) -> dict[str, Any]:
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


def parse_filter(query: str, provider: LLMProvider) -> FounderFilter:
    data = _extract_json(provider.complete(_PARSE_PROMPT.format(query=query)))
    try:
        return FounderFilter.model_validate(data)
    except ValidationError:
        return FounderFilter()


def _rollup(compactions: list[Any]) -> Rollup:
    by_project: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    actors: set[str] = set()
    total = 0.0
    for c in compactions:
        by_project[c.project] = by_project.get(c.project, 0) + 1
        by_outcome[str(c.outcome)] = by_outcome.get(str(c.outcome), 0) + 1
        actors.add(c.actor)
        total += c.est_cost_usd or 0.0
    return Rollup(
        session_count=len(compactions),
        distinct_contributors=len(actors),
        by_project=by_project,
        by_outcome=by_outcome,
        total_cost_usd=round(total, 6),
    )


def run_query(
    store: ServerStore,
    config: ServerConfig,
    *,
    org_id: str,
    query: str,
    provider: LLMProvider,
) -> FounderResult:
    spec = parse_filter(query, provider)
    compactions = store.query_compactions(
        org_id=org_id,
        team_id=spec.team_id,
        project=spec.project,
        outcome=spec.outcome,
        actor=spec.actor,
        surface=spec.surface,
        since=spec.since,
        until=spec.until,
    )
    rollup = _rollup(compactions)

    # k-anonymity floor: no team-level aggregate below the contributor floor.
    if rollup.distinct_contributors < config.k_anon_floor:
        return FounderResult(
            filter=spec, rollup=None, narrative=INSUFFICIENT, citations=[], insufficient_data=True
        )

    brief = [
        {"id": c.id, "project": c.project, "intent": c.task_intent, "outcome": str(c.outcome)}
        for c in compactions
    ]
    narrative = provider.complete(
        _NARRATIVE_PROMPT.format(
            rollup=json.dumps(rollup.__dict__), compactions=json.dumps(brief)
        )
    ).strip()

    citations = [c.id for c in compactions if f"[{c.id}]" in narrative]
    # Non-optional grounding: a narrative that cites nothing is treated as
    # ungrounded — return the factual rollup but withhold the narrative.
    if not citations:
        return FounderResult(
            filter=spec,
            rollup=rollup,
            narrative=INSUFFICIENT,
            citations=[],
            insufficient_data=True,
        )

    return FounderResult(
        filter=spec,
        rollup=rollup,
        narrative=narrative,
        citations=citations,
        insufficient_data=False,
    )


__all__ = ["FounderFilter", "Rollup", "FounderResult", "parse_filter", "run_query", "INSUFFICIENT"]
