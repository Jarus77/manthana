"""Prior-work surfacing action (engineer, notify, opt-out) — the compounding loop.

When a session is compacted, rank the new digest against the engineer's PRIOR
compactions (local semantic retrieval) and surface the most relevant past work, so
they benefit from their own history in real time. This is also the first real test
of the local embeddings for *retrieval* (not just clustering) — see
``validation/embed_eval.py``.

Builds on the default embedder (HashingEmbedder offline; bge-large if the
``embeddings`` extra is installed). Reuses the agent vector cache + the shared
``rank_scored`` so only relevant (>= tau) priors are shown.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.schemas import Action, ActionActor, ActionOutcome, ActionShape, ConsentClass
from manthana.skills.cluster import default_text_of
from manthana.skills.embed import Embedder, default_embedder
from manthana.skills.retrieval import rank_scored, text_hash

from ..store import Store
from .base import ActionContext, ActionResult, TriggerEvent

PRIOR_WORK_ACTION = Action(
    id="prior_work",
    name="Prior-work surfacing",
    shape=ActionShape.notify,
    actor=ActionActor.engineer,
    consent_class=ConsentClass.opt_out,
    description="Surface the engineer's most relevant past compactions for a new session.",
)

_K = 5  # how many priors to surface
# Minimum cosine to count as "related". Calibrated by validation/embed_eval.py: the offline
# HashingEmbedder scores even unrelated AI-coding sessions ~0.4-0.5 (shared generic vocab), so
# a low tau surfaces noise — 0.45 keeps only the strong matches. bge-large (the `embeddings`
# extra) is semantically sharper and is recommended when retrieval precision matters.
_TAU = 0.45
_MAX_SCAN = 5000  # cap on the prior-compaction candidate set


def find_prior_work(
    store: Store,
    session_id: str,
    *,
    embedder: Embedder | None = None,
    k: int = _K,
    tau: float = _TAU,
) -> list[tuple[float, object]]:
    """Return ``(score, compaction)`` for the engineer's prior compactions most relevant
    to the just-compacted session, filtered to score >= ``tau``. Excludes the session's
    own digest. Empty if no compaction, no priors, or nothing clears ``tau``."""
    new = store.get_compaction(f"comp-{session_id}")
    if new is None:
        return []
    candidates = [
        c
        for c in store.list_compactions(limit=_MAX_SCAN)
        if c.id != new.id and c.session_id != session_id
    ]
    if not candidates:
        return []
    embedder = embedder or default_embedder()
    try:
        # Ensure each candidate has a current cached vector, then rank by similarity to
        # the NEW digest's text (the query). Mirrors insights._index_and_rank but scored.
        have = store.vector_meta()
        todo: list[tuple[str, str, str]] = []
        for c in candidates:
            txt = default_text_of(c)
            h = text_hash(txt)
            if have.get(c.id) != (embedder.dim, h):
                todo.append((c.id, txt, h))
        if todo:
            vecs = embedder.embed([t for _, t, _ in todo])
            for (cid, _txt, h), v in zip(todo, vecs, strict=True):
                store.upsert_vector(cid, dim=embedder.dim, text_hash=h, vec=v)
        vectors = store.get_vectors([c.id for c in candidates], dim=embedder.dim)
        ranked, _coverage = rank_scored(default_text_of(new), candidates, vectors, embedder, k=k)
    except Exception:  # noqa: BLE001 - embedder/index failure → surface nothing, never crash
        return []
    return [(score, c) for score, c in ranked if score >= tau]


class PriorWorkHandler:
    """Handler for the prior-work surfacing action."""

    action: Action = PRIOR_WORK_ACTION

    def handles(self, event: TriggerEvent) -> bool:
        return event.type == "session_closed"

    def run(self, event: TriggerEvent, ctx: ActionContext) -> ActionResult:
        if event.session_id is None:
            return ActionResult(ActionOutcome.failed, "no_session_id")
        related = find_prior_work(ctx.store, event.session_id)
        if not related:
            return ActionResult(ActionOutcome.suppressed, "no_relevant_prior")
        details = {
            "session_id": event.session_id,
            "related": [
                {
                    "id": c.id,  # type: ignore[attr-defined]
                    "project": c.project,  # type: ignore[attr-defined]
                    "intent": c.task_intent[:100],  # type: ignore[attr-defined]
                    "score": round(score, 3),
                }
                for score, c in related
            ],
        }
        return ActionResult(
            ActionOutcome.fired, "prior_work_found", confidence=round(related[0][0], 3),
            details=details,
        )


__all__ = ["PriorWorkHandler", "PRIOR_WORK_ACTION", "find_prior_work"]
