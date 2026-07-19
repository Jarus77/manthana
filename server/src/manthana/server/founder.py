"""Founder query: structured-filter-first, narrative-second (decisions doc).

Pipeline: NL query -> LLM-parsed + validated structured filter -> org-scoped SQL
over released compactions -> k-anonymity floor (global AND per sub-aggregate) ->
grounded narrative whose every claim cites compaction ids. Grounding is
non-optional: too few contributors (k-anon), or a narrative with no citations,
returns "insufficient data" rather than an ungrounded/hallucinated answer.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from manthana.schemas import Surface
from manthana.skills.assembly import Topic, thread_key
from manthana.skills.assembly import topics as _build_topics
from manthana.skills.cluster import DEFAULT_MAX_ITEMS
from manthana.skills.embed import Embedder, default_embedder
from manthana.skills.retrieval import Coverage, rank
from pydantic import BaseModel, ConfigDict, ValidationError

from .config import ServerConfig
from .llm import LLMProvider
from .metering import QuotaExceededError
from .store import ServerStore
from .vectors import ensure_vectors

_log = logging.getLogger(__name__)

INSUFFICIENT = "insufficient data"
_ANSWER_K = 40  # how many ranked digests the narrative answers over
_VALID_OUTCOMES = {"success", "partial", "abandoned"}
_VALID_SURFACES = {s.value for s in Surface}
_CITE_RE = re.compile(r"\[([^\]]+)\]")


class FounderFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    team_id: str | None = None
    project: str | None = None
    outcome: str | None = None
    actor: str | None = None
    surface: str | None = None
    since: str | None = None  # ISO-8601
    until: str | None = None


@dataclass
class Rollup:
    session_count: int
    distinct_contributors: int
    by_project: dict[str, int]
    by_outcome: dict[str, int]
    total_cost_usd: float  # API list-price equivalent, NOT subscription spend
    total_tokens: int
    # Sessions per named engineer — populated ONLY on the named/individual path
    # (a founder whose org runs privacy_mode="open"). Empty on the
    # de-identified path so names can never leak through the aggregate.
    by_engineer: dict[str, int] = field(default_factory=dict)


@dataclass
class FounderResult:
    filter: FounderFilter
    rollup: Rollup | None
    narrative: str
    citations: list[str]
    insufficient_data: bool
    coverage: Coverage | None = None  # how much of the visible set the answer saw


_PARSE_PROMPT = (
    "Today is {today} (UTC). Resolve any relative dates ('this week', 'last 30 days', "
    "'yesterday', 'recently') against THAT date, not your training cutoff.\n"
    "Parse this founder question into a JSON filter with keys: team_id, project, "
    "outcome (success|partial|abandoned), actor, surface (claude_code|codex|cursor), "
    "since (ISO date), until (ISO date). Use null for anything unspecified. "
    "IMPORTANT: do NOT set outcome just because the question mentions 'wrong', "
    "'failed', 'failing', 'problems', 'blockers', or 'friction' — those ask about "
    "friction WITHIN sessions of any outcome, so leave outcome null. Set outcome only "
    "when the user explicitly restricts to successful / partial / abandoned sessions. "
    "Return ONLY the JSON object.\nQuestion: {query}"
)

_NARRATIVE_PROMPT = (
    "Answer the founder's QUESTION in 2-4 sentences based ONLY on this data. Cite the "
    "specific compaction id in [square brackets] for EVERY claim; do not invent facts. "
    "If the data does not support a claim, omit it. Each compaction lists its `friction` "
    "(problems/blockers hit during the session, with a category) — use that to answer "
    "questions about what went wrong, what's failing, or where the team is blocked, even "
    "for sessions whose overall outcome was success.\n"
    "({coverage}.)\n"
    "QUESTION: {query}\nRollup: {rollup}\nCompactions: {compactions}\n"
)


def _index_and_rank(
    store: ServerStore, org_id: str, query: str, candidates: list[Any], embedder: Embedder, k: int
) -> tuple[list[Any], Coverage]:
    """Released-only semantic rank: ensure each visible compaction has a current
    cached vector, then rank by relevance. The index only ever contains what
    ``query_compactions`` returns (released), so it can't hold unreleased/personal."""
    try:
        vectors = ensure_vectors(store, org_id, candidates, embedder)
        return rank(query, candidates, vectors, embedder, k=k)
    except Exception:  # noqa: BLE001 - embedder/index failure degrades to unranked, never 500s
        _log.exception("founder retrieval: embedder/index failed, returning unranked")
        return candidates[:k], Coverage(matched=len(candidates), used=min(k, len(candidates)))


