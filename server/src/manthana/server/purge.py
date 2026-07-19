"""Purging compactions — narrowly, auditably, and dry-run first.

Motivation: agents used to compact by shelling out to ``claude -p``. That call
itself created a Claude Code transcript, which the watcher then captured and
compacted — recursively. On production, 276 of 300 sessions for the pilot
customer are Manthana summarizing itself. The recursion is fixed at the source
(agents no longer call a model), but the junk it already produced has to go.

**Identifying the junk.** These are Claude Code sessions whose content IS
Manthana's own compaction prompt, so their digests echo that prompt's own
wording. The markers below are contiguous phrases lifted from the prompt
template (``enrich/prompt.py``), matched against the digest's own text after
normalization. They are deliberately LONG and SPECIFIC rather than a broad
regex, because a false positive deletes a customer's real work.

  1. "you are manthana's compactor"  — the template's verbatim opening sentence.
     Present in a pending digest's ``task_intent`` (the crude first-user-turn
     fallback) and in ``native_summary`` whenever the summarized session was a
     compaction call.
  2. "summarize one engineering session" — the template's imperative, echoed
     back by the model in enriched junk digests. Observed on production as
     "Summarize one engineering session into a structured JSON digest (the
     Manthana co…".
  3. "session contains only the manthana compa" — the other observed production
     phrasing, where the model correctly reported that no engineering work
     happened. Cut short of "compaction prompt" deliberately: ``task_intent`` is
     truncated (the agent's fallback caps it at 200 chars, and the production
     sample truncates mid-word at "…the Manthana compa"), so a longer marker
     would silently miss the very rows it was written for.

**False-positive risk, stated plainly.** The realistic risk is an engineer
working ON Manthana itself: their real sessions legitimately discuss the
compactor. That is why the markers are full phrases and not the tokens
"manthana" + "compactor" — a real task_intent like "Fix the Manthana compactor
prompt" or "Debug why manthana's compaction is recursing" matches NONE of them,
because none of those is the prompt's own sentence. The residual risk is an
engineer who pastes the prompt template verbatim into a session (e.g. while
editing it) — that session's first user turn genuinely contains marker 1, and
would match. This is exactly why the endpoint is dry-run by default: the
operator reviews the sample before confirming. Markers 2 and 3 are safe against
this, since they are model paraphrases rather than the template text.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from manthana.schemas import BaseCompaction

if TYPE_CHECKING:
    from .storage import ObjectStore
    from .store import ServerStore

# Contiguous phrases from the compaction prompt template / its observed echoes.
# Each is specific enough that ordinary engineering text cannot produce it.
SELF_GENERATED_MARKERS: tuple[str, ...] = (
    "you are manthana's compactor",
    "summarize one engineering session",
    "session contains only the manthana compa",
)

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, fold curly apostrophes to straight, collapse whitespace.

    The apostrophe fold matters: the template writes ``Manthana's`` with U+0027,
    but a model paraphrase may come back with U+2019 and would otherwise miss.
    Whitespace collapsing survives the template's hard line wrapping.
    """
    return _WS.sub(" ", text.replace("’", "'").replace("ʼ", "'")).strip().lower()


def is_self_generated(compaction: BaseCompaction) -> bool:
    """True when this digest describes one of Manthana's OWN compaction calls.

    Checks the fields that carry session content — never the deterministic
    metadata, so a project literally named "manthana" is not at risk.
    """
    haystack = _normalize(
        " ".join(
            part
            for part in (
                compaction.task_intent,
                compaction.approach,
                compaction.native_summary or "",
            )
            if part
        )
    )
    return any(marker in haystack for marker in SELF_GENERATED_MARKERS)


@dataclass
class PurgeSelector:
    """What to purge. At least one criterion must be set — an unfiltered purge is
    refused, so a mis-typed request can never mean "delete this org's data"."""

    source: str | None = None  # "pending" | "full" | "claude_summary"
    contains: str | None = None  # case-insensitive substring of the digest's text
    self_generated: bool = False  # apply the marker predicate above

    def is_empty(self) -> bool:
        return self.source is None and not self.contains and not self.self_generated

    def matches(self, compaction: BaseCompaction) -> bool:
        # AND across the criteria that were actually supplied — narrowing, never
        # widening, so adding a filter can only ever delete less.
        if self.source is not None and compaction.source != self.source:
            return False
        if self.self_generated and not is_self_generated(compaction):
            return False
        if self.contains:
            haystack = _normalize(
                " ".join(
                    p
                    for p in (
                        compaction.task_intent,
                        compaction.approach,
                        compaction.native_summary or "",
                    )
                    if p
                )
            )
            if _normalize(self.contains) not in haystack:
                return False
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "contains": self.contains,
            "self_generated": self.self_generated,
        }


