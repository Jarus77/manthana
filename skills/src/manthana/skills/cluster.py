"""Clustering compactions into recurring task-pattern clusters.

Uses the sentence-transformers "fast clustering" / community-detection algorithm
(greedy, non-overlapping, unknown-k): for each point gather all points within a
cosine threshold; keep communities above a minimum size; take largest-first,
removing already-assigned points. k-means is deliberately avoided (fixed k).

The k-anonymity / recurrence gate (>=N distinct contributors or sessions) is
applied AFTER clustering, on cluster membership — so 10 sessions from one person
do NOT qualify as a shared pattern.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from manthana.schemas import BaseCompaction

from .embed import Embedder, Vector, cosine

DEFAULT_THRESHOLD = 0.75  # SBERT community_detection default cosine cutoff
DEFAULT_MIN_CLUSTER_SIZE = 2  # a "pattern" needs at least two occurrences

_BLOCK = 512  # rows per numpy similarity block — bounds peak memory at large n


def _as_matrix(embeddings: list[Vector]) -> Any | None:
    """L2-normalized numpy matrix of the embeddings, or None if numpy is absent.

    numpy is an OPTIONAL accelerator (same posture as the sentence-transformers
    extra in ``embed``): with it, the O(n^2) similarity pass is one BLAS matmul
    instead of ~n^2/2 Python-level cosines, which is the difference between
    seconds and minutes at n in the thousands. The pure-Python path below stays
    the correctness reference and the two must agree.
    """
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - numpy present in the dev env
        return None
    if not embeddings:
        return None
    matrix = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0  # a zero vector stays zero (cosine 0), never NaN
    return matrix / norms


def _neighbor_sets(embeddings: list[Vector], threshold: float) -> list[set[int]]:
    """For each point, the indices within ``threshold`` cosine of it (self included).

    Stores adjacency (O(edges)) rather than a dense n*n float matrix (O(n^2)), so a
    few thousand compactions cost megabytes, not gigabytes.
    """
    n = len(embeddings)
    nbrs: list[set[int]] = [{i} for i in range(n)]
    matrix = _as_matrix(embeddings)
    if matrix is not None:
        import numpy as np  # type: ignore[import-not-found]

        for start in range(0, n, _BLOCK):
            block = matrix[start : start + _BLOCK] @ matrix.T
            for row, sims in enumerate(block):
                nbrs[start + row].update(np.flatnonzero(sims >= threshold).tolist())
        return nbrs
    for i in range(n):  # pragma: no cover - exercised only when numpy is absent
        for j in range(i + 1, n):
            if cosine(embeddings[i], embeddings[j]) >= threshold:
                nbrs[i].add(j)
                nbrs[j].add(i)
    return nbrs


def community_detection(
    embeddings: list[Vector],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_community_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> list[list[int]]:
    """Greedy non-overlapping communities (SBERT-style). Returns index lists."""
    candidates = [m for m in _neighbor_sets(embeddings, threshold) if len(m) >= min_community_size]
    candidates.sort(key=len, reverse=True)

    result: list[list[int]] = []
    assigned: set[int] = set()
    for members in candidates:
        fresh = members - assigned
        if len(fresh) >= min_community_size:
            result.append(sorted(fresh))
            assigned |= fresh
    return result


def _cohesion(embeddings: list[Vector], indices: list[int]) -> float:
    """Mean pairwise cosine within a cluster (a confidence signal)."""
    if len(indices) < 2:
        return 1.0
    matrix = _as_matrix([embeddings[i] for i in indices])
    if matrix is not None:
        import numpy as np  # type: ignore[import-not-found]

        sims = matrix @ matrix.T
        k = len(indices)
        # Mean of the strict upper triangle = (total - diagonal) / 2 / #pairs.
        total = float(sims.sum()) - float(np.trace(sims))
        return round(total / 2.0 / (k * (k - 1) / 2.0), 4)
    pairs = [  # pragma: no cover - exercised only when numpy is absent
        cosine(embeddings[a], embeddings[b])
        for ai, a in enumerate(indices)
        for b in indices[ai + 1 :]
    ]
    return round(sum(pairs) / len(pairs), 4) if pairs else 1.0


@dataclass
class CompactionCluster:
    compactions: list[BaseCompaction]
    cohesion: float
    contributors: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.compactions)


def default_text_of(compaction: BaseCompaction) -> str:
    """The semantic content used for embedding a compaction."""
    return f"{compaction.task_intent} {compaction.approach}".strip()


DEFAULT_MAX_ITEMS = 2000  # community_detection is O(n^2); cap to bound time/memory


def cluster_compactions(
    compactions: Sequence[BaseCompaction],
    embedder: Embedder,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    max_items: int = DEFAULT_MAX_ITEMS,
    text_of: Callable[[BaseCompaction], str] = default_text_of,
    vectors: dict[str, Vector] | None = None,
) -> list[CompactionCluster]:
    """``vectors`` is an optional id -> vector cache (e.g. the server's stored
    compaction vectors). Hits skip the embedder entirely and only the misses are
    embedded, so a repeat run over a mostly-unchanged corpus costs ~nothing —
    without it, every run re-embeds the whole corpus from scratch. Cached vectors
    MUST have been produced by the same ``text_of`` and the same embedder/dim as
    the one passed here, or clustering compares incomparable spaces.
    """
    if not compactions:
        return []
    # The similarity pass is O(n^2); cap n so a huge store can't OOM/hang.
    # Inputs are most-recent-first, so we keep the newest.
    items = list(compactions)[:max_items]
    cache = vectors or {}
    todo = [i for i, c in enumerate(items) if c.id not in cache]
    fresh = embedder.embed([text_of(items[i]) for i in todo]) if todo else []
    embeddings: list[Vector] = [cache.get(c.id) or [] for c in items]
    for i, vec in zip(todo, fresh, strict=True):
        embeddings[i] = vec
    clusters: list[CompactionCluster] = []
    for indices in community_detection(
        embeddings, threshold=threshold, min_community_size=min_cluster_size
    ):
        members = [items[i] for i in indices]
        clusters.append(
            CompactionCluster(
                compactions=members,
                cohesion=_cohesion(embeddings, indices),
                contributors={c.actor for c in members},
                sessions={c.session_id for c in members},
            )
        )
    return clusters


def recurring(
    clusters: list[CompactionCluster],
    *,
    min_contributors: int = 1,
    min_sessions: int = 1,
) -> list[CompactionCluster]:
    """Keep only clusters that meet the recurrence / k-anonymity floor."""
    return [
        c
        for c in clusters
        if len(c.contributors) >= min_contributors and len(c.sessions) >= min_sessions
    ]


__all__ = [
    "community_detection",
    "cluster_compactions",
    "recurring",
    "CompactionCluster",
    "default_text_of",
    "DEFAULT_THRESHOLD",
    "DEFAULT_MIN_CLUSTER_SIZE",
]