def _match_citations(narrative: str, visible: list[Any]) -> list[str]:
    """Resolve bracketed citations to visible compaction ids.

    Real models abbreviate long UUID-style ids (they cite a leading prefix) and
    sometimes group several ids in one bracket, so split each bracket on
    commas/whitespace and match a piece to an id by exact-or-**unique**-prefix.
    A piece that matches more than one id is ambiguous and dropped, so grounding
    stays conservative — it never grounds to the wrong compaction. Returns ids in
    their original (visible) order.
    """
    pieces: set[str] = set()
    for token in _CITE_RE.findall(narrative):
        for part in re.split(r"[,\s]+", token.strip()):
            if part:
                pieces.add(part)
    ids = [c.id for c in visible]
    matched: set[str] = set()
    for piece in pieces:
        hits = [cid for cid in ids if cid == piece or cid.startswith(piece)]
        if len(hits) == 1:  # unambiguous
            matched.add(hits[0])
    return [cid for cid in ids if cid in matched]


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
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _resolve_temporal(query: str, spec: FounderFilter, now: datetime) -> None:
    """Deterministically resolve common relative date phrases in the QUERY to concrete
    since/until (YYYY-MM-DD), overriding the LLM for these cases so 'this week' / 'last 30
    days' anchor to the actual clock (``now``), not the model's training cutoff. Phrases not
    matched here are left to the LLM (whose prompt is now date-anchored). Mutates ``spec``."""
    q = query.lower()
    today = now.date()

    def setw(since: date, until: date) -> None:
        spec.since = since.isoformat()
        spec.until = until.isoformat()

    m = re.search(r"\b(?:last|past|previous|in the last)\s+(\d{1,4})\s+days?\b", q)
    if m:
        setw(today - timedelta(days=int(m.group(1))), today)
        return
    if re.search(r"\btoday\b", q):
        setw(today, today)
    elif re.search(r"\byesterday\b", q):
        setw(today - timedelta(days=1), today - timedelta(days=1))
    elif re.search(r"\b(this week|past week|this past week|last 7 days|last seven days)\b", q):
        setw(today - timedelta(days=7), today)
    elif re.search(r"\blast week\b", q):
        setw(today - timedelta(days=14), today - timedelta(days=7))
    elif re.search(r"\bthis month\b", q):
        setw(today.replace(day=1), today)
    elif re.search(r"\blast month\b", q):
        last_prev = today.replace(day=1) - timedelta(days=1)  # last day of previous month
        setw(last_prev.replace(day=1), last_prev)
    elif re.search(r"\b(recently|lately|past month|last 30 days|last thirty days)\b", q):
        setw(today - timedelta(days=30), today)


