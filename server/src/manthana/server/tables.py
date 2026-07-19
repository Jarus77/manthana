"""Server SQLModel tables (multi-tenant: Org > Team > Actor; Project is a tag).

Distinct ``__tablename__``s (``released_compaction`` etc.) avoid any clash with
the local-agent tables on the shared SQLModel metadata. Same document-store
pattern as the local store: typed index columns + an authoritative ``data`` JSON.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class OrgRow(SQLModel, table=True):
    __tablename__ = "org"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    name: str
    created_at: str


class TeamRow(SQLModel, table=True):
    __tablename__ = "team"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    name: str


class ActorRow(SQLModel, table=True):
    __tablename__ = "actor"  # type: ignore[assignment]
    id: str = Field(primary_key=True)  # org email
    org_id: str = Field(index=True)
    team_id: str = Field(index=True)
    display_name: str | None = Field(default=None)


class ReleasedCompactionRow(SQLModel, table=True):
    __tablename__ = "released_compaction"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    team_id: str = Field(index=True)
    actor: str = Field(index=True)
    project: str = Field(index=True)
    surface: str = Field(index=True)
    outcome: str = Field(index=True)
    started_at: str = Field(index=True)  # UTC ISO-8601
    kind: str = Field(index=True)
    released: bool = Field(default=False, index=True)
    tier_used: str | None = Field(default=None)
    est_cost_usd: float | None = Field(default=None)
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))


class RawTranscriptRow(SQLModel, table=True):
    __tablename__ = "raw_transcript"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    compaction_id: str = Field(index=True)
    org_id: str = Field(index=True)
    object_key: str
    uploaded_at: str


class ActionQueueRow(SQLModel, table=True):
    """Pending org action awaiting human approval (seam; empty in v1)."""

    __tablename__ = "action_queue"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    action_id: str = Field(index=True)
    org_id: str = Field(index=True)
    team_id: str | None = Field(default=None, index=True)
    status: str = Field(index=True)
    created_at: str
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))


class OrgConsentRow(SQLModel, table=True):
    """Org/admin-level consent registry (seam)."""

    __tablename__ = "org_consent"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    subject: str = Field(index=True)
    action_category: str = Field(index=True)
    state: str = Field(index=True)
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))


class ReleasedCompactionVectorRow(SQLModel, table=True):
    """Cached embedding for a released compaction (semantic retrieval). Org-scoped;
    derived/regenerable. Only released compactions are ever embedded — the index can
    never contain unreleased/personal content."""

    __tablename__ = "released_compaction_vector"  # type: ignore[assignment]
    id: str = Field(primary_key=True)  # org-namespaced: org::compaction_id
    org_id: str = Field(index=True)
    compaction_id: str = Field(index=True)
    dim: int
    text_hash: str = Field(index=True)
    vec: list[float] = Field(sa_column=Column(JSON, nullable=False))


class InviteRow(SQLModel, table=True):
    """Onboarding invite: an engineer redeems ``code`` at ``POST /v1/enroll`` for a team
    token — so the token itself never travels over Slack. ``actor`` bound = per-engineer
    (single-use); ``actor`` null = open team invite (the engineer supplies their email).
    ``uses_left`` bounds redemptions; ``expires_at`` bounds time."""

    __tablename__ = "invite"  # type: ignore[assignment]
    code: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    team_id: str = Field(index=True)
    actor: str | None = Field(default=None)  # bound identity, or None for an open invite
    uses_left: int = Field(default=1)
    expires_at: str  # UTC ISO-8601
    created_at: str
    redeemed_at: str | None = Field(default=None)


class FounderQueryAuditRow(SQLModel, table=True):
    """Audit trail of founder queries — who looked at what, and whether the
    answer was grounded/k-anon-met. Governance + after-the-fact investigation."""

    __tablename__ = "founder_query_audit"  # type: ignore[assignment]
    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    query: str  # the NL question (truncated)
    insufficient: bool = Field(index=True)  # withheld (k-anon/grounding) vs answered
    citation_count: int
    created_at: str = Field(index=True)
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))  # cited ids, etc.


class LlmUsageRow(SQLModel, table=True):
    """Month-to-date server-side LLM usage per org (hosted quota accounting).

    One row per (org, month); counters are incremented atomically in SQL.
    New TABLE (not new columns on ``org``) so ``create_all`` upgrades existing DBs.
    """

    __tablename__ = "llm_usage"  # type: ignore[assignment]
    id: str = Field(primary_key=True)  # org-namespaced: org::YYYY-MM
    org_id: str = Field(index=True)
    month: str = Field(index=True)  # UTC YYYY-MM
    calls: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    est_cost_usd: float = Field(default=0.0)


class OrgPrivacyRow(SQLModel, table=True):
    """Per-org privacy posture. ``open`` = consenting org: the founder sees named,
    per-individual results (founder==manager). ``k_anon`` = the original contract:
    de-identified, floor-gated aggregates. New TABLE so ``create_all`` upgrades
    existing DBs; absent row → the server default."""

    __tablename__ = "org_privacy"  # type: ignore[assignment]
    org_id: str = Field(primary_key=True)
    mode: str = Field(default="k_anon")


class OrgQuotaRow(SQLModel, table=True):
    """Per-org monthly LLM budget override (None/absent → server default cap)."""

    __tablename__ = "org_quota"  # type: ignore[assignment]
    org_id: str = Field(primary_key=True)
    monthly_cap_usd: float | None = Field(default=None)


SERVER_TABLES = [
    OrgRow,
    TeamRow,
    ActorRow,
    ReleasedCompactionRow,
    RawTranscriptRow,
    ActionQueueRow,
    OrgConsentRow,
    FounderQueryAuditRow,
    ReleasedCompactionVectorRow,
    InviteRow,
    LlmUsageRow,
    OrgQuotaRow,
    OrgPrivacyRow,
]

__all__ = [
    "OrgRow",
    "TeamRow",
    "ActorRow",
    "ReleasedCompactionRow",
    "RawTranscriptRow",
    "ActionQueueRow",
    "OrgConsentRow",
    "FounderQueryAuditRow",
    "ReleasedCompactionVectorRow",
    "InviteRow",
    "LlmUsageRow",
    "OrgQuotaRow",
    "OrgPrivacyRow",
    "SERVER_TABLES",
]
