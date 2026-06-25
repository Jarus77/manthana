"""Semantic ranking + coverage for the query engine (shared by agent + server).

Generic and store-agnostic: given a query, candidate items (anything with an ``id``),
and a vectors-by-id map, rank by cosine and report **coverage** so the caller can
honour Manthana's "complete or said-so" rule (never silently truncate). Vector
storage/caching is each store's job; this module is just the math + the contract.

Lives in the Apache-2.0 ``manthana-skills`` package so BOTH the agent (Apache) and
the server (AGPL) can import it without a cross-package license bleed. Reuses the
miner's embedder + cosine.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .embed import Embedder, Vector, cosine


class HasId(Protocol):
    id: str


@dataclass(frozen=True)
class Coverage:
    """How much of the matching set the answer actually saw."""

    matched: int  # candidates that passed the structured filter
    used: int  # how many were ranked into the prompt

    @property
    def truncated(self) -> bool:
        return self.used < self.matched

    def note(self) -> str:
        return (
            f"answered over the {self.used} most relevant of {self.matched} matching "
            "compactions — narrow the question to cover more"
            if self.truncated
            else f"answered over all {self.matched} matching compactions"
        )


def text_hash(text: str) -> str:
    """Stable hash of the embedded text → re-embed only when content changes."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def rank_scored(
    query: str,
    items: Sequence[HasId],
    vectors: dict[str, Vector],
    embedder: Embedder,
    *,
    k: int,
) -> tuple[list[tuple[float, HasId]], Coverage]:
    """Like ``rank`` but returns ``(cosine_score, item)`` pairs (top-``k``), so callers
    that need to THRESHOLD on relevance (e.g. prior-work surfacing) can. Items without a
    current-dim vector are kept but sorted last with score 0.0 (never silently dropped)."""
    qv = embedder.embed([query])[0]
    scored: list[tuple[float, HasId]] = []
    unscored: list[HasId] = []
    for item in items:
        vec = vectors.get(item.id)
        if vec is not None and len(vec) == len(qv):
            scored.append((cosine(qv, vec), item))
        else:
            unscored.append(item)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    ranked: list[tuple[float, HasId]] = scored + [(0.0, item) for item in unscored]
    return ranked[:k], Coverage(matched=len(items), used=min(k, len(ranked)))


def rank(
    query: str,
    items: Sequence[HasId],
    vectors: dict[str, Vector],
    embedder: Embedder,
    *,
    k: int,
) -> tuple[list[HasId], Coverage]:
    """Rank ``items`` by cosine similarity of their cached vectors to ``query``.

    Items without a (current-dim) vector are kept but sorted last (never silently
    dropped). Returns the top-``k`` and a Coverage over the full matched set.
    """
    ranked, coverage = rank_scored(query, items, vectors, embedder, k=k)
    return [item for _, item in ranked], coverage


__all__ = ["Coverage", "HasId", "rank", "rank_scored", "text_hash"]
