"""Server-side digest enrichment.

Agents emit DETERMINISTIC digests (``source="pending"``): ids, actor, project,
timestamps, duration, token counts, tier, cost, and ``files_touched`` extracted
from real tool calls. The qualitative fields (approach, artifacts, outcome,
friction, languages/frameworks) arrive empty. This module fills them in on the
operator's metered Anthropic key — the agent never calls a model, because doing
so created a Claude Code transcript that was itself captured and compacted.

Input preference, cheapest first:
  1. ``native_summary`` — the coding agent's OWN compaction summary (Claude
     Code's ``isCompactSummary`` turn / Codex's ``compacted`` payload). ~14% of
     sessions have one and it is a fraction of the transcript's size.
  2. the raw transcript, rehydrated from the object store (JSONL, one serialized
     ``Turn`` per line, validated on ingest) back into real ``Turn`` objects —
     so the existing turn-based prompt is reused unchanged.
  3. NEITHER yet → WAIT. Metadata and raw are separate requests, so raw can lag
     or fail permanently; burning a model call on an empty transcript would buy
     nothing. Bounded attempts + an age-out stop it retrying forever, and the
     ``enrichment_state`` row makes that wait observable.

Invariants:
  * Deterministic fields are NEVER overwritten — only the qualitative ones are
    merged. ``files_touched`` in particular keeps the agent's tool-call-derived
    list verbatim; model-suggested paths may only be APPENDED after passing a
    path-sanity check.
  * Quota is enforced through ``MeteredProvider``, so enrichment counts against
    the org's monthly cap and shows up in ``/v1/admin/usage``. A
    ``QuotaExceededError`` leaves the digest ``pending`` and ends the org's
    batch cleanly — never a crash, never a partial write.
  * A write happens only after the model output parses; a failed call leaves the
    digest exactly as it was.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypeVar

from manthana.schemas import (
    BaseCompaction,
    EngineeringCompaction,
    Session,
    Turn,
)

from ..metering import MeteredProvider, QuotaExceededError
from .coerce import as_friction, as_outcome, extract_json, merge_files, str_list
from .prompt import PROMPT_VERSION, build_prompt

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import ServerConfig
    from ..llm import LLMProvider
    from ..storage import ObjectStore
    from ..store import ServerStore

_log = logging.getLogger(__name__)

# Enrichment preserves the digest's concrete subclass (it merges fields onto a
# copy), so callers keep their EngineeringCompaction rather than widening to the
# base — that's what makes ``files_touched`` still reachable on the result.
_C = TypeVar("_C", bound=BaseCompaction)


@dataclass
class EnrichStats:
    """Outcome of one pass, per the states an operator cares about."""

    enriched: int = 0
    waiting: int = 0  # no input yet (raw hasn't arrived) — will retry
    abandoned: int = 0  # attempts/age exhausted — will NOT retry
    failed: int = 0  # call or parse failed — will retry
    quota_blocked: int = 0  # org hit its monthly cap; digests left pending
    orgs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "enriched": self.enriched,
            "waiting": self.waiting,
            "abandoned": self.abandoned,
            "failed": self.failed,
            "quota_blocked": self.quota_blocked,
            "orgs": self.orgs,
        }


def rehydrate_turns(blob: bytes) -> list[Turn]:
    """Raw JSONL (one serialized ``Turn`` per line) → real ``Turn`` objects.

    Tolerant per line, exactly like the founder drill path: a malformed or
    non-conforming line is skipped rather than voiding the whole transcript.
    """
    turns: list[Turn] = []
    for line in blob.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            turns.append(Turn.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001 - malformed line: skip, keep the rest
            continue
    return turns


def _session_for(compaction: BaseCompaction, turn_count: int) -> Session:
    """The prompt header wants a ``Session``; rebuild the few fields it reads
    from the digest's own deterministic metadata."""
    return Session(
        id=compaction.session_id,
        actor=compaction.actor,
        surface=compaction.surface,
        project=compaction.project,
        started_at=compaction.started_at,
        ended_at=compaction.ended_at,
        turn_count=turn_count,
    )


