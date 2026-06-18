"""ServerStore — multi-tenant persistence for the org server.

Same document-store pattern as the local store (typed index columns +
authoritative ``data`` JSON; UTC-normalized timestamps for correct ordering).
Tenancy: every row is scoped to an org (and team); the founder query is always
org-scoped.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from manthana.schemas import BaseCompaction, CompactionAdapter
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from .db import create_db_engine, init_db
from .tables import (
    ActorRow,
    OrgConsentRow,
    OrgRow,
    RawTranscriptRow,
    ReleasedCompactionRow,
    TeamRow,
)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ServerStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def open(cls, db_url: str) -> ServerStore:
        engine = create_db_engine(db_url)
        init_db(engine)
        return cls(engine)

    # ── tenancy ──────────────────────────────────────────────────────────
    def create_org(self, org_id: str, name: str) -> None:
        with DBSession(self._engine) as db:
            db.merge(OrgRow(id=org_id, name=name, created_at=_now_iso()))
            db.commit()

    def create_team(self, team_id: str, org_id: str, name: str) -> None:
        with DBSession(self._engine) as db:
            db.merge(TeamRow(id=team_id, org_id=org_id, name=name))
            db.commit()

    def upsert_actor(
        self, actor_id: str, org_id: str, team_id: str, display_name: str | None = None
    ) -> None:
        with DBSession(self._engine) as db:
            db.merge(
                ActorRow(
                    id=actor_id, org_id=org_id, team_id=team_id, display_name=display_name
                )
            )
            db.commit()

    def get_org(self, org_id: str) -> OrgRow | None:
        with DBSession(self._engine) as db:
            return db.get(OrgRow, org_id)

    # ── ingestion ────────────────────────────────────────────────────────
    def ingest_compaction(
        self, compaction: BaseCompaction, *, org_id: str, team_id: str
    ) -> None:
        self.upsert_actor(compaction.actor, org_id, team_id)
        with DBSession(self._engine) as db:
            db.merge(
                ReleasedCompactionRow(
                    id=compaction.id,
                    org_id=org_id,
                    team_id=team_id,
                    actor=compaction.actor,
                    project=compaction.project,
                    surface=str(compaction.surface),
                    outcome=str(compaction.outcome),
                    started_at=_utc_iso(compaction.started_at),
                    kind=compaction.kind,
                    tier_used=compaction.tier_used,
                    est_cost_usd=compaction.est_cost_usd,
                    data=compaction.model_dump(mode="json"),
                )
            )
            db.commit()

    def get_compaction(self, compaction_id: str) -> BaseCompaction | None:
        with DBSession(self._engine) as db:
            row = db.get(ReleasedCompactionRow, compaction_id)
            return CompactionAdapter.validate_python(row.data) if row else None

    def query_compactions(
        self,
        *,
        org_id: str,
        team_id: str | None = None,
        project: str | None = None,
        outcome: str | None = None,
        actor: str | None = None,
        surface: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[BaseCompaction]:
        with DBSession(self._engine) as db:
            stmt = select(ReleasedCompactionRow).where(ReleasedCompactionRow.org_id == org_id)
            if team_id is not None:
                stmt = stmt.where(ReleasedCompactionRow.team_id == team_id)
            if project is not None:
                stmt = stmt.where(ReleasedCompactionRow.project == project)
            if outcome is not None:
                stmt = stmt.where(ReleasedCompactionRow.outcome == outcome)
            if actor is not None:
                stmt = stmt.where(ReleasedCompactionRow.actor == actor)
            if surface is not None:
                stmt = stmt.where(ReleasedCompactionRow.surface == surface)
            if since is not None:
                stmt = stmt.where(col(ReleasedCompactionRow.started_at) >= since)
            if until is not None:
                stmt = stmt.where(col(ReleasedCompactionRow.started_at) <= until)
            stmt = stmt.order_by(ReleasedCompactionRow.started_at.desc())  # type: ignore[attr-defined]
            if limit is not None:
                stmt = stmt.limit(limit)
            return [CompactionAdapter.validate_python(row.data) for row in db.exec(stmt)]

    # ── raw transcript release ───────────────────────────────────────────
    def record_raw(self, compaction_id: str, org_id: str, object_key: str) -> None:
        with DBSession(self._engine) as db:
            db.merge(
                RawTranscriptRow(
                    id=f"raw-{compaction_id}",
                    compaction_id=compaction_id,
                    org_id=org_id,
                    object_key=object_key,
                    uploaded_at=_now_iso(),
                )
            )
            db.commit()

    # ── consent registry (seam) ──────────────────────────────────────────
    def set_consent(
        self, *, org_id: str, subject: str, action_category: str, state: str
    ) -> None:
        with DBSession(self._engine) as db:
            db.merge(
                OrgConsentRow(
                    id=f"{org_id}:{subject}:{action_category}",
                    org_id=org_id,
                    subject=subject,
                    action_category=action_category,
                    state=state,
                    data={"state": state},
                )
            )
            db.commit()


__all__ = ["ServerStore"]
