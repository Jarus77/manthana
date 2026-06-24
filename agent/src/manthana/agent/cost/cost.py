"""Per-session cost estimation from normalized Turns.

Re-expressed from affaan-m/ECC ``scripts/hooks/cost-tracker.js``
``sumUsageFromTranscript`` (MIT, 2026 Affaan Mustafa): sum input/output/cache
tokens across assistant turns, take the last seen model, and price via
``RATE_TABLE``. ECC summed by re-reading the JSONL transcript; here the tokens
already live on the parsed ``Turn``s, so we sum those.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from manthana.schemas import Turn

from .rates import get_rates, resolve_tier


@dataclass(frozen=True)
class CostBreakdown:
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    total_tokens: int
    model: str | None
    tier: str | None
    usd: float


def estimate_cost(turns: Iterable[Turn]) -> CostBreakdown:
    """Sum token usage across turns and price it at API list rates (USD).

    Pricing is PER-TURN at each turn's own model rate, so a session that switches
    models (e.g. sonnet planning + opus execution) is priced correctly rather than
    pricing every token at the last-seen model.

    IMPORTANT: ``usd`` is an *API list-price equivalent*, NOT what a Claude
    *subscription* user actually pays. It is dominated by cache-read tokens (the
    same context re-read every turn) and can run into the hundreds of dollars for a
    long session. Treat it as a comparative upper bound; prefer ``total_tokens`` for
    real magnitude.
    """
    input_tokens = output_tokens = cache_write = cache_read = 0
    usd = 0.0
    model_tokens: dict[str, int] = {}
    for turn in turns:
        ti = turn.tokens_in or 0
        to = turn.tokens_out or 0
        cw = turn.cache_creation_tokens or 0
        cr = turn.cache_read_tokens or 0
        input_tokens += ti
        output_tokens += to
        cache_write += cw
        cache_read += cr
        rates = get_rates(turn.model)  # price each turn at its own model's rate
        usd += (
            (ti / 1e6) * rates["in"]
            + (to / 1e6) * rates["out"]
            + (cw / 1e6) * rates["cacheWrite"]
            + (cr / 1e6) * rates["cacheRead"]
        )
        if turn.model:
            model_tokens[turn.model] = model_tokens.get(turn.model, 0) + ti + to + cw + cr
    # Primary model = the one carrying the most tokens (the representative tier for a
    # mixed-model session); None if no turn reported a model.
    primary = max(model_tokens, key=model_tokens.__getitem__) if model_tokens else None
    return CostBreakdown(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write,
        cache_read_tokens=cache_read,
        total_tokens=input_tokens + output_tokens + cache_write + cache_read,
        model=primary,
        tier=resolve_tier(primary),
        usd=round(usd, 6),
    )


__all__ = ["CostBreakdown", "estimate_cost"]