def _load_raw_turns(
    store: ServerStore, object_store: ObjectStore, compaction_id: str, org_id: str
) -> list[Turn]:
    key = store.get_raw_key(compaction_id, org_id)
    if not key:
        return []
    blob = object_store.get(key)
    if not blob:
        return []
    return rehydrate_turns(blob)


def apply_enrichment(
    compaction: _C, data: dict[str, object], *, used_summary: bool
) -> _C:
    """Merge parsed model output onto a digest — qualitative fields ONLY.

    Everything deterministic (ids, actor, project, timestamps, duration, token
    counts, tier, costs, release state) is carried through untouched. The result
    is a new object; the input is not mutated.
    """
    merged = compaction.model_copy(deep=True)

    # Qualitative fields common to every compaction kind.
    intent = str(data.get("task_intent") or "").strip()
    if intent:
        merged.task_intent = intent
    merged.approach = str(data.get("approach") or "")
    merged.artifacts = str_list(data.get("artifacts"))
    merged.outcome = as_outcome(data.get("outcome"))
    merged.friction_points = as_friction(data.get("friction_points"))
    merged.reusable_pattern = bool(data.get("reusable_pattern", False))

    if isinstance(merged, EngineeringCompaction):
        # files_touched: the agent's tool-call-derived list is AUTHORITATIVE.
        # merge_files keeps it verbatim and only appends model-suggested paths
        # that pass the path-sanity gate and aren't already present.
        merged.files_touched = merge_files(
            merged.files_touched, str_list(data.get("files_touched"))
        )
        merged.prs_opened = str_list(data.get("prs_opened"))
        merged.tests_added = str_list(data.get("tests_added"))
        merged.dead_end_branches = str_list(data.get("dead_end_branches"))
        merged.languages = str_list(data.get("languages"))
        merged.frameworks = str_list(data.get("frameworks"))

    merged.source = "claude_summary" if used_summary else "full"
    merged.prompt_version = f"{PROMPT_VERSION}-summary" if used_summary else PROMPT_VERSION
    return merged


