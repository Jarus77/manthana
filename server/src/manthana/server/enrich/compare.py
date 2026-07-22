"""DRY-RUN comparison of a candidate enrichment model against production output.

Enrichment is what turns a deterministic digest into the sentences a human
actually reads, so the model behind it IS the product. Production runs
``enrich_model`` (Anthropic Haiku); an OpenRouter model costs roughly a tenth of
that, which is worth having only if the output is still worth reading. A cheap
model that writes vague approaches — or, worse, cannot reliably emit a JSON
object — degrades the whole wiki silently: nothing errors, the pages just get
duller, and nobody finds out until they read one.

So the switch is not a config change to make and watch. This module runs the
candidate over sessions the founder can already read the CURRENT output for, on
the SAME prompt, and puts the two side by side.

Design choices worth defending:

  * BASELINE = the STORED digest, not a fresh call to Haiku. Re-running the
    incumbent would double the spend and still compare against a sample of one;
    the stored text is what the wiki actually shows today, which is the only
    thing the candidate has to beat.
  * Only ALREADY-ENRICHED digests (``source != "pending"``) are eligible —
    a pending digest has no baseline, so comparing against it compares against
    nothing.
  * The prompt is built by ``build_prompt``, identical to the production pass,
    from the same input preference (native summary over raw turns). A tweaked
    prompt would measure the prompt, not the model, and quietly invalidate the
    whole exercise.
  * The candidate's output is run through ``extract_json`` + ``apply_enrichment``
    onto a COPY of the stored digest, so it is coerced, gated and merged exactly
    as production would coerce it. Comparing raw model text would flatter a model
    whose output the coercion layer would have thrown away.

NOTHING IS WRITTEN. No ``upsert``, no ``save_enriched``, no enrichment-state
row, not even on the candidate's success. A comparison tool that mutates the
data it is comparing is worse than no tool at all — the founder would be reading
a wiki the experiment had already edited. (The one write in the whole path is
the caller's ``MeteredProvider`` recording what the calls cost, which is
deliberate: this endpoint spends real money against the org's cap.)

A JSON parse failure is a RESULT, not an error to swallow or abort on: a model
that cannot emit an object is disqualified, and that is precisely the finding
the founder needs. Same for a provider error on one item — it is recorded and
the run continues, because five items with one failure is a far more useful
report than a traceback.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from manthana.schemas import BaseCompaction, FrictionPoint

from ..metering import estimate_cost_usd
from .coerce import extract_json
from .enricher import _load_raw_turns, _session_for, apply_enrichment
from .prompt import build_prompt

if TYPE_CHECKING:
    from manthana.schemas import Turn

    from ..config import ServerConfig
    from ..llm import LLMProvider
    from ..storage import ObjectStore
    from ..store import ServerStore

#: Every qualitative field ``apply_enrichment`` writes. This list is the contract
#: of the comparison: anything the model can move must be shown, or a regression
#: hides in the field nobody printed.
COMPARED_FIELDS: tuple[str, ...] = (
    "task_intent",
    "approach",
    "outcome",
    "artifacts",
    "friction_points",
    "files_touched",
    "prs_opened",
    "tests_added",
    "dead_end_branches",
    "languages",
    "frameworks",
    "reusable_pattern",
)

#: Hard ceiling on items per run. Each one is a paid model call on a real key, so
#: an operator fat-fingering ``--limit 500`` must cost pennies, not the org's
#: whole monthly cap. Twenty sessions is already more output than anyone reads in
#: one sitting.
MAX_ITEMS = 20

#: How far back to scan for enriched digests. Same bounded-scan convention as
#: ``list_pending_for_enrichment``: ``source`` lives inside the payload JSON, not
#: an index column, so the filter runs in Python over a capped window.
_SCAN_CAP = 500


@dataclass
class FieldDiff:
    """One qualitative field, as production has it vs as the candidate wrote it.

    Values are pre-rendered to display strings: the point of this tool is a human
    reading two texts, and normalizing here means the identical/differing verdict
    and what gets printed can never disagree.
    """

    name: str
    baseline: str
    candidate: str

    @property
    def identical(self) -> bool:
        return self.baseline == self.candidate


@dataclass
class SessionComparison:
    """One session's result. Exactly one of: ``skipped``, ``error``,
    ``parse_failure``, or a populated ``fields``."""

    compaction_id: str
    session_id: str = ""
    project: str = ""
    actor: str = ""
    #: Why this session contributed nothing (no baseline, no transcript). Skips
    #: are reported rather than dropped — a run that silently compared 2 of 5
    #: sessions would read as a clean result.
    skipped: str = ""
    #: Provider blew up on this item (network, auth, 5xx after retries).
    error: str = ""
    #: The candidate answered, but no JSON object could be extracted. THE finding.
    parse_failure: bool = False
    fields: list[FieldDiff] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    #: True when the cheap native-summary path was used (as production did).
    used_summary: bool = False

    @property
    def compared(self) -> bool:
        return bool(self.fields)

    @property
    def differing(self) -> list[FieldDiff]:
        return [f for f in self.fields if not f.identical]

    @property
    def identical_names(self) -> list[str]:
        return [f.name for f in self.fields if f.identical]

    def as_dict(self) -> dict[str, Any]:
        return {
            "compaction_id": self.compaction_id,
            "session_id": self.session_id,
            "project": self.project,
            "actor": self.actor,
            "skipped": self.skipped,
            "error": self.error,
            "parse_failure": self.parse_failure,
            "used_summary": self.used_summary,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 8),
            "latency_s": round(self.latency_s, 3),
            "fields": [
                {
                    "name": f.name,
                    "identical": f.identical,
                    "baseline": f.baseline,
                    "candidate": f.candidate,
                }
                for f in self.fields
            ],
        }


def _render(value: Any) -> str:
    """One field → the string a human compares. Lists join with '; ' so an added
    or dropped item shows as a text difference rather than a shape difference."""
    if isinstance(value, bool):  # before int/str: bool renders as yes/no, not "True"
        return "yes" if value else "no"
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, FrictionPoint):
                parts.append(f"{item.category}: {item.description}".strip())
            else:
                parts.append(str(item))
        return "; ".join(parts)
    if value is None:
        return ""
    return str(value).strip()


def _diff_fields(baseline: BaseCompaction, candidate: BaseCompaction) -> list[FieldDiff]:
    """Only fields that EXIST on this digest kind are compared — a
    ``BaseCompaction`` has no ``files_touched``, and inventing an empty one would
    report a phantom match."""
    out: list[FieldDiff] = []
    for name in COMPARED_FIELDS:
        if not hasattr(baseline, name):
            continue
        out.append(
            FieldDiff(
                name=name,
                baseline=_render(getattr(baseline, name)),
                candidate=_render(getattr(candidate, name)),
            )
        )
    return out


def _base_provider(provider: LLMProvider) -> Any:
    """Reach through the metering/resilience wrappers to whatever records
    ``last_usage``/``last_cost_usd``. Duck-typed on purpose: the wrappers are
    private-by-convention, and hard-importing them here would make this module
    care about a wrapping order it has no business knowing."""
    seen = 0
    current: Any = provider
    while seen < 5:  # bounded: a cyclic wrapper must not hang the run
        if hasattr(current, "last_usage"):
            return current
        nxt = getattr(current, "_inner", None) or getattr(current, "inner", None)
        if nxt is None:
            return current
        current, seen = nxt, seen + 1
    return current


def _usage_and_cost(
    provider: LLMProvider, model: str, prompt: str, text: str
) -> tuple[int, int, float]:
    """(input_tokens, output_tokens, usd) for the call just made.

    Prefers what the provider MEASURED — OpenRouter reports the real cost of the
    call, which is the number the founder is trying to decide on. Falls back to
    the price table, and finally to the same chars/4 heuristic metering uses, so
    a mock provider in tests still yields a coherent (zero-ish) figure instead of
    dividing by nothing.
    """
    base = _base_provider(provider)
    usage = getattr(base, "last_usage", None)
    if usage:
        input_tokens, output_tokens = int(usage[0]), int(usage[1])
    else:
        input_tokens, output_tokens = len(prompt) // 4, len(text) // 4
    measured = getattr(base, "last_cost_usd", None)
    if isinstance(measured, int | float):
        return input_tokens, output_tokens, float(measured)
    return input_tokens, output_tokens, estimate_cost_usd(model, input_tokens, output_tokens)


def _select(
    store: ServerStore, org_id: str, *, limit: int, ids: list[str] | None
) -> list[BaseCompaction]:
    """Enriched digests to compare, newest first.

    ``source != "pending"`` is the whole selection rule: a pending digest has no
    stored qualitative text, so there is nothing to hold the candidate against.
    """
    bound = max(1, min(limit, MAX_ITEMS))
    if ids:
        picked: list[BaseCompaction] = []
        for cid in ids[:bound]:
            comp = store.get_compaction(cid, org_id)
            if comp is not None:
                picked.append(comp)
        return picked
    rows = store.query_compactions(org_id=org_id, limit=_SCAN_CAP)  # newest first
    out: list[BaseCompaction] = []
    for comp in rows:
        if comp.source == "pending":
            continue
        out.append(comp)
        if len(out) >= bound:
            break
    return out


def compare_enrichment(
    store: ServerStore,
    object_store: ObjectStore,
    config: ServerConfig,
    org_id: str,
    *,
    provider: LLMProvider,
    candidate_label: str,
    limit: int = 5,
    ids: list[str] | None = None,
) -> list[SessionComparison]:
    """Run ``provider`` over already-enriched sessions and diff it against what is
    stored. Reads only — see the module docstring.

    ``QuotaExceededError`` is deliberately NOT caught: this pass spends real
    money on the org's key, and the budget must stop it exactly as it stops the
    production pass (surfacing as the usual 429).
    """
    results: list[SessionComparison] = []
    for stored in _select(store, org_id, limit=limit, ids=ids):
        item = SessionComparison(
            compaction_id=stored.id,
            session_id=stored.session_id,
            project=stored.project,
            actor=stored.actor,
        )
        if stored.source == "pending":
            # Only reachable via explicit --ids: the newest-first scan filters
            # these out. Say why rather than comparing against empty strings.
            item.skipped = "digest has no stored enrichment to compare against"
            results.append(item)
            continue

        # Same input preference as enrich_org, in the same order — the summary
        # path is a materially different (and much shorter) prompt, so a
        # comparison that took the other branch would not be measuring the model.
        summary = (stored.native_summary or "").strip()
        turns: list[Turn] = []
        if not summary:
            turns = _load_raw_turns(store, object_store, stored.id, org_id)
        if not summary and not turns:
            item.skipped = "no raw transcript in the object store (and no native summary)"
            results.append(item)
            continue

        prompt = build_prompt(
            _session_for(stored, len(turns)), turns, claude_summary=summary or None
        )
        item.used_summary = bool(summary)

        started = time.monotonic()
        try:
            raw = provider.complete(prompt)
        except Exception as exc:  # noqa: BLE001 - one bad item must not void the run
            item.latency_s = time.monotonic() - started
            item.error = f"{type(exc).__name__}: {exc}"
            results.append(item)
            continue
        item.latency_s = time.monotonic() - started
        item.input_tokens, item.output_tokens, item.cost_usd = _usage_and_cost(
            provider, candidate_label, prompt, raw
        )

        data = extract_json(raw)
        if not data:
            # NOT an error. This is the disqualifying result, recorded and shown.
            item.parse_failure = True
            results.append(item)
            continue

        # apply_enrichment returns a NEW object (model_copy(deep=True) inside), so
        # the stored digest we are diffing against is untouched by construction.
        candidate = apply_enrichment(stored, data, used_summary=bool(summary))
        item.fields = _diff_fields(stored, candidate)
        results.append(item)
    return results


def summarize(items: list[SessionComparison]) -> dict[str, Any]:
    """Roll-up the founder actually decides on: did it parse, what did it cost,
    how slow was it. Latency is the MEAN over calls that were actually made —
    skipped items never called the model and would drag it to nonsense."""
    called = [i for i in items if i.latency_s > 0 and not i.skipped]
    compared = [i for i in items if i.compared]
    total_cost = sum(i.cost_usd for i in items)
    total_in = sum(i.input_tokens for i in items)
    total_out = sum(i.output_tokens for i in items)
    return {
        "items": len(items),
        "compared": len(compared),
        "parse_failures": sum(1 for i in items if i.parse_failure),
        "errors": sum(1 for i in items if i.error),
        "skipped": sum(1 for i in items if i.skipped),
        "fields_differing": sum(len(i.differing) for i in compared),
        "fields_identical": sum(len(i.identical_names) for i in compared),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_cost_usd": round(total_cost, 8),
        "mean_latency_s": round(sum(i.latency_s for i in called) / len(called), 3)
        if called
        else 0.0,
        # Blended $/token MEASURED on this run. It is what makes an honest
        # monthly projection possible without a price table that has never heard
        # of the candidate model.
        "cost_per_token": (total_cost / (total_in + total_out))
        if (total_in + total_out) > 0
        else 0.0,
    }


__all__ = [
    "COMPARED_FIELDS",
    "MAX_ITEMS",
    "FieldDiff",
    "SessionComparison",
    "compare_enrichment",
    "summarize",
]
