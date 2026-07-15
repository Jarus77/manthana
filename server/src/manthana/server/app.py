"""FastAPI application: admin bootstrap, ingestion, raw release, founder query.

Auth: agent endpoints require a team-scoped JWT (Authorization: Bearer …); admin
and founder endpoints require the configured admin token (X-Admin-Token). Sync
endpoints run in FastAPI's threadpool over the sync ServerStore (the decisions
doc's async note is satisfied at the FastAPI layer; the DB layer mirrors the
local store for testability — can move to asyncpg later).

NOTE: this module intentionally does NOT use ``from __future__ import
annotations`` — FastAPI must resolve the ``Depends``/``Header`` dependencies in
the route annotations at runtime, which stringized annotations would break for
the closure-scoped dependency functions. Inline ``Annotated[...]`` keeps it
pyright-clean and avoids ruff B008 (no function call in a default value).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import hmac
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from manthana.schemas import CompactionAdapter
from manthana.skills import mine_org
from pydantic import BaseModel, ValidationError

from .analyzer import analyze_counterfactual_costs
from .auth import (
    AuthError,
    TeamClaims,
    issue_founder_token,
    issue_team_token,
    verify_founder_token,
    verify_team_token,
)
from .config import ServerConfig
from .digest import build_weekly_digest
from .founder import run_query, team_topics, thread
from .hardening import install_hardening
from .llm import LLMProvider, make_provider
from .metering import MeteredProvider, QuotaExceededError, month_key
from .storage import ObjectStore, make_object_store
from .store import ServerStore
from .ui import mount_ui


class CreateOrg(BaseModel):
    org_id: str
    name: str


class CreateTeam(BaseModel):
    team_id: str
    org_id: str
    name: str


class MintToken(BaseModel):
    org_id: str
    team_id: str
    actor: str


class CreateInvite(BaseModel):
    org_id: str
    team_id: str
    actor: str | None = None  # bound identity, or None for an open team invite
    expires_minutes: int = 20_160  # 14 days
    uses: int = 1


class RedeemInvite(BaseModel):
    code: str
    actor: str | None = None  # required only for an open (unbound) invite


class IngestBody(BaseModel):
    compactions: list[dict[str, Any]]


class RawBody(BaseModel):
    content: str


class FounderQueryBody(BaseModel):
    org_id: str
    query: str
    source: str | None = None  # None=all (default), "full", or "claude_summary"


class MineSkillsBody(BaseModel):
    org_id: str


class SetQuotaBody(BaseModel):
    monthly_cap_usd: float | None = None  # None clears the override → server default


class FounderTokenBody(BaseModel):
    org_id: str
    expires_days: int = 365


class ManagerThreadBody(BaseModel):
    org_id: str
    session_id: str


class ManagerDrillBody(BaseModel):
    org_id: str
    compaction_id: str


def _ct_eq(a: str, b: str) -> bool:
    """Constant-time string compare that won't crash on non-ASCII input.

    ``hmac.compare_digest`` raises TypeError when given a str with non-ASCII chars; a
    bad token must yield 401, never a 500. Comparing the UTF-8 bytes is safe + still
    constant-time over equal-length inputs.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def create_app(
    config: ServerConfig,
    store: ServerStore,
    object_store: ObjectStore,
    provider: LLMProvider,
) -> FastAPI:
    app = FastAPI(title="Manthana Server")
    install_hardening(app, config)

    def org_provider(org_id: str) -> LLMProvider:
        # Per-org metered view of the shared provider: the org's cap override, or
        # the server default. Cheap per-request construction; enforcement raises
        # QuotaExceededError → the 429 handler below.
        cap = store.get_org_quota(org_id)
        if cap is None:
            cap = config.llm_monthly_cap_usd
        return MeteredProvider(provider, store, org_id, cap)

    @app.exception_handler(QuotaExceededError)
    def quota_exceeded(_request: Request, exc: QuotaExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    def require_admin(x_admin_token: Annotated[str, Header()] = "") -> None:
        # constant-time comparison — admin token gates org/team/token mint + founder query
        if not _ct_eq(x_admin_token, config.admin_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

    def require_manager(x_manager_token: Annotated[str, Header()] = "") -> None:
        # The manager view (per-individual, k-anon-bypassing) is a privilege above
        # the founder console. Disabled unless a manager_token is configured; then
        # gated by constant-time comparison.
        if not config.manager_token or not _ct_eq(x_manager_token, config.manager_token):
            raise HTTPException(status_code=401, detail="invalid or disabled manager token")

    def require_founder_access(
        x_admin_token: Annotated[str, Header()] = "",
        authorization: Annotated[str, Header()] = "",
    ) -> Callable[[str], None]:
        """Admin token (any org) OR an org-scoped founder bearer token.

        Returns a checker the handler calls with the REQUESTED org id — a founder
        token for another org gets 403, so a hosted startup's founder can never
        read a different tenant. (Agent tokens are rejected here: their scope is
        'agent', and verify_founder_token requires scope 'founder'.)
        """
        if x_admin_token and _ct_eq(x_admin_token, config.admin_token):
            def allow_any(_org_id: str) -> None:
                return None

            return allow_any
        if authorization.startswith("Bearer "):
            try:
                claims = verify_founder_token(
                    config.jwt_secret, authorization.removeprefix("Bearer ")
                )
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

            def check(org_id: str) -> None:
                if org_id != claims.org_id:
                    raise HTTPException(
                        status_code=403, detail="founder token is not valid for this org"
                    )

            return check
        raise HTTPException(
            status_code=401, detail="admin token or founder bearer token required"
        )

    def require_team(authorization: Annotated[str, Header()] = "") -> TeamClaims:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            return verify_team_token(config.jwt_secret, authorization.removeprefix("Bearer "))
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        # Readiness (vs healthz liveness): DB reachable? Used by the compose/k8s probe.
        if store.ping():
            return {"status": "ready"}
        response.status_code = 503
        return {"status": "not ready"}

    @app.post("/v1/admin/orgs")
    def create_org(body: CreateOrg, _: Annotated[None, Depends(require_admin)]) -> dict[str, str]:
        store.create_org(body.org_id, body.name)
        return {"created": body.org_id}

    @app.post("/v1/admin/teams")
    def create_team(body: CreateTeam, _: Annotated[None, Depends(require_admin)]) -> dict[str, str]:
        store.create_team(body.team_id, body.org_id, body.name)
        return {"created": body.team_id}

    @app.post("/v1/admin/tokens")
    def mint_token(body: MintToken, _: Annotated[None, Depends(require_admin)]) -> dict[str, str]:
        token = issue_team_token(
            config.jwt_secret, org_id=body.org_id, team_id=body.team_id, actor=body.actor
        )
        return {"token": token}

    @app.post("/v1/admin/invites")
    def create_invite(
        body: CreateInvite, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Mint an onboarding invite the engineer redeems for a team token (so the token
        never travels in Slack). ``actor`` bound = single-engineer; ``actor`` null = open
        team invite (engineer supplies their email at redemption)."""
        code = secrets.token_urlsafe(8)
        expires = datetime.now(UTC) + timedelta(minutes=max(1, body.expires_minutes))
        store.create_invite(
            code, org_id=body.org_id, team_id=body.team_id, actor=body.actor,
            uses=max(1, body.uses), expires_at=expires,
        )
        return {"code": code, "expires_at": expires.isoformat(), "actor": body.actor}

    @app.post("/v1/enroll")
    def enroll(body: RedeemInvite) -> dict[str, str]:
        """Redeem an invite for a team token. UNAUTHENTICATED by design — the code IS the
        credential (validity + single-use bound). The engineer's `manthana setup` calls this."""
        invite = store.get_invite(body.code)
        if invite is None:
            raise HTTPException(status_code=400, detail="unknown invite code")
        actor = invite.actor or body.actor
        if not actor:
            raise HTTPException(status_code=400, detail="this invite needs your identity (actor)")
        # Atomic validity + single-use consume (guards expiry/exhaustion + races).
        if store.redeem_invite(body.code) is None:
            raise HTTPException(status_code=400, detail="invite expired or already used")
        token = issue_team_token(
            config.jwt_secret, org_id=invite.org_id, team_id=invite.team_id, actor=actor
        )
        return {"token": token, "actor": actor}

    @app.post("/v1/compactions")
    def ingest(
        body: IngestBody, claims: Annotated[TeamClaims, Depends(require_team)]
    ) -> dict[str, int]:
        # Validate (and require released) the WHOLE batch before persisting any,
        # so a bad item never leaves a partial commit.
        compactions = []
        for raw in body.compactions:
            try:
                compaction = CompactionAdapter.validate_python(raw)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"invalid compaction: {exc}") from exc
            if not compaction.released:
                raise HTTPException(
                    status_code=422, detail=f"compaction {compaction.id} is not released"
                )
            # The contributor identity is the AUTHENTICATED token, never the (untrusted)
            # payload — otherwise one engineer could submit compactions under several
            # forged actors and fake their way past the k-anonymity floor. Binding to
            # claims.actor makes the count reflect real distinct people.
            compaction.actor = claims.actor
            compactions.append(compaction)
        for compaction in compactions:
            store.ingest_compaction(compaction, org_id=claims.org_id, team_id=claims.team_id)
        return {"ingested": len(compactions)}

    @app.post("/v1/compactions/{compaction_id}/raw")
    def upload_raw(
        compaction_id: str, body: RawBody, claims: Annotated[TeamClaims, Depends(require_team)]
    ) -> dict[str, str]:
        # Tenant-scoped + released-only lookup; 404 (not 403) so cross-tenant
        # existence is not disclosed.
        if store.get_owned_compaction(compaction_id, claims.org_id, claims.team_id) is None:
            raise HTTPException(status_code=404, detail="unknown compaction")
        encoded = body.content.encode("utf-8")
        if len(encoded) > config.max_raw_bytes:
            raise HTTPException(status_code=413, detail="raw transcript too large")
        # Validate JSONL on the way in so the object store never holds un-parseable raw
        # (the manager drill path then never trips on a malformed line).
        for line in body.content.splitlines():
            if not line.strip():
                continue
            try:
                if not isinstance(json.loads(line), dict):
                    raise ValueError("each raw line must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"raw must be JSONL: {exc}") from exc
        key = f"{claims.org_id}/{claims.team_id}/{compaction_id}.jsonl"
        object_store.put(key, encoded)
        store.record_raw(compaction_id, claims.org_id, key)
        return {"object_key": key}

    @app.post("/v1/founder/query")
    def founder_query(
        body: FounderQueryBody,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        check_org(body.org_id)
        result = run_query(
            store, config, org_id=body.org_id, query=body.query,
            provider=org_provider(body.org_id), source=body.source,
        )
        store.record_founder_query(
            org_id=body.org_id,
            query=body.query,
            insufficient=result.insufficient_data,
            citations=result.citations,
        )
        return {
            "filter": result.filter.model_dump(),
            "rollup": result.rollup.__dict__ if result.rollup else None,
            "narrative": result.narrative,
            "citations": result.citations,
            "insufficient_data": result.insufficient_data,
            "coverage": result.coverage.__dict__ if result.coverage else None,
        }

    @app.post("/v1/manager/query")
    def manager_query(
        body: FounderQueryBody, _: Annotated[None, Depends(require_manager)]
    ) -> dict[str, Any]:
        # Manager view: may resolve to a single named person (k-anon bypassed).
        # ALWAYS audited as an individual query — the accountability record.
        result = run_query(
            store, config, org_id=body.org_id, query=body.query,
            provider=org_provider(body.org_id), source=body.source, allow_individual=True,
        )
        store.record_founder_query(
            org_id=body.org_id,
            query=body.query,
            insufficient=result.insufficient_data,
            citations=result.citations,
            individual=True,
        )
        return {
            "filter": result.filter.model_dump(),
            "rollup": result.rollup.__dict__ if result.rollup else None,
            "narrative": result.narrative,
            "citations": result.citations,
            "insufficient_data": result.insufficient_data,
            "coverage": result.coverage.__dict__ if result.coverage else None,
        }

    @app.get("/v1/founder/topics")
    def founder_topics(
        org_id: str,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        check_org(org_id)
        # Emergent topic clusters across the team, k-anon de-identified (>= floor
        # contributors, names dropped) — cross-cutting visibility beyond project tags.
        tops, cov = team_topics(store, config, org_id)
        return {
            "topics": [t.deidentified() for t in tops],
            "coverage": {"matched": cov.matched, "used": cov.used, "truncated": cov.truncated},
        }

    @app.get("/v1/manager/topics")
    def manager_topics(
        org_id: str, _: Annotated[None, Depends(require_manager)]
    ) -> dict[str, Any]:
        tops, cov = team_topics(store, config, org_id, named=True)
        store.record_founder_query(
            org_id=org_id, query="[topics]", insufficient=False, citations=[], individual=True
        )
        return {
            "topics": [
                {**t.deidentified(), "contributors": sorted(t.contributors), "members": t.members}
                for t in tops
            ],
            "coverage": {"matched": cov.matched, "used": cov.used, "truncated": cov.truncated},
        }

    @app.post("/v1/manager/thread")
    def manager_thread(
        body: ManagerThreadBody, _: Annotated[None, Depends(require_manager)]
    ) -> dict[str, Any]:
        comps = thread(store, body.org_id, body.session_id)
        store.record_founder_query(
            org_id=body.org_id,
            query=f"[thread] {body.session_id}",
            insufficient=not comps,
            citations=[c.id for c in comps],
            individual=True,
        )
        return {
            "session_id": body.session_id,
            "arc": [
                {"id": c.id, "actor": c.actor, "project": c.project,
                 "intent": c.task_intent, "outcome": str(c.outcome)}
                for c in comps
            ],
        }

    @app.post("/v1/manager/drill")
    def manager_drill(
        body: ManagerDrillBody, _: Annotated[None, Depends(require_manager)]
    ) -> dict[str, Any]:
        # Tier-2 raw drill-down, MANAGER-ONLY + audited. Returns the released raw
        # transcript, which was already redacted at sync (redact_turn per turn).
        # The founder has no drill path — raw never reaches the founder view.
        key = store.get_raw_key(body.compaction_id, body.org_id)
        turns: list[Any] = []
        if key:
            blob = object_store.get(key)
            if blob:
                for line in blob.decode("utf-8", "replace").splitlines():
                    if not line.strip():
                        continue
                    try:  # tolerate a malformed line rather than 500 the manager
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        store.record_founder_query(
            org_id=body.org_id,
            query=f"[drill] {body.compaction_id}",
            insufficient=not turns,
            citations=[body.compaction_id] if turns else [],
            individual=True,
        )
        return {"compaction_id": body.compaction_id, "turns": turns}

    @app.get("/v1/admin/audit")
    def audit(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        rows = store.list_founder_audit(org_id)
        return {
            "entries": [
                {
                    "id": r.id,
                    "query": r.query,
                    "insufficient": r.insufficient,
                    "citation_count": r.citation_count,
                    "created_at": r.created_at,
                    "individual": bool(r.data.get("individual")),
                }
                for r in rows
            ]
        }

    @app.post("/v1/admin/founder-tokens")
    def mint_founder_token(
        body: FounderTokenBody, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, str]:
        """Mint an org-scoped founder token (hosted onboarding: one per customer org).
        Grants that org's console/query/digest view — and only that org's."""
        token = issue_founder_token(
            config.jwt_secret, org_id=body.org_id,
            expires_days=max(1, body.expires_days),
        )
        return {"token": token, "org_id": body.org_id}

    @app.get("/v1/founder/digest")
    def founder_digest(
        org_id: str,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
        since: str = "",
        until: str = "",
    ) -> dict[str, Any]:
        """The weekly digest, reachable with an org-scoped founder token (the
        /v1/admin/digest route below stays admin-only for compatibility)."""
        check_org(org_id)
        return build_weekly_digest(
            store, config, org_id=org_id, provider=org_provider(org_id),
            since=since or None, until=until or None,
        ).as_dict()

    @app.get("/v1/founder/audit")
    def founder_audit(
        org_id: str,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        """The org's founder-query audit trail (same shape as /v1/admin/audit) —
        a founder can see who queried THEIR org, including manager-view lookups."""
        check_org(org_id)
        rows = store.list_founder_audit(org_id)
        return {
            "entries": [
                {
                    "id": r.id,
                    "query": r.query,
                    "insufficient": r.insufficient,
                    "citation_count": r.citation_count,
                    "created_at": r.created_at,
                    "individual": bool(r.data.get("individual")),
                }
                for r in rows
            ]
        }

    @app.get("/v1/admin/usage")
    def llm_usage(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Month-by-month server-side LLM usage for an org, plus its effective cap."""
        override = store.get_org_quota(org_id)
        cap = override if override is not None else config.llm_monthly_cap_usd
        return {
            "org_id": org_id,
            "month": month_key(),
            "monthly_cap_usd": cap,
            "cap_is_override": override is not None,
            "months": [
                {
                    "month": r.month,
                    "calls": r.calls,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "est_cost_usd": round(r.est_cost_usd, 6),
                }
                for r in store.list_llm_usage(org_id)
            ],
        }

    @app.put("/v1/admin/orgs/{org_id}/quota")
    def set_quota(
        org_id: str, body: SetQuotaBody, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Set (or clear, with null) the org's monthly LLM budget override."""
        if body.monthly_cap_usd is not None and body.monthly_cap_usd < 0:
            raise HTTPException(status_code=422, detail="monthly_cap_usd must be >= 0")
        store.set_org_quota(org_id, body.monthly_cap_usd)
        return {"org_id": org_id, "monthly_cap_usd": body.monthly_cap_usd}

    @app.get("/v1/admin/router-analysis")
    def router_analysis(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Counterfactual cost: what released sessions would cost on cheaper tiers, with
        an estimated saving from routing the low-risk ones one tier down."""
        return analyze_counterfactual_costs(store, org_id).as_dict()

    @app.get("/v1/admin/digest")
    def weekly_digest(
        org_id: str,
        _: Annotated[None, Depends(require_admin)],
        since: str = "",
        until: str = "",
    ) -> dict[str, Any]:
        """Founder weekly digest (last 7 days by default) composed from the founder-query
        pipeline; k-anon-insufficient sections are omitted."""
        return build_weekly_digest(
            store, config, org_id=org_id, provider=org_provider(org_id),
            since=since or None, until=until or None,
        ).as_dict()

    @app.post("/v1/admin/mine-skills")
    def mine_skills(
        body: MineSkillsBody, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        # Cross-engineer org mining over released compactions. k-anonymized
        # (>=K_ANON_FLOOR distinct contributors; names dropped). Compactions are
        # already redacted on sync, so no redactor is needed here. Proposals are
        # enqueued for human approval (the action-queue seam) rather than applied.
        compactions = store.query_compactions(org_id=body.org_id, limit=100_000)
        proposals = mine_org(compactions, provider=org_provider(body.org_id))
        out = []
        for proposal in proposals:
            store.enqueue_action(
                action_id="auto_draft_org_skill",
                org_id=body.org_id,
                payload={
                    "name": proposal.draft.name,
                    "description": proposal.draft.description,
                    "skill_md": proposal.skill_md,
                    "contributor_count": proposal.provenance.contributor_count,
                    "evidence": proposal.provenance.evidence,
                },
            )
            out.append(
                {
                    "name": proposal.draft.name,
                    "description": proposal.draft.description,
                    "contributor_count": proposal.provenance.contributor_count,
                    "evidence": proposal.provenance.evidence,
                }
            )
        return {"proposals": out, "queued": len(out)}

    mount_ui(app, config, store, provider, object_store, provider_for=org_provider)
    return app


def build_default_app() -> FastAPI:
    """App wired from environment config (uvicorn entry point)."""
    config = ServerConfig.from_env()
    store = ServerStore.open(config.db_url)
    object_store = make_object_store(config)
    # Founder-narrative provider: mock by default; real Anthropic model when the
    # org sets MANTHANA_SERVER_LLM=anthropic + ANTHROPIC_API_KEY (arch §9).
    provider = make_provider(config)
    return create_app(config, store, object_store, provider)


__all__ = ["create_app", "build_default_app"]
