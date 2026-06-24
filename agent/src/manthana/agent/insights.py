"""Engineer self-query over the LOCAL store.

Two tiers:
  * ``structural_insights`` — no LLM, no tokens: rollups straight from the store
    (projects, outcomes, cost, friction, "last 7 days"). Works the moment you've
    captured sessions, before any compaction exists.
  * ``ask`` — a grounded, cited natural-language answer over your own compactions
    (every claim must cite a compaction id, or it's flagged ungrounded).

This re-expresses the server's founder-query pipeline for the single-actor local
store (no org / k-anonymity scoping — it's your own data). It deliberately does
NOT import ``manthana.server`` (that package is AGPL; this is the Apache-2.0 agent).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from manthana.skills.assembly import Topic, thread_key
from manthana.skills.assembly import topics as _build_topics
from manthana.skills.cluster import default_text_of
from manthana.skills.embed import Embedder, default_embedder
from manthana.skills.retrieval import Coverage, rank, text_hash

from .cost import estimate_cost
from .llm import LLMProvider, default_provider
from .store import Store

INSUFFICIENT = "No compactions yet — run `manthana compact` first, then ask again."
_CITE_RE = re.compile(r"\[([^\]]+)\]")
_SINCE_RE = re.compile(r"^(\d+)\s*([dwh])$")  # 7d / 2w / 12h
_MAX_SCAN = 5000  # cap store reads (the structured-filter candidate set)
_COST_SCAN_CAP = 300  # cost reads turns per session; bound it (most-recent first)
_ANSWER_K = 40  # how many ranked digests the narrative answers over


@dataclass
class StructuralInsights:
    since: str | None
    session_count: int
    compaction_count: int
    by_project: dict[str, int]  # sessions per project (works without compactions)
    by_outcome: dict[str, int]  # compactions per outcome
    est_cost_usd: float
    top_friction: list[str]
    cost_capped: bool = False  # True if cost is over the most-recent _COST_SCAN_CAP only


@dataclass
class AskResult:
    narrative: str
    citations: list[str]
    grounded: bool
    filtered_to: dict[str, str] = field(default_factory=dict)
    coverage: Coverage | None = None  # how much of the matched set the answer saw


_PARSE_PROMPT = (
    "Parse this question about an engineer's OWN coding sessions into a JSON filter "
    'with keys: project, outcome (success|partial|abandoned), since ("Nd"/"Nw" or '
    "ISO date). Use null for anything unspecified. Return ONLY the JSON object.\n"
    "Question: {query}"
)
_NARRATIVE_PROMPT = (
    "Answer the engineer's question in 2-4 sentences based ONLY on this data "
    "({coverage}). Cite the specific compaction id in [square brackets] for EVERY "
    "claim; do not invent facts.\nQuestion: {query}\nCompactions: {compactions}\n"
)


def _index_and_rank(
    store: Store, query: str, candidates: list[Any], embedder: Embedder, k: int
) -> tuple[list[Any], Coverage]:
    """Ensure each candidate has a current cached vector, then rank by relevance.

    Indexing is scoped to the (already structured-filtered) candidates and cached,
    so the first ask over a slice embeds only that slice and later asks are instant.
    """
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
    return rank(query, candidates, vectors, embedder, k=k)


def _as_utc(value: datetime | None) -> datetime | None:
    # Naive datetimes are assumed UTC (the store normalizes timestamps to UTC).
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _within(started: datetime | None, cutoff: datetime | None) -> bool:
    """True if ``started`` is on/after ``cutoff`` (cutoff None = all time)."""
    if cutoff is None:
        return True
    ts = _as_utc(started)
    return ts is not None and ts >= cutoff


def _since_cutoff(since: str | None, *, now: datetime | None = None) -> datetime | None:
    """Turn '7d' / '2w' / '12h' / an ISO date into a UTC cutoff (None = all time)."""
    if not since:
        return None
    now = now or datetime.now(UTC)
    m = _SINCE_RE.match(since.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return now - delta
    try:
        return _as_utc(datetime.fromisoformat(since))
    except ValueError:
        return None


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}


def _match_citations(narrative: str, ids: list[str]) -> list[str]:
    """Map bracketed citations to compaction ids by exact-or-unique-prefix (models
    abbreviate long ids); ambiguous prefixes ground nothing. Order preserved."""
    pieces: set[str] = set()
    for token in _CITE_RE.findall(narrative):
        for part in re.split(r"[,\s]+", token.strip()):
            if part:
                pieces.add(part)
    matched = {
        hits[0]
        for piece in pieces
        if len(hits := [cid for cid in ids if cid == piece or cid.startswith(piece)]) == 1
    }
    return [cid for cid in ids if cid in matched]


def structural_insights(store: Store, *, since: str | None = None) -> StructuralInsights:
    """Token-free rollups from the local store. ``since`` accepts '7d'/'2w'/ISO."""
    cutoff = _since_cutoff(since)
    sessions = [s for s in store.list_sessions(limit=_MAX_SCAN) if _within(s.started_at, cutoff)]
    comps = [c for c in store.list_compactions(limit=_MAX_SCAN) if _within(c.started_at, cutoff)]

    by_project: dict[str, int] = defaultdict(int)
    for s in sessions:
        by_project[s.project] += 1
    by_outcome: dict[str, int] = defaultdict(int)
    friction: list[str] = []
    for c in comps:
        by_outcome[str(c.outcome)] += 1
        friction += [fp.description for fp in getattr(c, "friction_points", []) if fp.description]

    # Cost reads turns per session (an extra query each); bound it to the most
    # recent _COST_SCAN_CAP so the panel stays snappy on a large history.
    cost_sessions = sessions[:_COST_SCAN_CAP]
    cost = sum(estimate_cost(store.get_turns(s.id)).usd for s in cost_sessions)
    return StructuralInsights(
        since=since,
        session_count=len(sessions),
        compaction_count=len(comps),
        by_project=dict(sorted(by_project.items(), key=lambda kv: -kv[1])),
        by_outcome=dict(by_outcome),
        est_cost_usd=round(cost, 4),
        top_friction=friction[:5],
        cost_capped=len(sessions) > _COST_SCAN_CAP,
    )


def ask(
    store: Store,
    query: str,
    *,
    provider: LLMProvider | None = None,
    source: str | None = None,
    embedder: Embedder | None = None,
) -> AskResult:
    """Grounded, cited NL answer over your own compactions.

    ``source`` filters by how the compaction was made: None = all (default, the
    cheapest digests included), "full" = only full compactions, "claude_summary" =
    only the cheap summary-derived ones.
    """
    provider = provider or default_provider()
    # 1) light NL → filter (degrade to no filter on any provider error)
    spec: dict[str, Any] = {}
    try:
        spec = _extract_json(provider.complete(_PARSE_PROMPT.format(query=query)))
    except Exception:  # noqa: BLE001 - filter parsing is best-effort
        spec = {}
    project = spec.get("project") if isinstance(spec.get("project"), str) else None
    raw_outcome = spec.get("outcome")
    outcome = raw_outcome if raw_outcome in {"success", "partial", "abandoned"} else None
    cutoff = _since_cutoff(spec.get("since") if isinstance(spec.get("since"), str) else None)

    comps = store.list_compactions(project=project, outcome=outcome, limit=_MAX_SCAN)
    comps = [c for c in comps if _within(c.started_at, cutoff)]
    if source:
        comps = [c for c in comps if getattr(c, "source", "full") == source]
    active = {"project": project, "outcome": outcome, "source": source}
    filtered = {k: v for k, v in active.items() if v}

    if not comps:
        return AskResult(narrative=INSUFFICIENT, citations=[], grounded=False, filtered_to=filtered)

    # 2) semantic rank the filtered candidates → top-K (+ coverage, no silent truncation)
    top, coverage = _index_and_rank(store, query, comps, embedder or default_embedder(), _ANSWER_K)
    brief = [
        {"id": c.id, "project": c.project, "intent": c.task_intent, "outcome": str(c.outcome)}
        for c in top
    ]
    try:
        narrative = provider.complete(
            _NARRATIVE_PROMPT.format(
                query=query, compactions=json.dumps(brief), coverage=coverage.note()
            )
        ).strip()
    except Exception:  # noqa: BLE001 - provider failure → no answer, never a crash
        return AskResult(
            narrative="Couldn't reach the model to answer.", citations=[], grounded=False,
            filtered_to=filtered, coverage=coverage,
        )
    citations = _match_citations(narrative, [c.id for c in top])
    return AskResult(
        narrative=narrative, citations=citations, grounded=bool(citations),
        filtered_to=filtered, coverage=coverage,
    )


def my_topics(store: Store, *, embedder: Embedder | None = None) -> list[Topic]:
    """The engineer's own emergent topic clusters (own data → no k-anon)."""
    comps = store.list_compactions(limit=_MAX_SCAN)
    return _build_topics(comps, embedder or default_embedder(), min_contributors=1)


def drill_raw(
    store: Store, compaction_id: str, *, start: int = 0, end: int | None = None
) -> list[Any]:
    """The engineer's own raw turns behind a compaction (own data → unredacted).

    Tier-2 depth when the digest is too lossy. Returns ``[]`` if unknown.
    """
    comp = store.get_compaction(compaction_id)
    if comp is None:
        return []
    return store.get_turns(comp.session_id)[start:end]


def thread(store: Store, session_id: str) -> list[Any]:
    """The arc of one transcript — its resumed slices' compactions, in order."""
    base = thread_key(session_id)
    comps = [c for c in store.list_compactions(limit=_MAX_SCAN) if thread_key(c.session_id) == base]
    comps.sort(key=lambda c: c.started_at)
    return comps


__all__ = [
    "StructuralInsights",
    "AskResult",
    "structural_insights",
    "ask",
    "my_topics",
    "thread",
    "drill_raw",
    "INSUFFICIENT",
]