def parse_filter(
    query: str, provider: LLMProvider, *, now: datetime | None = None
) -> FounderFilter:
    now = now or datetime.now(UTC)
    # A real provider (Anthropic) can raise (rate limit / network / auth); degrade
    # to an empty filter (match all) rather than 500 — and never let the raw SDK
    # exception reach the client.
    try:
        raw = provider.complete(_PARSE_PROMPT.format(query=query, today=now.date().isoformat()))
    except QuotaExceededError:
        # Quota exhaustion must surface as 429 to the caller, never degrade to a
        # misleading "insufficient data" answer.
        raise
    except Exception:  # noqa: BLE001 - any provider failure degrades gracefully
        _log.exception("founder filter parse: provider call failed")
        spec = FounderFilter()
        _resolve_temporal(query, spec, now)  # still anchor relative dates deterministically
        return spec
    data = _extract_json(raw)
    try:
        spec = FounderFilter.model_validate(data)
    except ValidationError:
        spec = FounderFilter()
    # Null out values that aren't valid enum members (else they silently match
    # zero rows and the founder gets a spurious "insufficient data").
    if spec.outcome is not None and spec.outcome not in _VALID_OUTCOMES:
        spec.outcome = None
    if spec.surface is not None and spec.surface not in _VALID_SURFACES:
        spec.surface = None
    # Deterministically anchor common relative-date phrases to `now` (overrides the LLM
    # for these), so temporal filters never depend on the model's notion of "today".
    _resolve_temporal(query, spec, now)
    return spec


def _resolve_actor(store: ServerStore, org_id: str, name: str | None) -> str | None:
    """Map an NL name fragment ("Suraj") to a real actor id ("suraj@acme.demo").

    Named/individual path only. Unique case-insensitive match against the org's known
    actors (id or local-part or display name); ambiguous / no match → unchanged
    (so the query simply finds nothing rather than guessing wrong).
    """
    if not name:
        return name
    nl = name.lower()
    hits: list[str] = []
    for a in store.list_actors(org_id):
        hay = f"{a.id} {a.id.split('@')[0]} {a.display_name or ''}".lower()
        if nl in hay:
            hits.append(a.id)
    return hits[0] if len(hits) == 1 else name


def _tokens(s: str) -> list[str]:
    """Lower-case alphanumeric word tokens (split on hyphen/underscore/space/punct)."""
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _resolve_project(store: ServerStore, org_id: str, name: str | None) -> str | None:
    """Map a free-text project name ("LLM evaluation") to a real slug ("llm-eval").

    The NL parser emits human phrasing while the stored value is a slug, so an exact
    (even case-insensitive) match misses ``llm evaluation`` vs ``llm-eval``. Token-prefix
    match: a known slug wins if every one of ITS tokens is a prefix of some query token
    (``[llm, eval]`` ⊆-prefix ``[llm, evaluation]``). Exact (case-insensitive) hits take
    priority. Unique winner → the slug; no / ambiguous match → unchanged (the
    case-insensitive filter still handles a plain casing difference, and a genuinely
    unknown project simply finds nothing rather than guessing wrong)."""
    if not name:
        return name
    known = store.list_projects(org_id)
    low = name.lower()
    for p in known:  # exact (case-insensitive) — let the SQL filter handle it
        if p.lower() == low:
            return p
    q = _tokens(name)
    if not q:
        return name
    hits = [
        p for p in known
        if (pt := _tokens(p)) and all(any(qt.startswith(t) for qt in q) for t in pt)
    ]
    return hits[0] if len(hits) == 1 else name


def _rollup(compactions: list[Any], floor: int) -> tuple[Rollup, set[tuple[str, str]]]:
    """Build the rollup plus the set of (project, outcome) CELLS backed by >= ``floor``
    distinct contributors.

    The narrative is gated on the actual *cell*, NOT on the two dimensions
    independently: gating project-membership and outcome-membership separately leaks a
    k=1 (project ∩ outcome) cohort whenever each dimension happens to clear the floor via
    *different* rows (e.g. a lone "abandoned" P1 session by one person, where "abandoned"
    only cleared globally via an unrelated project P2). The displayed by_project /
    by_outcome counts are per-dimension aggregates (each >= floor) and stay safe to show.
    """
    proj_count: dict[str, int] = defaultdict(int)
    proj_contrib: dict[str, set[str]] = defaultdict(set)
    out_count: dict[str, int] = defaultdict(int)
    out_contrib: dict[str, set[str]] = defaultdict(set)
    cell_contrib: dict[tuple[str, str], set[str]] = defaultdict(set)
    actors: set[str] = set()
    total = 0.0
    tokens = 0
    for c in compactions:
        outcome = str(c.outcome)
        proj_count[c.project] += 1
        proj_contrib[c.project].add(c.actor)
        out_count[outcome] += 1
        out_contrib[outcome].add(c.actor)
        cell_contrib[(c.project, outcome)].add(c.actor)
        actors.add(c.actor)
        total += c.est_cost_usd or 0.0
        tokens += getattr(c, "total_tokens", 0) or 0

    by_project = {p: n for p, n in proj_count.items() if len(proj_contrib[p]) >= floor}
    by_outcome = {o: n for o, n in out_count.items() if len(out_contrib[o]) >= floor}
    kept_cells = {cell for cell, contrib in cell_contrib.items() if len(contrib) >= floor}
    rollup = Rollup(
        session_count=len(compactions),
        distinct_contributors=len(actors),
        by_project=by_project,
        by_outcome=by_outcome,
        total_cost_usd=round(total, 6),
        total_tokens=tokens,
    )
    return rollup, kept_cells