@dataclass
class PurgeReport:
    dry_run: bool
    matched: int = 0
    deleted: int = 0
    blobs_deleted: int = 0
    vectors_deleted: int = 0
    sample: list[dict[str, str]] = field(default_factory=list)
    audit_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "matched": self.matched,
            "deleted": self.deleted,
            "blobs_deleted": self.blobs_deleted,
            "vectors_deleted": self.vectors_deleted,
            "sample": self.sample,
            "audit_id": self.audit_id,
            "error": self.error,
        }


_SAMPLE_SIZE = 10


def _sample(compactions: list[BaseCompaction]) -> list[dict[str, str]]:
    return [
        {
            "id": c.id,
            "actor": c.actor,
            "project": c.project,
            "source": c.source,
            "started_at": c.started_at.isoformat(),
            "task_intent": c.task_intent[:160],
        }
        for c in compactions[:_SAMPLE_SIZE]
    ]


def select(store: ServerStore, org_id: str, selector: PurgeSelector) -> list[BaseCompaction]:
    """Every released compaction in the org matching the selector."""
    return [
        c
        for c in store.query_compactions(org_id=org_id, limit=1_000_000)
        if selector.matches(c)
    ]


def purge(
    store: ServerStore,
    object_store: ObjectStore,
    *,
    org_id: str,
    selector: PurgeSelector,
    confirm: bool = False,
    actor: str = "admin",
) -> PurgeReport:
    """Purge compactions and everything derived from them.

    DRY RUN BY DEFAULT: without ``confirm=True`` this only counts and samples
    what WOULD be deleted and writes nothing. Both outcomes are audited.

    Ordering is deliberate. Object-store blobs are deleted FIRST and, if any
    deletion fails, the DB transaction is abandoned entirely — so a failure
    leaves the rows intact and the purge can simply be re-run. Committing the DB
    first would strip the only pointers to those blobs and orphan them forever.
    Rows, raw-transcript records, and cached embedding vectors then go in ONE
    transaction, so the three can never diverge.
    """
    matched = select(store, org_id, selector)
    report = PurgeReport(dry_run=not confirm, matched=len(matched), sample=_sample(matched))

    if selector.is_empty():
        report.error = "refusing an unfiltered purge — set source, contains, or self_generated"
        report.matched = 0
        report.sample = []
        return report

    if not confirm or not matched:
        report.audit_id = store.record_purge_audit(
            org_id=org_id, dry_run=True, matched=len(matched), deleted=0,
            selector=selector.as_dict(), sample_ids=[c.id for c in matched[:_SAMPLE_SIZE]],
            actor=actor,
        )
        return report

    ids = [c.id for c in matched]
    keys = [k for k in (store.get_raw_key(cid, org_id) for cid in ids) if k]
    failed = [key for key in keys if not object_store.delete(key)]
    if failed:
        # Abort before touching the DB — rows survive, so a retry can finish the job.
        report.error = f"object-store delete failed for {len(failed)} blob(s); nothing deleted"
        report.audit_id = store.record_purge_audit(
            org_id=org_id, dry_run=False, matched=len(matched), deleted=0,
            selector=selector.as_dict(), sample_ids=ids[:_SAMPLE_SIZE], actor=actor,
            error=report.error,
        )
        return report

    deleted, vectors = store.delete_compactions(org_id, ids)
    report.deleted = deleted
    report.blobs_deleted = len(keys)
    report.vectors_deleted = vectors
    report.audit_id = store.record_purge_audit(
        org_id=org_id, dry_run=False, matched=len(matched), deleted=deleted,
        selector=selector.as_dict(), sample_ids=ids[:_SAMPLE_SIZE], actor=actor,
    )
    return report


__all__ = [
    "PurgeSelector",
    "PurgeReport",
    "SELF_GENERATED_MARKERS",
    "is_self_generated",
    "purge",
    "select",
]
