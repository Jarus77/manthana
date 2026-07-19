"""ServerStore — multi-tenant persistence for the org server.

Same document-store pattern as the local store (typed index columns +
authoritative ``data`` JSON; UTC-normalized timestamps for correct ordering).

Tenant isolation (defense-in-depth, post-review):
  * Stored primary keys are **org-namespaced** (``org::id``) so a compaction id
    from one org can never collide with / overwrite another org's row.
  * Reads are **org-scoped** (and ``get_owned_*`` also team-scoped).
  * The server is **fail-closed on release**: only ``released=True`` compactions
    are stored as released and only released rows are ever returned.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from manthana.schemas import BaseCompaction, CompactionAdapter
from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from .db import create_db_engine, init_db
from .tables import (
    ActionQueueRow,
    ActorRow,
    EnrichmentStateRow,
    FounderQueryAuditRow,
    InviteRow,
    LlmUsageRow,
    OrgConsentRow,
    OrgPrivacyRow,
    OrgQuotaRow,
    OrgRow,
    PurgeAuditRow,
    RawTranscriptRow,
    ReleasedCompactionRow,
    ReleasedCompactionVectorRow,
    TeamRow,
)


class NotReleasedError(ValueError):
    """Raised when an unreleased compaction is offered to the server."""


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    """Parse a stored ISO timestamp to a UTC-aware datetime (naive → assumed UTC)."""
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _pk(org_id: str, compaction_id: str) -> str:
    """Org-namespaced primary key (prevents cross-tenant id collisions)."""
    return f"{org_id}::{compaction_id}"


def _normalize_since(since: str | None) -> str | None:
    if since is None:
        return None
    if "T" not in since and len(since) == 10:
        return f"{since}T00:00:00+00:00"
    return since


def _until_bound(until: str | None) -> tuple[str, str] | None:
    """Return (operator, value) for the upper bound: half-open '<' for a
    date-only bound (so the whole boundary day is included), inclusive '<='
    for a full timestamp."""
    if until is None:
        return None
    if "T" not in until and len(until) == 10:
        try:
            nxt = date.fromisoformat(until) + timedelta(days=1)
            return ("<", f"{nxt.isoformat()}T00:00:00+00:00")
        except ValueError:
            return ("<=", until)
    return ("<=", until)


class ServerStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def open(cls, db_url: str) -> ServerStore:
        engine = create_db_engine(db_url)
        init_db(engine)
        return cls(engine)

    def ping(self) -> bool:
        """Lightweight DB connectivity check for the /readyz probe."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - any DB error means not-ready
            return False

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
                ActorRow(id=actor_id, org_id=org_id, team_id=team_id, display_name=display_name)
            )
            db.commit()

    def get_org(self, org_id: str) -> OrgRow | None:
        with DBSession(self._engine) as db:
            return db.get(OrgRow, org_id)

    def list_orgs(self) -> list[OrgRow]:
        with DBSession(self._engine) as db:
            return list(db.exec(select(OrgRow)))

    def list_teams(self, org_id: str) -> list[TeamRow]:
        with DBSession(self._engine) as db:
            return list(db.exec(select(TeamRow).where(TeamRow.org_id == org_id)))

    def list_actors(self, org_id: str) -> list[ActorRow]:
        with DBSession(self._engine) as db:
            return list(db.exec(select(ActorRow).where(ActorRow.org_id == org_id)))

    # ── onboarding invites ───────────────────────────────────────────────
    def create_invite(
        self,
        code: str,
        *,
        org_id: str,
        team_id: str,
        actor: str | None = None,
        uses: int = 1,
        expires_at: datetime,
    ) -> None:
        """Store an onboarding invite (redeemed at POST /v1/enroll for a team token)."""
        with DBSession(self._engine) as db:
            db.merge(
                InviteRow(
                    code=code, org_id=org_id, team_id=team_id, actor=actor,
                    uses_left=uses, expires_at=_utc_iso(expires_at), created_at=_now_iso(),
                )
            )
            db.commit()

    def get_invite(self, code: str) -> InviteRow | None:
        with DBSession(self._engine) as db:
            return db.get(InviteRow, code)

    def redeem_invite(self, code: str, *, now: datetime | None = None) -> InviteRow | None:
        """Atomically consume one use of a valid invite; return the (pre-decrement) row, or
        None if the code is unknown / expired / exhausted. Decrements ``uses_left`` and stamps
        ``redeemed_at`` when it hits zero — so a single-use invite can't be replayed."""
        now = now or datetime.now(UTC)
        with DBSession(self._engine) as db:
            row = db.get(InviteRow, code)
            if row is None or row.uses_left <= 0:
                return None
            if _parse_iso(row.expires_at) <= now:
                return None
            row.uses_left -= 1
            if row.uses_left == 0:
                row.redeemed_at = _utc_iso(now)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def list_invites(self, org_id: str) -> list[InviteRow]:
        with DBSession(self._engine) as db:
            return list(db.exec(select(InviteRow).where(InviteRow.org_id == org_id)))

    def list_projects(self, org_id: str) -> list[str]:
        """Distinct project slugs that have at least one released compaction in this org.
        Used by the founder pipeline to resolve a free-text project name to a real slug."""
        with DBSession(self._engine) as db:
            stmt = (
                select(ReleasedCompactionRow.project)
                .where(ReleasedCompactionRow.org_id == org_id)
                .where(ReleasedCompactionRow.released == True)  # noqa: E712 - SQL boolean column
                .distinct()
            )
            return sorted({p for p in db.exec(stmt) if p})

    # ── compaction vectors (semantic retrieval cache; released-only) ──────
    def vector_meta(self, org_id: str) -> dict[str, tuple[int, str]]:
        with DBSession(self._engine) as db:
            stmt = select(ReleasedCompactionVectorRow).where(
                ReleasedCompactionVectorRow.org_id == org_id
            )
            return {r.compaction_id: (r.dim, r.text_hash) for r in db.exec(stmt)}

    def upsert_vector(
        self, org_id: str, compaction_id: str, *, dim: int, text_hash: str, vec: list[float]
    ) -> None:
        with DBSession(self._engine) as db:
            db.merge(
                ReleasedCompactionVectorRow(
                    id=_pk(org_id, compaction_id),
                    org_id=org_id,
                    compaction_id=compaction_id,
                    dim=dim,
                    text_hash=text_hash,
                    vec=vec,
                )
            )
            db.commit()

    def get_vectors(self, org_id: str, ids: list[str], *, dim: int) -> dict[str, list[float]]:
        """Cached vectors for the given org's compaction ids at the active dim. Filters
        in SQL (org + id IN + dim), chunked under SQLite's 999-variable limit."""
        wanted = list(dict.fromkeys(ids))
        out: dict[str, list[float]] = {}
        with DBSession(self._engine) as db:
            for i in range(0, len(wanted), 900):
                chunk = wanted[i : i + 900]
                stmt = (
                    select(ReleasedCompactionVectorRow)
                    .where(ReleasedCompactionVectorRow.org_id == org_id)
                    .where(col(ReleasedCompactionVectorRow.compaction_id).in_(chunk))
                    .where(ReleasedCompactionVectorRow.dim == dim)
                )
                for r in db.exec(stmt):
                    out[r.compaction_id] = r.vec
        return out

    def count_compactions(self, org_id: str) -> int:
        with DBSession(self._engine) as db:
            rows = db.exec(
                select(ReleasedCompactionRow.id)
                .where(ReleasedCompactionRow.org_id == org_id)
                .where(ReleasedCompactionRow.released == True)  # noqa: E712 - SQL boolean column
            )
            return len(list(rows))

    # ── ingestion (fail-closed on release; org-namespaced PK) ─────────────
    def ingest_compaction(
        self, compaction: BaseCompaction, *, org_id: str, team_id: str
    ) -> None:
        if not compaction.released:
            raise NotReleasedError(f"compaction {compaction.id} is not released")
        self.upsert_actor(compaction.actor, org_id, team_id)
        with DBSession(self._engine) as db:
            db.merge(
                ReleasedCompactionRow(
                    id=_pk(org_id, compaction.id),
                    org_id=org_id,
                    team_id=team_id,
                    actor=compaction.actor,
                    project=compaction.project,
                    surface=str(compaction.surface),
                    outcome=str(compaction.outcome),
                    started_at=_utc_iso(compaction.started_at),
                    kind=compaction.kind,
                    released=True,
                    tier_used=compaction.tier_used,
                    est_cost_usd=compaction.est_cost_usd,
                    data=compaction.model_dump(mode="json"),
                )
            )
            db.commit()

    def get_compaction(self, compaction_id: str, org_id: str) -> BaseCompaction | None:
        """Org-scoped fetch of a released compaction."""
        with DBSession(self._engine) as db:
            row = db.get(ReleasedCompactionRow, _pk(org_id, compaction_id))
            if row is None or not row.released:
                return None
            return CompactionAdapter.validate_python(row.data)

    def get_owned_compaction(
        self, compaction_id: str, org_id: str, team_id: str
    ) -> BaseCompaction | None:
        """Fetch a released compaction only if it belongs to this org AND team."""
        with DBSession(self._engine) as db:
            row = db.get(ReleasedCompactionRow, _pk(org_id, compaction_id))
            if row is None or not row.released or row.team_id != team_id:
                return None
            return CompactionAdapter.validate_python(row.data)

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
            stmt = (
                select(ReleasedCompactionRow)
                .where(ReleasedCompactionRow.org_id == org_id)
                .where(ReleasedCompactionRow.released == True)  # noqa: E712 - SQL boolean column
            )
            if team_id is not None:
                stmt = stmt.where(ReleasedCompactionRow.team_id == team_id)
            # project / outcome / surface are matched case-INSENSITIVELY: the founder
            # NL parser emits human casing ("ASR", "BIRD", "Success") while the stored
            # slug is lower/enum-cased, so an exact `==` silently returns nothing — which
            # reads as "no data" rather than a filter miss. actor stays exact (it's a
            # resolved id on the named path; the k-anon path suppresses per-person).
            if project is not None:
                stmt = stmt.where(func.lower(ReleasedCompactionRow.project) == project.lower())
            if outcome is not None:
                stmt = stmt.where(func.lower(ReleasedCompactionRow.outcome) == outcome.lower())
            if actor is not None:
                stmt = stmt.where(ReleasedCompactionRow.actor == actor)
            if surface is not None:
                stmt = stmt.where(func.lower(ReleasedCompactionRow.surface) == surface.lower())
            since_norm = _normalize_since(since)
            if since_norm is not None:
                stmt = stmt.where(col(ReleasedCompactionRow.started_at) >= since_norm)
            bound = _until_bound(until)
            if bound is not None:
                op, value = bound
                column = col(ReleasedCompactionRow.started_at)
                stmt = stmt.where(column < value if op == "<" else column <= value)
            stmt = stmt.order_by(ReleasedCompactionRow.started_at.desc())  # type: ignore[attr-defined]
            if limit is not None:
                stmt = stmt.limit(limit)
            return [CompactionAdapter.validate_python(row.data) for row in db.exec(stmt)]

    # ── raw transcript release (org-namespaced; ownership enforced by caller) ─
    def get_raw_key(self, compaction_id: str, org_id: str) -> str | None:
        """Object-store key for a compaction's released raw transcript (org-scoped)."""
        with DBSession(self._engine) as db:
            row = db.exec(
                select(RawTranscriptRow)
                .where(RawTranscriptRow.compaction_id == compaction_id)
                .where(RawTranscriptRow.org_id == org_id)
            ).first()
            return row.object_key if row else None

    def record_raw(self, compaction_id: str, org_id: str, object_key: str) -> None:
        with DBSession(self._engine) as db:
            db.merge(
                RawTranscriptRow(
                    id=f"raw::{org_id}::{compaction_id}",
                    compaction_id=compaction_id,
                    org_id=org_id,
                    object_key=object_key,
                    uploaded_at=_now_iso(),
                )
            )
            db.commit()

    # ── server-side enrichment (pending digests → qualitative fields) ─────
    # ``source`` lives in the authoritative ``data`` JSON, not an index column —
    # adding a column would not reach existing DBs (``create_all`` only creates
    # whole tables), so the pending filter runs in Python over a BOUNDED scan of
    # the most recent rows. That cap is what keeps one org's backlog from turning
    # every pass into a full-table read.
    _ENRICH_SCAN_CAP = 2000

    def list_pending_for_enrichment(
        self, org_id: str, *, limit: int = 25, skip_ids: set[str] | None = None
    ) -> list[BaseCompaction]:
        """Released digests still awaiting enrichment (``source == "pending"``),
        newest first, excluding ``skip_ids`` (typically the aged-out/abandoned set).
        """
        skip = skip_ids or set()
        out: list[BaseCompaction] = []
        with DBSession(self._engine) as db:
            stmt = (
                select(ReleasedCompactionRow)
                .where(ReleasedCompactionRow.org_id == org_id)
                .where(ReleasedCompactionRow.released == True)  # noqa: E712 - SQL boolean column
                .order_by(ReleasedCompactionRow.started_at.desc())  # type: ignore[attr-defined]
                .limit(self._ENRICH_SCAN_CAP)
            )
            for row in db.exec(stmt):
                if row.data.get("source") != "pending":
                    continue
                compaction = CompactionAdapter.validate_python(row.data)
                if compaction.id in skip:
                    continue
                out.append(compaction)
                if len(out) >= limit:
                    break
        return out

    def count_pending_for_enrichment(self, org_id: str) -> int:
        """How many released digests in this org are still ``source="pending"``
        (bounded by the same scan cap). Powers the admin enrichment view."""
        with DBSession(self._engine) as db:
            stmt = (
                select(ReleasedCompactionRow)
                .where(ReleasedCompactionRow.org_id == org_id)
                .where(ReleasedCompactionRow.released == True)  # noqa: E712 - SQL boolean column
                .order_by(ReleasedCompactionRow.started_at.desc())  # type: ignore[attr-defined]
                .limit(self._ENRICH_SCAN_CAP)
            )
            return sum(1 for row in db.exec(stmt) if row.data.get("source") == "pending")

    def orgs_with_pending(self) -> list[str]:
        """Orgs that have at least one pending digest. The batch pass iterates
        these so a quiet org costs nothing."""
        return sorted(
            org.id for org in self.list_orgs() if self.count_pending_for_enrichment(org.id) > 0
        )

    def save_enriched(self, compaction: BaseCompaction, *, org_id: str) -> bool:
        """Persist an enriched digest over its existing row.

        Deliberately NOT ``ingest_compaction``: the row's tenancy (team_id) and
        released state are preserved from what is already stored — enrichment
        must never re-home a compaction or change who owns it. Returns False when
        the row vanished (purged mid-pass), so the caller writes nothing.
        """
        with DBSession(self._engine) as db:
            row = db.get(ReleasedCompactionRow, _pk(org_id, compaction.id))
            if row is None or not row.released:
                return False
            # Refresh only the index columns that enrichment can legitimately move
            # (outcome), plus the authoritative payload. actor/project/team/started_at
            # are deterministic and stay exactly as ingested.
            row.outcome = str(compaction.outcome)
            row.data = compaction.model_dump(mode="json")
            db.add(row)
            db.commit()
        return True

    def get_enrichment_state(self, org_id: str, compaction_id: str) -> EnrichmentStateRow | None:
        with DBSession(self._engine) as db:
            return db.get(EnrichmentStateRow, _pk(org_id, compaction_id))

    def record_enrichment_attempt(
        self, org_id: str, compaction_id: str, *, state: str, detail: str = ""
    ) -> int:
        """Bump the attempt counter for a digest that could NOT be enriched and
        record why. Returns the new attempt count so the caller can age it out."""
        now = _now_iso()
        with DBSession(self._engine) as db:
            row = db.get(EnrichmentStateRow, _pk(org_id, compaction_id))
            if row is None:
                row = EnrichmentStateRow(
                    id=_pk(org_id, compaction_id),
                    org_id=org_id,
                    compaction_id=compaction_id,
                    attempts=0,
                    first_seen_at=now,
                    updated_at=now,
                )
            row.attempts += 1
            row.state = state
            row.detail = detail[:500]
            row.updated_at = now
            db.add(row)
            db.commit()
            return row.attempts

    def mark_enrichment_abandoned(self, org_id: str, compaction_id: str, *, detail: str) -> None:
        """Terminal state: never picked up again. Set when attempts or age are
        exhausted (raw never arrived / permanently failed)."""
        now = _now_iso()
        with DBSession(self._engine) as db:
            row = db.get(EnrichmentStateRow, _pk(org_id, compaction_id))
            if row is None:
                row = EnrichmentStateRow(
                    id=_pk(org_id, compaction_id),
                    org_id=org_id,
                    compaction_id=compaction_id,
                    attempts=1,
                    first_seen_at=now,
                    updated_at=now,
                )
            row.state = "abandoned"
            row.detail = detail[:500]
            row.updated_at = now
            db.add(row)
            db.commit()

    def clear_enrichment_state(self, org_id: str, compaction_id: str) -> None:
        """Drop the bookkeeping row once the digest enriched successfully — the
        digest's own ``source`` is the record from then on."""
        with DBSession(self._engine) as db:
            row = db.get(EnrichmentStateRow, _pk(org_id, compaction_id))
            if row is not None:
                db.delete(row)
                db.commit()

    def list_enrichment_state(
        self, org_id: str, *, state: str | None = None, limit: int = 200
    ) -> list[EnrichmentStateRow]:
        with DBSession(self._engine) as db:
            stmt = select(EnrichmentStateRow).where(EnrichmentStateRow.org_id == org_id)
            if state is not None:
                stmt = stmt.where(EnrichmentStateRow.state == state)
            stmt = stmt.order_by(EnrichmentStateRow.updated_at.desc()).limit(limit)  # type: ignore[attr-defined]
            return list(db.exec(stmt))

    def abandoned_enrichment_ids(self, org_id: str) -> set[str]:
        """Compaction ids the pass must stop retrying."""
        return {r.compaction_id for r in self.list_enrichment_state(org_id, state="abandoned")}

    # ── purge (admin-only; see purge.py for the selection predicate) ──────
    def delete_compactions(self, org_id: str, ids: list[str]) -> tuple[int, int]:
        """Delete compactions and everything DERIVED from them, in ONE transaction.

        Three tables move together — the released compaction row, its
        raw-transcript record, and its cached embedding vector — because leaving
        any one behind orphans it: a stale vector keeps the digest answering
        semantic search after it was purged, and a stale raw record points the
        founder drill path at a blob that no longer exists.

        Object-store blobs are NOT deleted here; the caller removes those first
        and abandons this call if any blob deletion fails (see ``purge.purge``).

        Returns ``(compactions_deleted, vectors_deleted)``.
        """
        removed = 0
        vectors = 0
        with DBSession(self._engine) as db:
            for compaction_id in ids:
                pk = _pk(org_id, compaction_id)
                row = db.get(ReleasedCompactionRow, pk)
                if row is not None:
                    db.delete(row)
                    removed += 1
                vec = db.get(ReleasedCompactionVectorRow, pk)
                if vec is not None:
                    db.delete(vec)
                    vectors += 1
                raw = db.exec(
                    select(RawTranscriptRow)
                    .where(RawTranscriptRow.compaction_id == compaction_id)
                    .where(RawTranscriptRow.org_id == org_id)
                ).first()
                if raw is not None:
                    db.delete(raw)
                # Enrichment bookkeeping is derived too — drop it so a purged id
                # can't resurface as a permanently "waiting" ghost.
                state = db.get(EnrichmentStateRow, pk)
                if state is not None:
                    db.delete(state)
            db.commit()
        return removed, vectors

    def record_purge_audit(
        self,
        *,
        org_id: str,
        dry_run: bool,
        matched: int,
        deleted: int,
        selector: dict[str, Any],
        sample_ids: list[str],
        actor: str = "admin",
        error: str | None = None,
    ) -> str:
        """Append an audit row for a purge. Dry runs are audited too — knowing
        someone probed for deletable rows is itself of governance interest, and it
        ties a later confirmed delete back to the preview it was based on."""
        audit_id = f"pg-{uuid.uuid4().hex[:12]}"
        with DBSession(self._engine) as db:
            db.merge(
                PurgeAuditRow(
                    id=audit_id,
                    org_id=org_id,
                    dry_run=dry_run,
                    matched=matched,
                    deleted=deleted,
                    created_at=_now_iso(),
                    data={
                        "selector": selector,
                        "sample_ids": sample_ids,
                        "actor": actor,
                        "error": error,
                    },
                )
            )
            db.commit()
        return audit_id

    def list_purge_audit(self, org_id: str, *, limit: int = 100) -> list[PurgeAuditRow]:
        with DBSession(self._engine) as db:
            stmt = (
                select(PurgeAuditRow)
                .where(PurgeAuditRow.org_id == org_id)
                .order_by(PurgeAuditRow.created_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
            return list(db.exec(stmt))

    # ── action queue (seam) ──────────────────────────────────────────────
    def enqueue_action(
        self,
        *,
        action_id: str,
        org_id: str,
        payload: dict[str, Any],
        team_id: str | None = None,
    ) -> str:
        """Enqueue a pending org action (e.g. an auto-drafted skill) for approval."""
        queue_id = f"queue-{uuid.uuid4().hex[:12]}"
        with DBSession(self._engine) as db:
            db.merge(
                ActionQueueRow(
                    id=queue_id,
                    action_id=action_id,
                    org_id=org_id,
                    team_id=team_id,
                    status="pending",
                    created_at=_now_iso(),
                    data=payload,
                )
            )
            db.commit()
        return queue_id

    def list_queue(self, org_id: str, *, status: str = "pending") -> list[ActionQueueRow]:
        with DBSession(self._engine) as db:
            stmt = (
                select(ActionQueueRow)
                .where(ActionQueueRow.org_id == org_id)
                .where(ActionQueueRow.status == status)
            )
            return list(db.exec(stmt))

    # ── founder-query audit ──────────────────────────────────────────────
    def record_founder_query(
        self,
        *,
        org_id: str,
        query: str,
        insufficient: bool,
        citations: list[str],
        individual: bool = False,
    ) -> str:
        """Append an audit row for a founder query (governance / transparency).

        ``individual=True`` marks a query that could name a person —
        the accountability record for the privacy escalation."""
        audit_id = f"fq-{uuid.uuid4().hex[:12]}"
        with DBSession(self._engine) as db:
            db.merge(
                FounderQueryAuditRow(
                    id=audit_id,
                    org_id=org_id,
                    query=query[:500],
                    insufficient=insufficient,
                    citation_count=len(citations),
                    created_at=_now_iso(),
                    data={"citations": citations, "individual": individual},
                )
            )
            db.commit()
        return audit_id

    def list_founder_audit(self, org_id: str, *, limit: int = 100) -> list[FounderQueryAuditRow]:
        with DBSession(self._engine) as db:
            stmt = (
                select(FounderQueryAuditRow)
                .where(FounderQueryAuditRow.org_id == org_id)
                .order_by(FounderQueryAuditRow.created_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
            return list(db.exec(stmt))

    # ── LLM usage metering + org quotas (hosted multi-tenant) ────────────
    def add_llm_usage(
        self,
        org_id: str,
        month: str,
        *,
        input_tokens: int,
        output_tokens: int,
        est_cost_usd: float,
    ) -> None:
        """Atomically accumulate one LLM call into the org's monthly bucket.

        SQL-side increments (not read-modify-write): handlers run concurrently
        in FastAPI's threadpool, and lost updates would under-count spend.
        """
        row_id = f"{org_id}::{month}"
        with DBSession(self._engine) as db:
            updated = db.execute(
                text(
                    "UPDATE llm_usage SET calls = calls + 1, "
                    "input_tokens = input_tokens + :i, "
                    "output_tokens = output_tokens + :o, "
                    "est_cost_usd = est_cost_usd + :c WHERE id = :id"
                ),
                {"i": input_tokens, "o": output_tokens, "c": est_cost_usd, "id": row_id},
            )
            if getattr(updated, "rowcount", 0) == 0:
                try:
                    db.add(
                        LlmUsageRow(
                            id=row_id, org_id=org_id, month=month, calls=1,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            est_cost_usd=est_cost_usd,
                        )
                    )
                    db.commit()
                    return
                except Exception:  # noqa: BLE001 - insert race: another thread created the row
                    db.rollback()
                    db.execute(
                        text(
                            "UPDATE llm_usage SET calls = calls + 1, "
                            "input_tokens = input_tokens + :i, "
                            "output_tokens = output_tokens + :o, "
                            "est_cost_usd = est_cost_usd + :c WHERE id = :id"
                        ),
                        {
                            "i": input_tokens, "o": output_tokens,
                            "c": est_cost_usd, "id": row_id,
                        },
                    )
            db.commit()

    def get_llm_usage(self, org_id: str, month: str) -> LlmUsageRow:
        """The org's usage bucket for a month (a zeroed row when none exists yet)."""
        with DBSession(self._engine) as db:
            row = db.get(LlmUsageRow, f"{org_id}::{month}")
            return row or LlmUsageRow(
                id=f"{org_id}::{month}", org_id=org_id, month=month
            )

    def list_llm_usage(self, org_id: str, *, limit: int = 12) -> list[LlmUsageRow]:
        with DBSession(self._engine) as db:
            stmt = (
                select(LlmUsageRow)
                .where(LlmUsageRow.org_id == org_id)
                .order_by(LlmUsageRow.month.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
            return list(db.exec(stmt))

    def set_org_quota(self, org_id: str, monthly_cap_usd: float | None) -> None:
        with DBSession(self._engine) as db:
            db.merge(OrgQuotaRow(org_id=org_id, monthly_cap_usd=monthly_cap_usd))
            db.commit()

    def set_org_privacy(self, org_id: str, mode: str) -> None:
        with DBSession(self._engine) as db:
            db.merge(OrgPrivacyRow(org_id=org_id, mode=mode))
            db.commit()

    def get_org_privacy(self, org_id: str) -> str | None:
        """The org's privacy mode override ('open' | 'k_anon'), or None for default."""
        with DBSession(self._engine) as db:
            row = db.get(OrgPrivacyRow, org_id)
            return row.mode if row else None

    def get_org_quota(self, org_id: str) -> float | None:
        """The org's cap override, or None when the server default applies."""
        with DBSession(self._engine) as db:
            row = db.get(OrgQuotaRow, org_id)
            return row.monthly_cap_usd if row else None

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


__all__ = ["ServerStore", "NotReleasedError"]