def run_query(
    store: ServerStore,
    config: ServerConfig,
    *,
    org_id: str,
    query: str,
    provider: LLMProvider,
    source: str | None = None,
    allow_individual: bool = False,
    embedder: Embedder | None = None,
    now: datetime | None = None,
    since: str | None = None,
    until: str | None = None,
) -> FounderResult:
    """``allow_individual`` is the **named view**: it skips the k-anonymity floor
    so a query that resolves to a single named person returns results. It is set only
    for orgs whose privacy_mode is "open", and every such call is audited by the
    caller. The de-identified path is unchanged: per-person queries are suppressed.
    ``now`` anchors relative-date parsing (defaults to the wall clock; injectable for tests).
    ``since``/``until`` (ISO dates) FORCE the time window, overriding whatever the query
    parsed — used by the weekly digest so every section covers the same period."""
    spec = parse_filter(query, provider, now=now)
    if since is not None:
        spec.since = since
    if until is not None:
        spec.until = until
    # Resolve a free-text project ("LLM evaluation") to a real slug ("llm-eval") so a
    # phrasing mismatch doesn't silently return nothing (semantic retrieval still ranks
    # within the resolved set). Safe on both paths — it only narrows to a known slug.
    spec.project = _resolve_project(store, org_id, spec.project)
    if allow_individual and spec.actor:
        # Keep the actor filter ONLY if it resolves to a real person. A comparison
        # ("Suraj vs Tarun") or an unresolved name → drop it, so the narrative sees
        # everyone matched and can compare named individuals (person-relational).
        resolved = _resolve_actor(store, org_id, spec.actor)
        known = {a.id for a in store.list_actors(org_id)}
        spec.actor = resolved if resolved in known else None
    compactions = store.query_compactions(
        org_id=org_id,
        team_id=spec.team_id,
        project=spec.project,
        outcome=spec.outcome,
        actor=spec.actor,
        surface=spec.surface,
        since=spec.since,
        until=spec.until,
    )
    # source filter: None = all (default, includes cheap summary-derived); "full"
    # = full only; "claude_summary" = summary-derived only.
    if source:
        compactions = [c for c in compactions if getattr(c, "source", "full") == source]
    rollup, kept_cells = _rollup(compactions, config.k_anon_floor)

    # Global k-anonymity floor — SKIPPED only for the audited named view.
    if not allow_individual and rollup.distinct_contributors < config.k_anon_floor:
        return FounderResult(
            filter=spec, rollup=None, narrative=INSUFFICIENT, citations=[], insufficient_data=True
        )

    if allow_individual:
        # Named view: no per-cell suppression — individuals may be shown.
        visible = list(compactions)
        counts: dict[str, int] = defaultdict(int)
        for c in visible:
            counts[c.actor] += 1
        rollup.by_engineer = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    else:
        # Per-CELL k-anon: the narrative only sees compactions whose (project, outcome)
        # cell itself has >= floor distinct contributors, so it can never cite a cohort
        # that is sub-floor on the intersection (the two dimensions are NOT gated
        # independently — that would leak a k=1 cell when each dimension clears via
        # different rows).
        visible = [c for c in compactions if (c.project, str(c.outcome)) in kept_cells]
        # Cell-gating guarantees >= floor distinct contributors when visible is non-empty;
        # if nothing clears, withhold (keep the safe aggregate rollup).
        if len({c.actor for c in visible}) < config.k_anon_floor:
            return FounderResult(
                filter=spec, rollup=rollup, narrative=INSUFFICIENT, citations=[],
                insufficient_data=True,
            )
    # Semantic rank the visible set → top-K (+ coverage; no silent truncation).
    top, coverage = _index_and_rank(
        store, org_id, query, visible, embedder or default_embedder(), _ANSWER_K
    )
    brief = [
        {
            "id": c.id,
            # The engineer's identity reaches the model ONLY on the named path.
            # Without this the narrative literally cannot attribute work to a
            # person, which is why "who did what" answers came back anonymous.
            **({"engineer": c.actor} if allow_individual else {}),
            "project": c.project,
            "intent": c.task_intent,
            "outcome": str(c.outcome),
            "friction": [
                {"category": str(fp.category), "issue": fp.description}
                for fp in (getattr(c, "friction_points", None) or [])
            ],
        }
        for c in top
    ]
    try:
        narrative = provider.complete(
            _NARRATIVE_PROMPT.format(
                query=query,
                rollup=json.dumps(rollup.__dict__),
                compactions=json.dumps(brief),
                coverage=coverage.note(),
            )
        ).strip()
    except QuotaExceededError:
        raise  # surface as 429, never as "insufficient data"
    except Exception:  # noqa: BLE001 - provider failure → withhold narrative, keep rollup
        _log.exception("founder narrative: provider call failed")
        return FounderResult(
            filter=spec, rollup=rollup, narrative=INSUFFICIENT, citations=[],
            insufficient_data=True, coverage=coverage,
        )

    citations = _match_citations(narrative, top)
    # Non-optional grounding: a narrative citing nothing is withheld (rollup kept).
    if not citations:
        return FounderResult(
            filter=spec, rollup=rollup, narrative=INSUFFICIENT, citations=[],
            insufficient_data=True, coverage=coverage,
        )

    return FounderResult(
        filter=spec,
        rollup=rollup,
        narrative=narrative,
        citations=citations,
        insufficient_data=False,
        coverage=coverage,
    )


