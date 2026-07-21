"""Manthana shared schemas (Pydantic v2 + mirrored JSON Schema).

This package is the single source of truth for Manthana's data contracts. The
mirrored JSON Schema under ``schemas/json/`` is generated from these models via
``manthana-schemas-export`` and guarded by a CI test.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from .action import Action, ActionAuditEntry, ActionQueueItem
from .compaction import (
    BaseCompaction,
    Compaction,
    CompactionAdapter,
    EngineeringCompaction,
)
from .consent import ConsentEntry
from .enums import (
    ActionActor,
    ActionOutcome,
    ActionShape,
    CompactionKind,
    ConsentClass,
    ConsentState,
    FrictionCategory,
    Mode,
    NoteKind,
    NoteSource,
    NoteStatus,
    Outcome,
    QueueStatus,
    Role,
    SessionEndReason,
    Surface,
)
from .friction import FrictionPoint
from .invite import decode_invite, encode_invite
from .knowledge import (
    BODY_CHAR_CAP,
    OVERVIEW_BODY_CHAR_CAP,
    KnowledgeNote,
    NoteEntities,
    body_char_cap,
)
from .session import Session
from .turn import Turn

__all__ = [
    # entities
    "Turn",
    "Session",
    "FrictionPoint",
    "BaseCompaction",
    "EngineeringCompaction",
    "Compaction",
    "CompactionAdapter",
    "Action",
    "ActionAuditEntry",
    "ActionQueueItem",
    "ConsentEntry",
    "KnowledgeNote",
    "NoteEntities",
    "BODY_CHAR_CAP",
    "OVERVIEW_BODY_CHAR_CAP",
    "body_char_cap",
    # enums
    "Surface",
    "Role",
    "Mode",
    "Outcome",
    "FrictionCategory",
    "SessionEndReason",
    "CompactionKind",
    "ActionShape",
    "ActionActor",
    "ConsentClass",
    "ConsentState",
    "ActionOutcome",
    "QueueStatus",
    "NoteKind",
    "NoteStatus",
    "NoteSource",
    # onboarding
    "encode_invite",
    "decode_invite",
]
