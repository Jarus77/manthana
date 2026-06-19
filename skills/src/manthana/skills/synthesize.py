"""Synthesize one cluster of compactions into a single generalized SkillDraft.

No authoritative source survived the research for cluster→skill prompts, so this
uses sound patterns: give the model ALL cluster members, instruct it to extract
the common invariant and parameterize what varies (avoid single-example
overfit), and to write a third-person, what+when description. Output is validated
and repaired; if the LLM is absent or its output can't be salvaged, a
deterministic template fallback produces a valid (if mechanical) skill so mining
works offline and in tests.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from typing import Any

from .cluster import CompactionCluster
from .provider import LLMProvider
from .skillmd import SkillDraft, repair_draft, slugify_name, validate_draft

PROMPT_VERSION = "v1"

_SYNTH_PROMPT = """You are Manthana's skill miner. Below are {n} engineering session
digests, from {c} distinct contributor(s), that all solve the SAME class of problem.
Distill them into ONE reusable Agent Skill that GENERALIZES across ALL the examples —
do not overfit to any single example; extract the common invariant and parameterize
what varies. Concretely:
  - replace specific dataset/file names (e.g. a particular CSV, "CSN reports") with
    {{placeholders}} like {{dataset}} / {{input_file}};
  - do NOT hard-code a rigid output (e.g. "answer.txt") — say "write results to the
    requested output";
  - extract the invariant PROCEDURE (e.g. discover source -> filter -> aggregate ->
    report) independent of the specific domain, dataset, or tool.
Return ONLY a JSON object with keys:
  "name": a lowercase-hyphen slug, <=64 chars, not containing 'anthropic' or 'claude';
    action-oriented (verb-noun, e.g. "analyze-tabular-reports"), NOT a dataset name
  "description": third person, <=1024 chars, stating WHAT the skill does AND WHEN to use
    it. Name the trigger PATTERN abstractly (e.g. "when asked to count/aggregate records
    in a CSV by category"), NOT the specific files or one-off task wording.
  "body": concise markdown for the reusable procedure (<500 lines): the generalized
    steps with {{placeholders}} for what varies, concrete heuristics (how to find the
    right source, match columns), and what to do on failure (missing file/columns).
    Do NOT paste the literal example tasks.
Digests (JSON):
{examples}
Output JSON only."""


def _examples_json(cluster: CompactionCluster) -> str:
    return json.dumps(
        [
            {
                "task_intent": c.task_intent,
                "approach": c.approach,
                "artifacts": c.artifacts,
                "outcome": str(c.outcome),
            }
            for c in cluster.compactions
        ],
        ensure_ascii=False,
    )


def _s(value: Any) -> str:
    """Coerce a JSON field to str only if it really is one (null/number/dict -> '')."""
    return value if isinstance(value, str) else ""


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Collect ALL top-level dicts; prefer the one that looks like the answer
    # (has 'description'), else the last (a trailing real answer beats a prose
    # example that appears first).
    decoder = json.JSONDecoder()
    dicts: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(value, dict):
            dicts.append(value)
        index += end
    for candidate in reversed(dicts):
        if "description" in candidate:
            return candidate
    return dicts[-1] if dicts else {}


def fallback_draft(cluster: CompactionCluster) -> SkillDraft:
    """Deterministic, always-valid skill from a cluster (no LLM needed)."""
    intents = [c.task_intent for c in cluster.compactions]
    unique = list(dict.fromkeys(intents))
    primary = unique[0] if unique else "recurring task"
    name = slugify_name(primary)
    triggers = "; ".join(unique[:3])
    description = (
        f"Reusable procedure for {primary}. Use it when tasked to: {triggers} — "
        f"or similar work of the same kind. Distilled from {cluster.size} sessions "
        f"across {len(cluster.contributors)} contributor(s)."
    )[:1024]
    lines = ["## Pattern", "", f"Recurs across {cluster.size} sessions:", ""]
    lines += [f"- intent: {c.task_intent} — approach: {c.approach}" for c in cluster.compactions]
    return repair_draft(SkillDraft(name=name, description=description, body="\n".join(lines)))


def synthesize(
    cluster: CompactionCluster,
    provider: LLMProvider | None,
    *,
    prompt_version: str = PROMPT_VERSION,
) -> SkillDraft:
    if provider is None:
        return fallback_draft(cluster)
    prompt = _SYNTH_PROMPT.format(
        n=cluster.size, c=len(cluster.contributors), examples=_examples_json(cluster)
    )
    try:
        raw = provider.complete(prompt)
    except Exception:  # noqa: BLE001 - LLM failure must not break mining
        return fallback_draft(cluster)
    data = _extract_json(raw)
    if not data:
        return fallback_draft(cluster)
    draft = repair_draft(
        SkillDraft(
            name=_s(data.get("name")),
            description=_s(data.get("description")),
            body=_s(data.get("body")),
        )
    )
    if validate_draft(draft):  # unsalvageable (e.g. empty description/body)
        return fallback_draft(cluster)
    return draft


__all__ = ["synthesize", "fallback_draft", "PROMPT_VERSION"]
