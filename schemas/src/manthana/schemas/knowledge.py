"""KnowledgeNote — the atomic unit of the org wiki.

A note is a small, typed, evidence-cited claim (decision, convention, gotcha,
failure pattern, procedure reference, benchmark result). Wiki pages are
PROJECTIONS over notes + live compaction rollups, never documents of their own —
so revision history, citations, and editorial control all fall out of the note
model itself:

  * **Versioning**: a version IS a note (append-only). ``supersedes`` /
    ``superseded_by`` chain versions together; nothing is ever deleted.
  * **Citations**: ``evidence`` holds the compaction ids the claim is grounded
    in; ``actors`` is derived from those compactions (powers Person pages).
  * **Editorial control** (the one law of the layer): a ``source="human"`` note
    has top authority — the AI consolidator may *dispute* it with new evidence
    (``disputed_by`` + status badge) but may never supersede or rewrite it.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import NoteKind, NoteSource, NoteStatus

#: Soft cap on note bodies (~300 tokens). Enforced at apply time by truncation
#: with a marker, not by validation — a long body from an LLM should degrade,
#: not crash the pass.
BODY_CHAR_CAP = 1600
#: A project_overview holds a whole living article (What this is / Current
#: state / Open questions), so it gets more room — but still a hard ceiling,
#: because "Current state" is REWRITTEN each update, never appended, and the
#: changelog lives in the version chain rather than the body. The article is
#: O(1) by construction; this cap is the backstop, not the mechanism.
OVERVIEW_BODY_CHAR_CAP = 4000


def body_char_cap(kind: NoteKind | str) -> int:
    """Body cap for a note kind. Everything except project_overview stays at
    the tight cap — a longer cap for ordinary notes would bloat every
    consolidated note and the ask-retrieval context with it."""
    return OVERVIEW_BODY_CHAR_CAP if str(kind) == "project_overview" else BODY_CHAR_CAP


class NoteEntities(BaseModel):
    """What a note is *about* — drives page matching and candidate retrieval."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list, description="Project slugs")
    concepts: list[str] = Field(default_factory=list, description="Free concepts")


class KnowledgeNote(BaseModel):
    """One durable, cited claim in the org knowledge base."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable note id (kn-<hex12>)")
    org_id: str
    kind: NoteKind
    title: str
    body: str = Field(..., description="Markdown, target <300 tokens")
    scope: str = Field(default="org", description='"org" | "project:<slug>"')
    entities: NoteEntities = Field(default_factory=NoteEntities)
    links: list[str] = Field(
        default_factory=list, description="Related note ids (schema seam; unpopulated in v1)"
    )
    evidence: list[str] = Field(
        default_factory=list, description="Compaction ids grounding this claim"
    )
    actors: list[str] = Field(
        default_factory=list,
        description="Derived from evidence compactions' actors — powers Person pages",
    )

    source: NoteSource = NoteSource.ai
    author: str | None = Field(
        default=None, description='Actor id / "founder" / "admin" for human notes'
    )
    confidence: float = 0.5
    status: NoteStatus = NoteStatus.candidate
    confirmed_by: str | None = Field(
        default=None, description="Human endorsement badge without changing source"
    )
    disputed_by: list[str] = Field(
        default_factory=list, description="Compaction ids of contradicting evidence"
    )

    version: int = 1
    #: One line describing what changed since the previous version. Written by
    #: the overview pass on each refresh (project_overview only today); the
    #: project page's changelog is the version chain's change_summary lines —
    #: an append-only log that costs the body nothing.
    change_summary: str | None = None
    supersedes: str | None = Field(default=None, description="Previous version's note id")
    superseded_by: str | None = None

    # benchmark-kind optional structured fields (best-effort extraction; the
    # feed shows the note text alone when these are unparseable).
    metric: str | None = None
    value: str | None = None

    created_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime | None = None


__all__ = [
    "BODY_CHAR_CAP",
    "OVERVIEW_BODY_CHAR_CAP",
    "KnowledgeNote",
    "NoteEntities",
    "body_char_cap",
]
