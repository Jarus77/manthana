"""Shared cached-embedding access for released compactions.

Embedding is the expensive part of every semantic path on the server (founder
retrieval, org skill mining). The vectors live in the DB keyed by
``(org_id, compaction_id, dim, text_hash)``, so a compaction is embedded once and
re-embedded only when its text or the embedder's dimensionality changes. Any new
semantic path MUST go through here rather than calling ``embedder.embed`` over a
whole corpus — that is what made org mining take minutes per run.

The cache only ever holds what ``query_compactions`` returns (released rows), so
it can never retain unreleased or personal-mode content.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from manthana.skills.cluster import default_text_of
from manthana.skills.embed import Embedder, Vector
from manthana.skills.retrieval import text_hash

if TYPE_CHECKING:
    from .store import ServerStore


def ensure_vectors(
    store: ServerStore, org_id: str, candidates: list[Any], embedder: Embedder
) -> dict[str, Vector]:
    """Embed only what is missing/stale, persist it, and return id -> vector.

    Vectors are keyed on the SAME text (``default_text_of``) every caller uses, so
    the founder-query cache and the mining cache are one cache, not two.
    """
    have = store.vector_meta(org_id)
    todo: list[tuple[str, str, str]] = []
    for c in candidates:
        txt = default_text_of(c)
        h = text_hash(txt)
        if have.get(c.id) != (embedder.dim, h):
            todo.append((c.id, txt, h))
    if todo:
        vecs = embedder.embed([t for _, t, _ in todo])
        for (cid, _txt, h), v in zip(todo, vecs, strict=True):
            store.upsert_vector(org_id, cid, dim=embedder.dim, text_hash=h, vec=v)
    return store.get_vectors(org_id, [c.id for c in candidates], dim=embedder.dim)


__all__ = ["ensure_vectors"]
