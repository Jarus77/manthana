"""Knowledge consolidation — enriched digests → typed org-wiki notes.

The wiki's editorial model is auto-publish-revert-later: each enriched compaction
gets ONE cheap adjudication call against the most relevant live notes, and the
verdicts are applied deterministically:

  * ``supports``    → evidence++, confidence bump; ``candidate → established``
                      once the claim recurs (≥3 evidence sessions or ≥2 actors).
  * ``contradicts`` → appended to ``disputed_by`` + a disputed badge. Never a
                      silent rewrite — the body stands until a human (or a
                      refine of an AI note) resolves it.
  * ``refines``     → a NEW version supersedes the old (append-only chain) —
                      but NEVER against a ``source="human"`` note: the one law
                      of the layer. A refine against a human note is downgraded
                      to ``contradicts``.
  * new notes       → published immediately as ``candidate`` (unreviewed badge),
                      capped and sanity-gated; a title-duplicate of a candidate
                      is treated as ``supports`` instead.

Structure mirrors ``enrich/enricher.py``: bounded per-org batches, per-org
``MeteredProvider`` (shares the monthly cap), quota defers the org cleanly, the
pass never raises, and ``apply_verdicts`` is a pure function so the whole write
path is unit-testable without a model.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from manthana.schemas import (
    BODY_CHAR_CAP,
    BaseCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
)
from manthana.skills.cluster import default_text_of
from manthana.skills.embed import Embedder, default_embedder
from manthana.skills.retrieval import rank_scored

from .enrich.coerce import extract_json, str_list
from .graph import cooccurrence_edges, entity_edges
from .metering import MeteredProvider, QuotaExceededError
from .vectors import ensure_note_vectors

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import ServerConfig
    from .llm import LLMProvider
    from .store import ServerStore

_log = logging.getLogger(__name__)

# Cosine floor for semantic candidates — below this a note is only ever pulled in
# by entity overlap. Total candidates per adjudication are capped regardless.
_MIN_COSINE = 0.25
#: Window for the persisted co-occurrence graph. Matches the wiki's own
#: connection window so the stored edges and the rendered "works with" agree.
_GRAPH_WINDOW_DAYS = 45
_MAX_CANDIDATES = 12
_MAX_NEW_NOTES = 3

#: Kinds the per-session adjudicator may create.
#:
#: ``project_overview`` is excluded BY CONSTRUCTION: it describes a whole
#: project and is written by a dedicated pass, so a single session could never
#: ground one. ``faq`` is excluded because nothing populates it yet.
#:
#: This is a real gate, not documentation. ``_new_note`` builds ``NoteKind``
#: straight from model output, so the moment a member exists a hallucinating
#: adjudicator can create it — the prompt alone has never been able to stop that.
#: Both the prompt and the parser read this tuple, so they cannot drift.
ADJUDICABLE_KINDS: tuple[NoteKind, ...] = (
    NoteKind.decision,
    NoteKind.convention,
    NoteKind.gotcha,
    NoteKind.failure_pattern,
    NoteKind.procedure_ref,
    NoteKind.benchmark,
)

# Confidence dynamics: supports bumps toward (not past) the ceiling.
_CONFIDENCE_BUMP = 0.1
_CONFIDENCE_CEILING = 0.95
# Promotion: a claim is "established" once it recurs.
_PROMOTE_EVIDENCE = 3
_PROMOTE_ACTORS = 2


@dataclass
class ConsolidateStats:
    """Outcome of one pass, per the states an operator cares about."""

    consolidated: int = 0  # compactions fully adjudicated + applied
    new_notes: int = 0
    supported: int = 0
    disputed: int = 0
    refined: int = 0
    failed: int = 0  # call/parse failed — will retry (bounded)
    abandoned: int = 0  # attempts exhausted — will NOT retry
    quota_blocked: int = 0
    orgs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "consolidated": self.consolidated,
            "new_notes": self.new_notes,
            "supported": self.supported,
            "disputed": self.disputed,
            "refined": self.refined,
            "failed": self.failed,
            "abandoned": self.abandoned,
            "quota_blocked": self.quota_blocked,
            "orgs": self.orgs,
        }


# ── candidate retrieval ──────────────────────────────────────────────────
def _entity_overlap(note: KnowledgeNote, compaction: BaseCompaction) -> bool:
    if compaction.project and compaction.project in note.entities.projects:
        return True
    files = set(getattr(compaction, "files_touched", []) or [])
    return bool(files and files & set(note.entities.files))


def retrieve_candidates(
    store: ServerStore,
    config: ServerConfig,
    compaction: BaseCompaction,
    *,
    org_id: str,
    embedder: Embedder,
) -> list[KnowledgeNote]:
    """Top-k live notes by cosine ∪ entity-overlap hits, capped. Retrieval
    failure degrades to entity-overlap only — a broken embedder must not stall
    consolidation."""
    notes = store.query_notes(org_id, limit=config.consolidate_note_scan)
    if not notes:
        return []
    semantic: list[KnowledgeNote] = []
    try:
        vectors = ensure_note_vectors(store, org_id, notes, embedder)
        ranked, _cov = rank_scored(
            default_text_of(compaction), notes, vectors, embedder, k=config.consolidate_top_k
        )
        semantic = [n for score, n in ranked if score >= _MIN_COSINE]  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - degrade, don't stall the pass
        _log.exception("consolidation retrieval: embedder failed; entity overlap only")
    have = {n.id for n in semantic}
    overlap = [n for n in notes if n.id not in have and _entity_overlap(n, compaction)]
    return (semantic + overlap)[:_MAX_CANDIDATES]


# ── adjudication prompt ──────────────────────────────────────────────────
def build_adjudication_prompt(
    compaction: BaseCompaction, candidates: list[KnowledgeNote]
) -> str:
    friction = "; ".join(
        f"{fp.category}: {fp.description}" for fp in compaction.friction_points
    )
    files = ", ".join(getattr(compaction, "files_touched", [])[:20])
    lines = [
        "You maintain a startup's engineering knowledge base. Given ONE finished",
        "coding session digest and the existing notes most related to it, decide",
        "how the session bears on each note, and whether it contains any NEW",
        "durable knowledge worth a note of its own.",
        "",
        "SESSION DIGEST:",
        f"  actor: {compaction.actor}",
        f"  project: {compaction.project}",
        f"  intent: {compaction.task_intent}",
        f"  approach: {compaction.approach}",
        f"  outcome: {compaction.outcome}",
        f"  friction: {friction or '(none)'}",
        f"  files: {files or '(none)'}",
        f"  artifacts: {', '.join(compaction.artifacts) or '(none)'}",
        "",
        "EXISTING NOTES:",
    ]
    if candidates:
        for n in candidates:
            lines.append(
                f"  [{n.id}] kind={n.kind} status={n.status} source={n.source}\n"
                f"    title: {n.title}\n    body: {n.body}"
            )
    else:
        lines.append("  (none yet)")
    lines += [
        "",
        "Return ONLY a JSON object:",
        '{"verdicts": [{"note_id": "<id>", "relation":',
        '  "supports|contradicts|refines|unrelated", "updated_body": "<only for refines>",',
        '  "value": "<only for refines of a benchmark whose number moved>"}],',
        # Built from ADJUDICABLE_KINDS so the prompt and the parser's allowlist
        # cannot drift apart.
        ' "new_notes": [{"kind": "' + "|".join(str(k) for k in ADJUDICABLE_KINDS) + '",',
        '  "title": "...", "body": "...", "files": [], "libraries": [], "concepts": [],',
        '  "metric": null, "value": null}]}',
        "",
        "Rules:",
        "- A verdict for every existing note; 'unrelated' when the session says nothing about it.",
        "- 'refines' means the session materially improves/updates the note; give the full",
        "  rewritten body in updated_body (<300 tokens, markdown).",
        "- New notes ONLY for durable, non-obvious facts: decisions with their rationale,",
        "  conventions, gotchas, failure patterns, benchmark results (fill metric/value).",
        "- NEVER write activity/status notes ('X is working on Y') — that is computed live.",
        f"- At most {_MAX_NEW_NOTES} new notes; bodies under 300 tokens; no duplicates of",
        "  existing notes (use a 'supports' verdict instead).",
    ]
    return "\n".join(lines)


# ── deterministic apply (pure — the unit-testable core) ──────────────────
@dataclass
class ApplyPlan:
    """The writes one adjudication produces. ``upserts`` are in-place updates
    (supports/contradicts); ``supersedes`` are (old_id, new_version) pairs."""

    upserts: list[KnowledgeNote] = field(default_factory=list)
    supersedes: list[tuple[str, KnowledgeNote]] = field(default_factory=list)
    #: Typed relationships this adjudication established. Previously the
    #: relation was read, used to pick a mutation, and dropped — so the wiki
    #: paid a model call to label edges and kept none of them.
    edges: list[dict[str, Any]] = field(default_factory=list)
    new_notes: int = 0
    supported: int = 0
    disputed: int = 0
    refined: int = 0


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _edge(
    src_type: str, src_id: str, relation: str, dst_type: str, dst_id: str, *, weight: float = 1.0
) -> dict[str, Any]:
    """One edge record. ``evidence_id`` is the session that established it, which
    is what lets a reader check the claim the edge makes."""
    return {
        "src_type": src_type,
        "src_id": src_id,
        "relation": relation,
        "dst_type": dst_type,
        "dst_id": dst_id,
        "weight": weight,
        "evidence_id": src_id if src_type == "session" else "",
    }


def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _clip_body(body: str) -> str:
    if len(body) <= BODY_CHAR_CAP:
        return body
    return body[: BODY_CHAR_CAP - 12].rstrip() + " …[truncated]"


def _promote(note: KnowledgeNote) -> NoteStatus:
    if note.status != NoteStatus.candidate:
        return note.status
    if len(note.evidence) >= _PROMOTE_EVIDENCE or len(set(note.actors)) >= _PROMOTE_ACTORS:
        return NoteStatus.established
    return note.status


def _support(note: KnowledgeNote, compaction: BaseCompaction, now: datetime) -> KnowledgeNote:
    updated = note.model_copy(
        update={
            "evidence": _dedup([*note.evidence, compaction.id]),
            "actors": _dedup([*note.actors, compaction.actor]),
            "confidence": min(_CONFIDENCE_CEILING, note.confidence + _CONFIDENCE_BUMP),
            "last_confirmed_at": now,
            "updated_at": now,
        }
    )
    return updated.model_copy(update={"status": _promote(updated)})


def _contradict(note: KnowledgeNote, compaction: BaseCompaction, now: datetime) -> KnowledgeNote:
    # The body is untouched — for human notes it stays canonical; the dispute is
    # a badge plus the conflicting evidence list.
    return note.model_copy(
        update={
            "disputed_by": _dedup([*note.disputed_by, compaction.id]),
            "status": NoteStatus.disputed,
            "updated_at": now,
        }
    )


def _refine(
    note: KnowledgeNote,
    compaction: BaseCompaction,
    body: str,
    now: datetime,
    *,
    value: str | None = None,
) -> KnowledgeNote:
    """The successor version of an AI note. A fresh claim: disputes reset, and it
    re-earns ``established`` unless the old version already had it.

    A refine may also move the structured ``value`` (a benchmark that improved).
    That is what lets the home feed show "61% → 64%": the delta is read off the
    supersedes chain, so the number must live on the version, not just in prose.
    """
    status = (
        NoteStatus.established if note.status == NoteStatus.established else NoteStatus.candidate
    )
    return note.model_copy(
        update={
            "id": _note_id(),
            "body": _clip_body(body),
            **({"value": value} if value else {}),
            "evidence": _dedup([*note.evidence, compaction.id]),
            "actors": _dedup([*note.actors, compaction.actor]),
            "status": status,
            "disputed_by": [],
            "superseded_by": None,
            "confirmed_by": None,
            "version": note.version + 1,
            "supersedes": note.id,
            "created_at": now,
            "updated_at": now,
        }
    )


def _note_id() -> str:
    return f"kn-{uuid.uuid4().hex[:12]}"


def _new_note(
    item: dict[str, Any], compaction: BaseCompaction, org_id: str, now: datetime
) -> KnowledgeNote | None:
    try:
        kind = NoteKind(str(item.get("kind", "")))
    except ValueError:
        return None
    if kind not in ADJUDICABLE_KINDS:
        return None  # the prompt is not a gate; this is
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    if not title or not body:
        return None
    project = compaction.project or ""
    return KnowledgeNote(
        id=_note_id(),
        org_id=org_id,
        kind=kind,
        title=title,
        body=_clip_body(body),
        scope=f"project:{project}" if project else "org",
        entities=NoteEntities(
            files=str_list(item.get("files")),
            libraries=str_list(item.get("libraries")),
            projects=[project] if project else [],
            concepts=str_list(item.get("concepts")),
        ),
        evidence=[compaction.id],
        actors=[compaction.actor],
        source=NoteSource.ai,
        status=NoteStatus.candidate,
        metric=(str(item["metric"]) if item.get("metric") else None),
        value=(str(item["value"]) if item.get("value") else None),
        created_at=now,
        updated_at=now,
    )


def apply_verdicts(
    compaction: BaseCompaction,
    candidates: list[KnowledgeNote],
    data: dict[str, Any],
    *,
    org_id: str,
    now: datetime,
) -> ApplyPlan:
    """Model output → a deterministic write plan. Unknown note ids and malformed
    verdicts are dropped (conservative, like founder citation matching); the one
    law — never supersede a human note — is enforced HERE, not in the prompt."""
    plan = ApplyPlan()
    by_id = {n.id: n for n in candidates}
    handled: set[str] = set()

    verdicts = data.get("verdicts")
    for v in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(v, dict):
            continue
        note = by_id.get(str(v.get("note_id", "")))
        if note is None or note.id in handled:
            continue
        relation = str(v.get("relation", "")).strip().lower()
        if relation == "refines" and note.source == NoteSource.human:
            relation = "contradicts"  # the one law of the layer
        if relation == "refines" and not str(v.get("updated_body") or "").strip():
            relation = "supports"  # a refine with no new body is just agreement
        if relation == "supports":
            plan.upserts.append(_support(note, compaction, now))
            plan.supported += 1
            plan.edges.append(_edge("session", compaction.id, "supports", "note", note.id))
        elif relation == "contradicts":
            plan.upserts.append(_contradict(note, compaction, now))
            plan.disputed += 1
            plan.edges.append(_edge("session", compaction.id, "contradicts", "note", note.id))
        elif relation == "refines":
            plan.supersedes.append(
                (
                    note.id,
                    _refine(
                        note, compaction, str(v["updated_body"]), now,
                        value=str(v["value"]) if v.get("value") else None,
                    ),
                )
            )
            plan.refined += 1
            plan.edges.append(_edge("session", compaction.id, "refines", "note", note.id))
        else:
            # Keep the NEGATIVE edge. It is the cheapest signal to store and the
            # most expensive to recompute — a later pass that knows these two
            # were already judged unrelated need not ask a model again.
            plan.edges.append(
                _edge("session", compaction.id, "unrelated", "note", note.id, weight=0.0)
            )
            continue
        handled.add(note.id)

    # The candidate set is itself a graph and was being discarded. Notes
    # retrieved together for one digest are semantically adjacent (cosine ≥
    # _MIN_COSINE or entity overlap) — recording that adjacency is free here and
    # costs a full re-retrieval to recover later.
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            plan.edges.append(_edge("note", a.id, "co_adjudicated", "note", b.id, weight=0.5))
            plan.edges[-1]["evidence_id"] = compaction.id

    titles = {n.title.casefold(): n for n in candidates}
    new_notes = data.get("new_notes")
    for item in (new_notes if isinstance(new_notes, list) else [])[:_MAX_NEW_NOTES]:
        if not isinstance(item, dict):
            continue
        dup = titles.get(str(item.get("title") or "").strip().casefold())
        if dup is not None:
            # A title-duplicate of an existing candidate is agreement, not news.
            if dup.id not in handled:
                plan.upserts.append(_support(dup, compaction, now))
                plan.supported += 1
                handled.add(dup.id)
            continue
        note = _new_note(item, compaction, org_id, now)
        if note is not None:
            plan.upserts.append(note)
            plan.new_notes += 1
            titles[note.title.casefold()] = note
    return plan


# ── the pass (mirrors enrich_org / run_enrichment_pass) ──────────────────
def consolidate_org(
    store: ServerStore,
    provider: LLMProvider,
    config: ServerConfig,
    *,
    org_id: str,
    limit: int,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> ConsolidateStats:
    """Consolidate up to ``limit`` enriched digests for one org. Never raises."""
    now = now or datetime.now(UTC)
    stats = ConsolidateStats(orgs=[org_id])
    embedder = embedder or default_embedder()
    meta = store.consolidation_meta(org_id)

    for compaction in store.list_unconsolidated(org_id, limit=limit):
        state = meta.get(compaction.id)
        if state is not None and state.attempts >= max(1, config.consolidate_max_attempts):
            store.mark_consolidation_abandoned(
                org_id, compaction.id, detail=f"gave up after {state.attempts} attempts"
            )
            stats.abandoned += 1
            continue

        candidates = retrieve_candidates(
            store, config, compaction, org_id=org_id, embedder=embedder
        )
        prompt = build_adjudication_prompt(compaction, candidates)
        try:
            raw = provider.complete(prompt)
        except QuotaExceededError:
            # Monthly budget spent: defer the whole org, not an attempt against
            # this digest — nothing was tried on it.
            _log.info("consolidation: org %s over its monthly LLM cap; deferring", org_id)
            stats.quota_blocked += 1
            return stats
        except Exception as exc:  # noqa: BLE001 - provider failure must not kill the batch
            store.record_consolidation_failure(
                org_id, compaction.id, detail=f"{type(exc).__name__}: {exc}"
            )
            stats.failed += 1
            continue

        data = extract_json(raw)
        if not data:
            store.record_consolidation_failure(
                org_id, compaction.id, detail="model returned no JSON object"
            )
            stats.failed += 1
            continue

        plan = apply_verdicts(compaction, candidates, data, org_id=org_id, now=now)
        for note in plan.upserts:
            store.upsert_note(note)
        for old_id, new_version in plan.supersedes:
            store.supersede_note(old_id, new_version, org_id)
        # Phase 2: every note this pass touched gets `mentions` edges to the
        # files/libraries/concepts it names — the first reader those entity
        # lists have ever had.
        for note in [*plan.upserts, *(nv for _old, nv in plan.supersedes)]:
            plan.edges.extend(entity_edges(note))
        store.add_edges(org_id, plan.edges)
        store.mark_consolidated(org_id, compaction.id)
        stats.consolidated += 1
        stats.new_notes += plan.new_notes
        stats.supported += plan.supported
        stats.disputed += plan.disputed
        stats.refined += plan.refined

    # Phase 3: refresh the persisted co-occurrence graph once per pass, not per
    # digest — it is a whole-org computation and re-running it inside the loop
    # would be quadratic for no gain. Built by calling the SAME functions the
    # pages render from, so the stored graph cannot drift from what a reader
    # sees. Best-effort: a graph refresh must never fail a consolidation pass
    # that has already written real notes.
    if stats.consolidated:
        try:
            base = now or datetime.now(UTC)
            since = _iso(base - timedelta(days=_GRAPH_WINDOW_DAYS))
            comps = store.query_compactions(org_id=org_id, since=since)
            live = store.query_notes(org_id, exclude_superseded=True)
            store.add_edges(org_id, cooccurrence_edges(comps, live))
        except Exception:  # noqa: BLE001 - never fail the pass on a graph refresh
            _log.exception("consolidate: co-occurrence graph refresh failed for %s", org_id)

    return stats


def run_consolidation_pass(
    store: ServerStore,
    config: ServerConfig,
    provider_for: Callable[[str], LLMProvider],
    *,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> ConsolidateStats:
    """One batched pass across every org with enriched-but-unconsolidated
    digests. Bounded per org and whole-pass, exactly like enrichment."""
    total = ConsolidateStats()
    budget = max(1, config.consolidate_max_batch)
    per_org = max(1, config.consolidate_batch_per_org)
    embedder = embedder or default_embedder()

    for org_id in store.orgs_with_unconsolidated():
        if budget <= 0:
            break
        stats = consolidate_org(
            store,
            provider_for(org_id),
            config,
            org_id=org_id,
            limit=min(per_org, budget),
            embedder=embedder,
            now=now,
        )
        total.consolidated += stats.consolidated
        total.new_notes += stats.new_notes
        total.supported += stats.supported
        total.disputed += stats.disputed
        total.refined += stats.refined
        total.failed += stats.failed
        total.abandoned += stats.abandoned
        total.quota_blocked += stats.quota_blocked
        total.orgs.append(org_id)
        budget -= stats.consolidated + stats.failed
    return total


def consolidate_provider_for(
    store: ServerStore, config: ServerConfig, inner: LLMProvider
) -> Callable[[str], LLMProvider]:
    """Per-org metered view of the consolidation provider — shares the org's
    monthly cap with enrichment and the founder pipeline."""

    def _for(org_id: str) -> LLMProvider:
        cap = store.get_org_quota(org_id)
        if cap is None:
            cap = config.llm_monthly_cap_usd
        return MeteredProvider(inner, store, org_id, cap, purpose="consolidate")

    return _for


__all__ = [
    "ApplyPlan",
    "ConsolidateStats",
    "apply_verdicts",
    "build_adjudication_prompt",
    "consolidate_org",
    "consolidate_provider_for",
    "retrieve_candidates",
    "run_consolidation_pass",
]
