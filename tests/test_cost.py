"""Cost estimation tests (verbatim ECC RATE_TABLE).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.agent.cost import estimate_cost, get_rates, tier_of
from manthana.schemas import Role, Turn


def _assistant(
    model: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> Turn:
    return Turn(
        id="t",
        session_id="s",
        actor="e",
        seq=0,
        role=Role.assistant,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
    )


def test_tier_and_rate_resolution() -> None:
    assert tier_of("claude-opus-4-8") == "opus"
    assert tier_of("claude-3-5-haiku") == "haiku"
    assert tier_of("claude-sonnet-4-6") == "sonnet"
    assert tier_of("mystery") is None
    assert get_rates("claude-opus-4-8")["in"] == 15.00
    assert get_rates("unknown-model") == get_rates("sonnet")  # defaults to sonnet


def test_estimate_cost_matches_rate_table() -> None:
    turns = [
        _assistant(
            "claude-opus-4-8",
            tokens_in=1_000_000,
            tokens_out=1_000_000,
            cache_creation_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )
    ]
    cost = estimate_cost(turns)
    # opus: 15 + 75 + 18.75 + 1.5
    assert cost.usd == 110.25
    assert cost.tier == "opus"
    assert cost.input_tokens == 1_000_000


def test_estimate_cost_empty_is_zero() -> None:
    cost = estimate_cost([])
    assert cost.usd == 0.0
    assert cost.model is None
    assert cost.total_tokens == 0


def test_total_tokens_sums_all_token_kinds() -> None:
    cost = estimate_cost(
        [
            _assistant(
                "claude-opus-4-8",
                tokens_in=10,
                tokens_out=20,
                cache_creation_tokens=30,
                cache_read_tokens=40,
            )
        ]
    )
    assert cost.total_tokens == 100  # 10 + 20 + 30 + 40


def test_mixed_model_session_priced_per_turn_not_last_seen() -> None:
    # A sonnet-heavy session that ends on one opus turn must NOT price all tokens
    # at opus (the old last-seen bug). Each turn prices at its own model.
    turns = [
        _assistant("claude-sonnet-4-6", tokens_out=1_000_000),  # sonnet out: 1M * 15 = 15.0
        _assistant("claude-opus-4-8", tokens_out=2_000_000),  # opus out: 2M * 75 = 150.0
    ]
    cost = estimate_cost(turns)
    # per-turn: 15 + 150 = 165. last-seen-opus bug would be (3M*75)=225; all-sonnet=45.
    assert cost.usd == 165.0
    assert cost.tier == "opus"  # primary = model carrying the most tokens (opus, 2M)
