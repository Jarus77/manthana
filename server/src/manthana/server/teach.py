"""Teaching the wiki — human writes that outrank the AI.

This is the mechanic behind "correct it once and it sticks for everyone". Every
function here produces a ``source="human"`` note version, and the consolidation
operator is forbidden from superseding one (``consolidate.apply_verdicts``
downgrades a refine against a human note to a dispute). So a founder's
correction is not a one-off fix to one page — it becomes the authority that
every later page render and every later Q&A answer reads first.

Four verbs, matching what a Wikipedia editor expects:

  * ``edit``    — rewrite a claim. Appends a new version; the old one survives.
  * ``create``  — add a claim the sessions never produced.
  * ``confirm`` — endorse an AI note as-is. NOT a new version (nothing changed
                  but the trust), so history stays meaningful.
  * ``revert``  — restore an earlier version's text. Also append-only: it writes
                  a NEW version rather than rewinding, so the mistake and its
                  correction both stay on the record.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from manthana.schemas import (
    BODY_CHAR_CAP,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
)

if TYPE_CHECKING:
    from .store import ServerStore

#: A human wrote it, so it starts trusted — this is what outranks AI notes in
#: retrieval and in the Q&A prompt.
HUMAN_CONFIDENCE = 0.95


class NoteNotFoundError(ValueError):
    """The note does not exist in this org (or was never visible to it)."""


def _note_id() -> str:
    return f"kn-{uuid.uuid4().hex[:12]}"


def _clip(body: str) -> str:
    if len(body) <= BODY_CHAR_CAP:
        return body
    return body[: BODY_CHAR_CAP - 12].rstrip() + " …[truncated]"


def _require(store: ServerStore, org_id: str, note_id: str) -> KnowledgeNote:
    note = store.get_note(note_id, org_id)
    if note is None:
        raise NoteNotFoundError(f"note {note_id} not found in org {org_id}")
    return note


def edit(
    store: ServerStore,
    org_id: str,
    note_id: str,
    *,
    title: str,
    body: str,
    author: str,
    now: datetime | None = None,
) -> KnowledgeNote:
    """Rewrite a claim as a human. Supersedes the old version (append-only).

    A human edit RESOLVES any dispute: the founder has seen the conflicting
    evidence and made a call, so the new version starts clean rather than
    inheriting a red badge it no longer deserves.
    """
    now = now or datetime.now(UTC)
    old = _require(store, org_id, note_id)
    new = old.model_copy(
        update={
            "id": _note_id(),
            "title": title.strip() or old.title,
            "body": _clip(body.strip()),
            "source": NoteSource.human,
            "author": author,
            "status": NoteStatus.established,
            "confidence": HUMAN_CONFIDENCE,
            "confirmed_by": author,
            "disputed_by": [],
            "superseded_by": None,
            "version": old.version + 1,
            "supersedes": old.id,
            "created_at": now,
            "updated_at": now,
            "last_confirmed_at": now,
        }
    )
    store.supersede_note(old.id, new, org_id)
    return new


def create(
    store: ServerStore,
    org_id: str,
    *,
    kind: NoteKind,
    title: str,
    body: str,
    author: str,
    project: str = "",
    actors: list[str] | None = None,
    now: datetime | None = None,
) -> KnowledgeNote:
    """Add a claim by hand — knowledge that was in someone's head, not in a
    session. Carries no evidence by design: its authority is the author's."""
    now = now or datetime.now(UTC)
    note = KnowledgeNote(
        id=_note_id(),
        org_id=org_id,
        kind=kind,
        title=title.strip(),
        body=_clip(body.strip()),
        scope=f"project:{project}" if project else "org",
        entities=NoteEntities(projects=[project] if project else []),
        actors=actors or [],
        source=NoteSource.human,
        author=author,
        status=NoteStatus.established,
        confidence=HUMAN_CONFIDENCE,
        confirmed_by=author,
        created_at=now,
        updated_at=now,
        last_confirmed_at=now,
    )
    store.upsert_note(note)
    return note


def confirm(
    store: ServerStore,
    org_id: str,
    note_id: str,
    *,
    author: str,
    now: datetime | None = None,
) -> KnowledgeNote:
    """Endorse an AI note as correct, without changing a word of it.

    Deliberately NOT a new version: the claim did not change, only its standing,
    and versioning an endorsement would fill the history with noise. ``source``
    stays ``ai`` (it records who wrote the text) while ``confirmed_by`` records
    who vouched — and that is what the Q&A prompt reads as authoritative.
    """
    now = now or datetime.now(UTC)
    note = _require(store, org_id, note_id)
    confirmed = note.model_copy(
        update={
            "status": NoteStatus.established,
            "confirmed_by": author,
            "last_confirmed_at": now,
            "updated_at": now,
        }
    )
    store.upsert_note(confirmed)
    return confirmed


def revert(
    store: ServerStore,
    org_id: str,
    note_id: str,
    *,
    to_version_id: str,
    author: str,
    now: datetime | None = None,
) -> KnowledgeNote:
    """Restore an earlier version's text as a NEW human version.

    Append-only on purpose: rewinding would erase the bad edit, and the point of
    auto-publish-revert-later is that the record shows what was published and
    what a human did about it. The restored text is human-authored from now on,
    so the AI that produced the reverted edit cannot simply redo it.
    """
    now = now or datetime.now(UTC)
    current = _require(store, org_id, note_id)
    chain = {n.id: n for n in store.note_history(note_id, org_id)}
    target = chain.get(to_version_id)
    if target is None:
        raise NoteNotFoundError(
            f"version {to_version_id} is not in the history of {note_id}"
        )
    new = current.model_copy(
        update={
            "id": _note_id(),
            "title": target.title,
            "body": target.body,
            "metric": target.metric,
            "value": target.value,
            "source": NoteSource.human,
            "author": author,
            "status": NoteStatus.established,
            "confidence": HUMAN_CONFIDENCE,
            "confirmed_by": author,
            "disputed_by": [],
            "superseded_by": None,
            "version": current.version + 1,
            "supersedes": current.id,
            "created_at": now,
            "updated_at": now,
            "last_confirmed_at": now,
        }
    )
    store.supersede_note(current.id, new, org_id)
    return new


__all__ = ["NoteNotFoundError", "HUMAN_CONFIDENCE", "confirm", "create", "edit", "revert"]
