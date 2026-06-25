"""Router analyzer — counterfactual cost of released sessions on cheaper tiers.

A measurable, showable artifact: re-price each released compaction's per-kind tokens at
each model tier and estimate the savings from routing the *low-risk* sessions one tier
down. Pure arithmetic over stored token counts — no model calls, no live replay.

Re-pricing is exact (per-kind tokens × tier rate), which matters because cache-read
tokens are ~1/10th the input rate and dominate long sessions. Sessions compacted before
the per-kind token fields existed (no breakdown) are SKIPPED and counted, never silently
dropped.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..store import ServerStore

# Immutable rate table, duplicated here to keep the AGPL server from importing the
# Apache agent package. Canonical copy: agent/src/manthana/agent/cost/rates.py.
_RATE_TABLE: dict[str, dict[str, float]] = {
    "haiku": {"in": 0.80, "out": 4.0, "cacheWrite": 1.00, "cacheRead": 0.08},
    "sonnet": {"in": 3.00, "out": 15.0, "cacheWrite": 3.75, "cacheRead": 0.30},
    "opus": {"in": 15.00, "out": 75.0, "cacheWrite": 18.75, "cacheRead": 1.50},
}
_CHEAPER = {"opus": "sonnet", "sonnet": "haiku"}  # one tier down (haiku is the floor)
_HARD_FRICTION = {"loop", "deadend"}  # signals the task genuinely needed a strong model


@dataclass
class SessionCost:
    id: str
    project: str
    tier: str
    current_usd: float
    safe_to_downgrade: bool
    target_tier: str | None
    projected_usd: float
    savings_usd: float


@dataclass
class RouterReport:
    org_id: str
    sessions: int  # released compactions seen
    priced: int  # how many had a per-kind token breakdown to re-price
    skipped_no_tokens: int  # pre-breakdown digests (logged, not dropped)
    current_usd: float  # sum of current (list-equivalent) cost over priced sessions
    projected_usd: float  # sum after routing the safe ones one tier down
    savings_usd: float
    savings_pct: float
    by_target: dict[str, int] = field(default_factory=dict)  # downgrades per target tier
    rows: list[SessionCost] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "sessions": self.sessions,
            "priced": self.priced,
            "skipped_no_tokens": self.skipped_no_tokens,
            "current_usd": round(self.current_usd, 4),
            "projected_usd": round(self.projected_usd, 4),
            "savings_usd": round(self.savings_usd, 4),
            "savings_pct": round(self.savings_pct, 2),
            "by_target": self.by_target,
            "rows": [vars(r) for r in self.rows],
        }


def _price(c: Any, tier: str) -> float:
    r = _RATE_TABLE[tier]
    return (
        (c.input_tokens or 0) / 1e6 * r["in"]
        + (c.output_tokens or 0) / 1e6 * r["out"]
        + (c.cache_write_tokens or 0) / 1e6 * r["cacheWrite"]
        + (c.cache_read_tokens or 0) / 1e6 * r["cacheRead"]
    )


def _has_breakdown(c: Any) -> bool:
    return any(
        getattr(c, f, None) is not None
        for f in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
    )


def _safe_to_downgrade(c: Any) -> bool:
    """A session is a downgrade candidate if it wasn't a struggle: not abandoned, no
    loop/deadend friction, and few friction points overall — i.e. a cheaper model likely
    would have sufficed. Conservative on purpose (false 'safe' overstates savings)."""
    if str(getattr(c, "outcome", "")) == "abandoned":
        return False
    fps = getattr(c, "friction_points", None) or []
    if any(str(fp.category) in _HARD_FRICTION for fp in fps):
        return False
    return len(fps) <= 2


def analyze_counterfactual_costs(
    store: ServerStore, org_id: str, *, limit: int = 100_000
) -> RouterReport:
    """Re-price each released compaction at its current tier and at one tier cheaper for
    the low-risk ones; aggregate the savings."""
    comps = store.query_compactions(org_id=org_id, limit=limit)
    rows: list[SessionCost] = []
    by_target: dict[str, int] = {}
    current_total = projected_total = 0.0
    priced = skipped = 0
    for c in comps:
        tier = c.tier_used
        if tier not in _RATE_TABLE or not _has_breakdown(c):
            skipped += 1  # unknown tier or pre-breakdown digest — can't re-price reliably
            continue
        priced += 1
        current = _price(c, tier)
        safe = _safe_to_downgrade(c)
        target = _CHEAPER.get(tier) if safe else None
        projected = _price(c, target) if target else current
        savings = current - projected
        if target:
            by_target[target] = by_target.get(target, 0) + 1
        current_total += current
        projected_total += projected
        rows.append(
            SessionCost(
                id=c.id, project=c.project, tier=tier, current_usd=round(current, 4),
                safe_to_downgrade=safe, target_tier=target,
                projected_usd=round(projected, 4), savings_usd=round(savings, 4),
            )
        )
    savings = current_total - projected_total
    rows.sort(key=lambda r: r.savings_usd, reverse=True)  # biggest savings first
    return RouterReport(
        org_id=org_id, sessions=len(comps), priced=priced, skipped_no_tokens=skipped,
        current_usd=current_total, projected_usd=projected_total, savings_usd=savings,
        savings_pct=(100.0 * savings / current_total) if current_total else 0.0,
        by_target=by_target, rows=rows,
    )


__all__ = ["RouterReport", "SessionCost", "analyze_counterfactual_costs"]
