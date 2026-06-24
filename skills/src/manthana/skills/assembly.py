"""Assembly primitives for the query engine: threads + topics (shared, store-agnostic).

- **Threads** = an engineer's arc across the resumed slices of one transcript. Sessionize
  ids a transcript as ``<base>`` + ``<base>.2`` … chained by ``resumed_from``; so the
  thread key is the base id (strip a trailing ``.N``). Recoverable from the id alone on
  both sides — no schema change.
- **Topics** = emergent clusters that span sessions AND engineers, via the miner's
  clustering. ``recurring(min_contributors=N)`` is the k-anonymity gate; the founder sees
  a ``deidentified()`` view, the manager/engineer sees named members.

Lives in the Apache-2.0 ``manthana-skills`` package so agent (Apache) + server (AGPL)
share it. Reuses ``cluster_compactions`` / ``recurring`` (no new ML).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from manthana.schemas import BaseCompaction

from .cluster import cluster_compactions, recurring
from .embed import Embedder

_SLICE_SUFFIX = re.compile(r"\.\d+$")  # sessionize slice suffix: "<uuid>.2"


def thread_key(session_id: str) -> str:
    """The base transcript id for a session — its thread. Strips a ``.N`` slice suffix."""
    return _SLICE_SUFFIX.sub("", session_id)


def group_threads(compactions: Sequence[BaseCompaction]) -> dict[str, list[BaseCompaction]]:
    """Group compactions into threads (by base session id), each ordered chronologically."""
    threads: dict[str, list[BaseCompaction]] = {}
    for c in compactions:
        threads.setdefault(thread_key(c.session_id), []).append(c)
    for members in threads.values():
        members.sort(key=lambda c: c.started_at)
    return threads


@dataclass
class Topic:
    """An emergent cluster of related compactions (spanning sessions / engineers)."""

    id: str
    label: str
    members: list[str]  # compaction ids
    contributors: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    cohesion: float = 0.0
    sample_intents: list[str] = field(default_factory=list)

    def deidentified(self) -> dict[str, Any]:
        """Founder-safe view: counts + sample intents, NO actor names."""
        return {
            "id": self.id,
            "label": self.label,
            "contributor_count": len(self.contributors),
            "session_count": len(self.sessions),
            "sample_intents": self.sample_intents,
        }


def topics(
    compactions: Sequence[BaseCompaction],
    embedder: Embedder,
    *,
    min_contributors: int = 1,
    min_sessions: int = 1,
) -> list[Topic]:
    """Cluster compactions into recurring topics, gated by the recurrence / k-anon floor.

    ``min_contributors=k_anon_floor`` gives the founder view (de-identified); ``=1`` gives
    an engineer's own topics or the manager's named view. Labels are deterministic (a
    representative member's intent) — no LLM, no token spend.
    """
    kept = recurring(
        cluster_compactions(compactions, embedder),
        min_contributors=min_contributors,
        min_sessions=min_sessions,
    )
    out: list[Topic] = []
    for cluster in kept:
        members = [c.id for c in cluster.compactions]
        intents = [c.task_intent[:90] for c in cluster.compactions[:3]]
        tid = "topic-" + hashlib.blake2b(
            "|".join(sorted(members)).encode("utf-8"), digest_size=6
        ).hexdigest()
        out.append(
            Topic(
                id=tid,
                label=(cluster.compactions[0].task_intent[:90] if cluster.compactions else ""),
                members=members,
                contributors=set(cluster.contributors),
                sessions=set(cluster.sessions),
                cohesion=cluster.cohesion,
                sample_intents=intents,
            )
        )
    # Most-shared topics first (more contributors, then more sessions).
    out.sort(key=lambda t: (len(t.contributors), len(t.sessions)), reverse=True)
    return out


__all__ = ["Topic", "thread_key", "group_threads", "topics"]