def team_topics(
    store: ServerStore,
    config: ServerConfig,
    org_id: str,
    *,
    embedder: Embedder | None = None,
    named: bool = False,
) -> tuple[list[Topic], Coverage]:
    """Emergent topic clusters over released compactions, plus a coverage signal.

    ``named=False`` (founder) gates to >= k_anon_floor distinct contributors — the
    caller renders the ``deidentified()`` view. ``named=True`` (privacy_mode="open") keeps
    single-contributor topics with names, and must be audited by the caller. Clustering
    caps at DEFAULT_MAX_ITEMS (O(n^2)); the returned Coverage flags that truncation so a
    missing topic is never silently indistinguishable from "no topic" (no silent
    truncation).
    """
    comps = store.query_compactions(org_id=org_id, limit=100_000)
    floor = 1 if named else config.k_anon_floor
    tops = _build_topics(comps, embedder or default_embedder(), min_contributors=floor)
    return tops, Coverage(matched=len(comps), used=min(len(comps), DEFAULT_MAX_ITEMS))


def thread(store: ServerStore, org_id: str, session_id: str) -> list[Any]:
    """The arc of one transcript across its released slices (named view; a thread is
    one contributor, so it is correctly empty under k-anon)."""
    base = thread_key(session_id)
    comps = [
        c
        for c in store.query_compactions(org_id=org_id, limit=100_000)
        if thread_key(c.session_id) == base
    ]
    comps.sort(key=lambda c: c.started_at)
    return comps


__all__ = [
    "FounderFilter",
    "Rollup",
    "FounderResult",
    "parse_filter",
    "run_query",
    "team_topics",
    "thread",
    "INSUFFICIENT",
]