def enrich_org(
    store: ServerStore,
    object_store: ObjectStore,
    provider: LLMProvider,
    config: ServerConfig,
    *,
    org_id: str,
    limit: int,
    now: datetime | None = None,
) -> EnrichStats:
    """Enrich up to ``limit`` pending digests for one org. Never raises."""
    now = now or datetime.now(UTC)
    stats = EnrichStats(orgs=[org_id])
    max_age = timedelta(days=max(1, config.enrich_max_age_days))

    pending = store.list_pending_for_enrichment(
        org_id, limit=limit, skip_ids=store.abandoned_enrichment_ids(org_id)
    )
    for compaction in pending:
        state = store.get_enrichment_state(org_id, compaction.id)

        # Age-out BEFORE spending anything: a digest whose raw never arrived must
        # not retry forever. Either bound (attempts or wall age) retires it.
        if state is not None:
            # Attempts now count only real failed model calls — waiting for raw
            # does NOT increment them. Before that distinction, five 5-minute
            # passes (~25 min) with a late raw upload permanently abandoned the
            # digest, which is how the wiki filled with "awaiting summary"
            # sessions that could never recover. Waiting digests retire on wall
            # age alone; a raw upload arriving deletes this row entirely
            # (store.record_raw), restarting both clocks.
            too_many = state.attempts >= max(1, config.enrich_max_attempts)
            first_seen = datetime.fromisoformat(state.first_seen_at)
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=UTC)
            too_old = (now - first_seen) > max_age
            if too_many or too_old:
                reason = (
                    f"gave up after {state.attempts} failed attempts"
                    if too_many
                    else f"no input within {config.enrich_max_age_days}d"
                )
                store.mark_enrichment_abandoned(org_id, compaction.id, detail=reason)
                stats.abandoned += 1
                continue

        summary = (compaction.native_summary or "").strip()
        turns: list[Turn] = []
        if not summary:
            # Only reach for raw when there's no native summary — that's the whole
            # point of carrying it: it's a fraction of the transcript's tokens.
            turns = _load_raw_turns(store, object_store, compaction.id, org_id)

        if not summary and not turns:
            # Neither input has arrived. WAIT — do not burn a call on nothing.
            store.record_enrichment_attempt(
                org_id, compaction.id, state="waiting",
                detail="no native_summary and no raw yet", count_attempt=False,
            )
            stats.waiting += 1
            continue

        prompt = build_prompt(
            _session_for(compaction, len(turns)), turns, claude_summary=summary or None
        )
        try:
            raw = provider.complete(prompt)
        except QuotaExceededError:
            # The org's monthly budget is spent. Leave every remaining digest
            # pending and stop THIS org — the next pass picks up where we left
            # off. Not an attempt against the digest: nothing was tried on it.
            _log.info("enrichment: org %s is over its monthly LLM cap; deferring", org_id)
            stats.quota_blocked += 1
            return stats
        except Exception as exc:  # noqa: BLE001 - provider failure must not kill the batch
            store.record_enrichment_attempt(
                org_id, compaction.id, state="failed", detail=f"{type(exc).__name__}: {exc}"
            )
            stats.failed += 1
            continue

        data = extract_json(raw)
        if not data:
            store.record_enrichment_attempt(
                org_id, compaction.id, state="failed", detail="model returned no JSON object"
            )
            stats.failed += 1
            continue

        enriched = apply_enrichment(compaction, data, used_summary=bool(summary))
        if store.save_enriched(enriched, org_id=org_id):
            store.clear_enrichment_state(org_id, compaction.id)
            stats.enriched += 1
        else:  # row disappeared mid-pass (purged) — nothing to write
            stats.failed += 1

    return stats


def run_enrichment_pass(
    store: ServerStore,
    object_store: ObjectStore,
    config: ServerConfig,
    provider_for: Callable[[str], LLMProvider],
    *,
    now: datetime | None = None,
) -> EnrichStats:
    """One batched pass across every org with pending digests.

    Bounded twice over: ``enrich_batch_per_org`` caps what a single org gets in
    one pass (so a big backlog cannot starve the others), and
    ``enrich_max_batch`` caps the pass as a whole.
    """
    total = EnrichStats()
    budget = max(1, config.enrich_max_batch)
    per_org = max(1, config.enrich_batch_per_org)

    for org_id in store.orgs_with_pending():
        if budget <= 0:
            break
        stats = enrich_org(
            store,
            object_store,
            provider_for(org_id),
            config,
            org_id=org_id,
            limit=min(per_org, budget),
            now=now,
        )
        total.enriched += stats.enriched
        total.waiting += stats.waiting
        total.abandoned += stats.abandoned
        total.failed += stats.failed
        total.quota_blocked += stats.quota_blocked
        total.orgs.append(org_id)
        # Only real work consumes the pass budget; waiting/abandoned digests cost
        # no tokens, so they must not crowd out another org's enrichable ones.
        budget -= stats.enriched + stats.failed
    return total


def enrich_provider_for(
    store: ServerStore, config: ServerConfig, inner: LLMProvider
) -> Callable[[str], LLMProvider]:
    """Per-org metered view of the enrichment provider (same wrapper the founder
    pipeline uses, so enrichment shares the org's monthly cap and usage view)."""

    def _for(org_id: str) -> LLMProvider:
        cap = store.get_org_quota(org_id)
        if cap is None:
            cap = config.llm_monthly_cap_usd
        return MeteredProvider(inner, store, org_id, cap, purpose="enrich")

    return _for


__all__ = [
    "EnrichStats",
    "apply_enrichment",
    "enrich_org",
    "enrich_provider_for",
    "rehydrate_turns",
    "run_enrichment_pass",
]
