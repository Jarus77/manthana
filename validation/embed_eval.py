"""Embedding retrieval-quality eval — is local embedding useful for *retrieval*?

Prior-work surfacing (Phase C) is the first use of the local embeddings for retrieval,
not just clustering. This compares the offline default (HashingEmbedder) against
bge-large (the optional `embeddings` extra) on the REAL local corpus, using
`find_prior_work` — for each of the 10 validation sessions, what prior compactions does
each embedder surface?

Metrics (proxies — related work can legitimately cross projects, so read these as
signal, not ground truth):
  * mean top-1 cosine  — how confident the strongest match is
  * same-project@3     — fraction of the top-3 sharing the query's project (domain grouping)
  * hits@tau           — how many of the 10 surface ANY related prior at the default tau

Read-only on your data: compactions are copied into an IN-MEMORY store, so the real
vector cache is never touched.

Run:  uv run python validation/embed_eval.py
      uv sync --extra embeddings   # to include bge-large in the comparison
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from manthana.agent.actions.prior_work import _TAU, find_prior_work
from manthana.agent.store import Store
from manthana.skills.embed import DEFAULT_MODEL, Embedder, HashingEmbedder

# The 10 validation sessions (same slate as recompact_ten.py).
TEN = [
    "59896ed2-495e-4661-a0a5-ec85c8318ed9.11", "138db3b9-4762-4d01-8633-2764c60b197c.2",
    "1782ce84-8cc9-44c7-9f70-c17a85fb1111.7", "7f09f08c-a4c3-4360-ab62-6063864bdce9",
    "10375d20-b5a4-4e08-8d51-198705175757", "b636d78d-c0c8-4e94-a7c7-52f8d0a8dfa3.96",
    "2eddfe69-cd5e-46c8-bfae-a3b77eefe20f.7", "a8aeb113-5f21-41cd-8e7d-3a9ecb6d8cf7.12",
    "4811a97d-8461-4c22-b9b1-d4951bd853ab", "2647d07f-646e-444d-b3a6-c16c94f67b70",
]


def _eval(label: str, embedder: Embedder, n_corpus: int) -> None:
    mem = Store.open_memory()
    real = Store.open()
    by_id = {c.session_id: c for c in real.list_compactions(limit=5000)}
    for c in real.list_compactions(limit=5000):
        mem.upsert_compaction(c)

    print(f"\n=== {label} (dim={embedder.dim}, corpus={n_corpus}) ===")
    top1s: list[float] = []
    same_proj: list[float] = []
    hits = 0
    for sid in TEN:
        q = by_id.get(sid)
        if q is None:
            continue
        # tau=0 to inspect the raw ranking; report hits at the default tau separately.
        ranked = find_prior_work(mem, sid, embedder=embedder, k=3, tau=0.0)
        if not ranked:
            print(f"  {sid[:12]}: (no priors)")
            continue
        top1s.append(ranked[0][0])
        same = sum(1 for _, c in ranked if c.project == q.project) / len(ranked)  # type: ignore[attr-defined]
        same_proj.append(same)
        if ranked[0][0] >= _TAU:
            hits += 1
        shown = ", ".join(
            f"{c.project}/{s:.2f}" for s, c in ranked  # type: ignore[attr-defined]
        )
        print(f"  {sid[:12]} [{q.project}]: {shown}")
    if top1s:
        print(
            f"  → mean top-1 cosine={sum(top1s) / len(top1s):.3f} | "
            f"same-project@3={sum(same_proj) / len(same_proj):.2f} | "
            f"hits@tau({_TAU})={hits}/{len(top1s)}"
        )


def main() -> None:
    n = len(Store.open().list_compactions(limit=5000))
    _eval("HashingEmbedder (default, offline)", HashingEmbedder(), n)
    try:
        from manthana.skills.embed import SentenceTransformerEmbedder

        bge = SentenceTransformerEmbedder(DEFAULT_MODEL)
    except Exception as exc:  # noqa: BLE001 - extra not installed
        print(f"\n(bge-large unavailable: {type(exc).__name__} — `uv sync --extra embeddings`)")
        return
    _eval(f"{DEFAULT_MODEL} (bge-large)", bge, n)


if __name__ == "__main__":
    main()
