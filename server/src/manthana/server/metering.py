"""Per-org LLM usage metering + monthly quota caps (hosted multi-tenant).

In the hosted deployment a single server-wide Anthropic key pays for every
tenant's founder narratives/digests, so each org gets a monthly USD cap: the
``MeteredProvider`` checks month-to-date spend BEFORE each call and records
usage after, raising ``QuotaExceededError`` (surfaced as HTTP 429) once the cap
is reached. Self-hosted deploys are unaffected — a cap of 0 disables metering
enforcement (usage is still recorded for visibility).

Costs are estimated from token counts at API list price (same convention as the
router analyzer): real counts when the provider exposes them, else a chars/4
heuristic for the mock/scripted providers.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .llm import LLMProvider, ResilientProvider

if TYPE_CHECKING:
    from .store import ServerStore


class QuotaExceededError(Exception):
    """The org's monthly LLM budget is exhausted (surfaced as HTTP 429)."""

    def __init__(self, org_id: str, cap_usd: float, spent_usd: float) -> None:
        self.org_id = org_id
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        super().__init__(
            f"org {org_id!r} has used ${spent_usd:.2f} of its ${cap_usd:.2f} "
            "monthly AI budget — quota resets at the start of next month"
        )


# API list price per MILLION tokens (input, output), matched by substring of the
# model id. Kept deliberately small — this is a budget estimate, not billing.
_PRICE_PER_MTOK: list[tuple[str, float, float]] = [
    ("haiku", 1.0, 5.0),
    ("opus", 15.0, 75.0),
    ("sonnet", 3.0, 15.0),
]
_DEFAULT_PRICE = (3.0, 15.0)  # unknown model → sonnet-class rates


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = next(
        ((i, o) for needle, i, o in _PRICE_PER_MTOK if needle in model), _DEFAULT_PRICE
    )
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def month_key(now: datetime | None = None) -> str:
    """The usage bucket key, e.g. '2026-07' (UTC)."""
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _unwrap(provider: LLMProvider) -> LLMProvider:
    """Reach through ResilientProvider to the provider that records ``last_usage``."""
    return provider.inner if isinstance(provider, ResilientProvider) else provider


class MeteredProvider:
    """Wraps the shared provider with one org's budget. Cheap to build per-request.

    A cap of 0 (or negative) means unlimited — usage is still recorded so the
    admin usage endpoint stays informative either way.
    """

    name = "metered"

    def __init__(
        self,
        inner: LLMProvider,
        store: ServerStore,
        org_id: str,
        cap_usd: float,
        *,
        purpose: str = "",
    ) -> None:
        self._inner = inner
        self._store = store
        self._org_id = org_id
        self._cap_usd = cap_usd
        #: Which pass this provider serves (enrich/consolidate/overview/founder/
        #: ask). Attribution only — the CAP stays whole-org, so no purpose mix
        #: can sneak past the budget.
        self._purpose = purpose

    def complete(self, prompt: str) -> str:
        month = month_key()
        if self._cap_usd > 0:
            spent = self._store.get_llm_usage(self._org_id, month).est_cost_usd
            if spent >= self._cap_usd:
                raise QuotaExceededError(self._org_id, self._cap_usd, spent)
        text = self._inner.complete(prompt)
        base = _unwrap(self._inner)
        usage = getattr(base, "last_usage", None)
        if usage:
            input_tokens, output_tokens = usage
        else:  # mock/scripted providers → chars/4 heuristic
            input_tokens, output_tokens = len(prompt) // 4, len(text) // 4
        model = getattr(base, "model", "") or ""
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        self._store.add_llm_usage(
            self._org_id,
            month,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            est_cost_usd=cost,
        )
        if self._purpose:
            try:
                self._store.add_llm_usage_purpose(
                    self._org_id, month, self._purpose,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    est_cost_usd=cost,
                )
            except Exception:  # noqa: BLE001 - attribution must never fail the call
                pass
        return text


__all__ = ["QuotaExceededError", "MeteredProvider", "estimate_cost_usd", "month_key"]
