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

import asyncio
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from manthana.schemas import CompactionAdapter
from pydantic import BaseModel, ValidationError

from .analyzer import analyze_counterfactual_costs
from .ask import ask
from .auth import (
    AuthError,
    TeamClaims,
    issue_engineer_token,
    issue_founder_token,
    issue_team_token,
    verify_founder_token,
    verify_team_token,
)
from .config import ServerConfig
from .consolidate import consolidate_org, consolidate_provider_for, run_consolidation_pass
from .digest import build_weekly_digest
from .enrich import enrich_org, enrich_provider_for, run_enrichment_pass
from .founder import run_query, team_topics, thread
from .hardening import install_hardening
from .llm import LLMProvider, make_consolidate_provider, make_enrich_provider, make_provider
from .metering import MeteredProvider, QuotaExceededError, month_key
from .mining import FAILED, QUOTA, run_mining
from .overview import overview_provider_for, run_overview_pass
from .purge import PurgeSelector, purge
from .storage import ObjectStore, make_object_store
from .store import ServerStore
from .ui import mount_ui
from .wiki_api import mount_wiki_api
from .wiki_ui import mount_retired_wiki, mount_wiki_ui


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


class AskBody(BaseModel):
    org_id: str
    query: str


class MineSkillsBody(BaseModel):
    org_id: str


class SetQuotaBody(BaseModel):
    monthly_cap_usd: float | None = None  # None clears the override → server default


class SetPrivacyBody(BaseModel):
    mode: str  # "open" | "k_anon"


class FounderTokenBody(BaseModel):
    org_id: str
    expires_days: int = 365


class EngineerTokenBody(BaseModel):
    org_id: str
    actor: str  # the engineer's org email — becomes their note-authorship identity
    expires_days: int = 365


class FounderThreadBody(BaseModel):
    org_id: str
    session_id: str


class FounderDrillBody(BaseModel):
    org_id: str
    compaction_id: str


class PurgeBody(BaseModel):
    org_id: str
    # At least one selector is required — an unfiltered purge is refused.
    source: str | None = None  # "pending" | "full" | "claude_summary"
    contains: str | None = None  # substring of the digest's own text
    self_generated: bool = False  # Manthana's own compaction sessions
    # Sessions that ARE a compaction call rather than work ABOUT one: no files
    # touched, no real project, abandoned, and compaction-shaped text.
    structural_junk: bool = False
    # Dry run by DEFAULT. Deleting requires saying so explicitly.
    confirm: bool = False


_log = logging.getLogger(__name__)


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
    # Founder MCP gateway (spec: manthana-founder-mcp) — OFF by default. When enabled,
    # its streamable-HTTP session manager needs its lifespan run by the parent app, and
    # the token-auth wrapper becomes the tenant boundary. Built before FastAPI() so the
    # session-manager lifespan can be attached.
    mcp_asgi: Any = None
    _mcp_server: Any = None
    if config.enable_founder_mcp:
        from .founder_mcp import available as mcp_available
        from .founder_mcp import build_founder_mcp, founder_mcp_asgi

        if not mcp_available():
            raise RuntimeError(
                "MANTHANA_SERVER_ENABLE_FOUNDER_MCP set but the 'mcp' extra is missing "
                "— install: uv sync --extra mcp"
            )
        _mcp_server = build_founder_mcp(store, object_store, config)
        mcp_asgi = founder_mcp_asgi(_mcp_server, config)

    # Server-side enrichment: a batched BACKGROUND pass, never inline on ingest
    # (ingest must stay fast). Runs on its own cheap-model provider, metered per
    # org so it counts against the same monthly cap as the founder pipeline.
    enrich_provider = make_enrich_provider(config) if config.enable_enrichment else None

    # Knowledge consolidation: same posture — a batched background pass turning
    # enriched digests into org-wiki notes, on its own cheap-model provider.
    consolidate_provider = (
        make_consolidate_provider(config) if config.enable_consolidation else None
    )

    # Writes one project_overview note per project. Its own provider so a
    # description backlog cannot delay the passes that feed it.
    overview_provider = (
        make_consolidate_provider(config) if config.enable_project_overview else None
    )

    lifespan: Any = None
    if (
        _mcp_server is not None
        or enrich_provider is not None
        or consolidate_provider is not None
        or overview_provider is not None
    ):
        # Compose the lifespans with an AsyncExitStack so enabling one background
        # feature can never disturb another — each is entered only when its own
        # feature is on, and all unwind in order on shutdown.
        from contextlib import AsyncExitStack, asynccontextmanager

        @asynccontextmanager
        async def _lifespan(_app: FastAPI):  # noqa: ANN202 - FastAPI lifespan
            async with AsyncExitStack() as stack:
                if _mcp_server is not None:
                    await stack.enter_async_context(_mcp_server.session_manager.run())
                if enrich_provider is not None:
                    task = asyncio.create_task(_enrichment_loop(enrich_provider))
                    stack.callback(task.cancel)
                if consolidate_provider is not None:
                    ctask = asyncio.create_task(_consolidation_loop(consolidate_provider))
                    stack.callback(ctask.cancel)
                if overview_provider is not None:
                    otask = asyncio.create_task(_overview_loop(overview_provider))
                    stack.callback(otask.cancel)
                yield

        lifespan = _lifespan

    async def _enrichment_loop(inner: LLMProvider) -> None:
        """Periodic background pass. Runs the synchronous store/provider work in a
        worker thread so it never blocks the event loop, and swallows per-pass
        errors so a transient failure can't kill the loop for the process lifetime.
        """
        provider_for = enrich_provider_for(store, config, inner)
        while True:
            try:
                await asyncio.sleep(config.enrich_interval_seconds)
                stats = await asyncio.to_thread(
                    run_enrichment_pass, store, object_store, config, provider_for
                )
                if stats.enriched or stats.failed:
                    _log.info("enrichment pass: %s", stats.as_dict())
            except asyncio.CancelledError:  # shutdown
                raise
            except Exception:  # noqa: BLE001 - a bad pass must not kill the loop
                _log.exception("enrichment pass failed; will retry next interval")

    async def _consolidation_loop(inner: LLMProvider) -> None:
        """Sibling of ``_enrichment_loop``: periodic, off-thread, error-swallowing.
        Runs on its own interval so a consolidation backlog can't delay enrichment
        (which feeds it) or vice-versa."""
        provider_for = consolidate_provider_for(store, config, inner)
        while True:
            try:
                await asyncio.sleep(config.consolidate_interval_seconds)
                stats = await asyncio.to_thread(
                    run_consolidation_pass, store, config, provider_for
                )
                if stats.consolidated or stats.failed:
                    _log.info("consolidation pass: %s", stats.as_dict())
            except asyncio.CancelledError:  # shutdown
                raise
            except Exception:  # noqa: BLE001 - a bad pass must not kill the loop
                _log.exception("consolidation pass failed; will retry next interval")

    async def _overview_loop(inner: LLMProvider) -> None:
        """Sibling of the other two: periodic, off-thread, error-swallowing.

        Runs on a much longer interval because what a project IS changes on the
        scale of weeks, and because the pass is a no-op whenever no new work has
        landed — the contributors hash, not the clock, decides whether anything
        is spent.
        """
        provider_for = overview_provider_for(store, config, inner)
        while True:
            try:
                await asyncio.sleep(config.overview_interval_seconds)
                stats = await asyncio.to_thread(
                    run_overview_pass, store, config, provider_for
                )
                if stats.written or stats.failed:
                    _log.info("project overview pass: %s", stats.as_dict())
            except asyncio.CancelledError:  # shutdown
                raise
            except Exception:  # noqa: BLE001 - a bad pass must not kill the loop
                _log.exception("overview pass failed; will retry next interval")

    app = FastAPI(title="Manthana Server", lifespan=lifespan)
    install_hardening(app, config)

    def org_provider(org_id: str) -> LLMProvider:
        # Per-org metered view of the shared provider: the org's cap override, or
        # the server default. Cheap per-request construction; enforcement raises
        # QuotaExceededError → the 429 handler below.
        cap = store.get_org_quota(org_id)
        if cap is None:
            cap = config.llm_monthly_cap_usd
        return MeteredProvider(provider, store, org_id, cap, purpose="founder")

    def privacy_open(org_id: str) -> bool:
        """True when this org waived anonymization (named, per-individual
        results). Per-org override wins over the server default."""
        return (store.get_org_privacy(org_id) or config.privacy_mode) == "open"

    @app.exception_handler(QuotaExceededError)
    def quota_exceeded(_request: Request, exc: QuotaExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    def require_admin(x_admin_token: Annotated[str, Header()] = "") -> None:
        # constant-time comparison — admin token gates org/team/token mint + founder query
        if not _ct_eq(x_admin_token, config.admin_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

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
        # (the founder drill path then never trips on a malformed line).
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
        allow_individual = privacy_open(body.org_id)
        result = run_query(
            store, config, org_id=body.org_id, query=body.query,
            provider=org_provider(body.org_id), source=body.source,
            allow_individual=allow_individual,
        )
        # ``individual`` is driven by allow_individual itself: whenever the query
        # could resolve to a named person, the audit row says so.
        store.record_founder_query(
            org_id=body.org_id,
            query=body.query,
            insufficient=result.insufficient_data,
            citations=result.citations,
            individual=allow_individual,
        )
        return {
            "filter": result.filter.model_dump(),
            "rollup": result.rollup.__dict__ if result.rollup else None,
            "narrative": result.narrative,
            "citations": result.citations,
            "insufficient_data": result.insufficient_data,
            "coverage": result.coverage.__dict__ if result.coverage else None,
        }

    @app.post("/v1/founder/ask")
    def founder_ask(
        body: AskBody,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        """Ask the org wiki. Answers from consolidated notes first, drilling into
        session digests only when the notes don't cover the question. No k-anon
        gate on this path (see ask.py / pages.py)."""
        check_org(body.org_id)
        result = ask(
            store, config, org_id=body.org_id, query=body.query,
            provider=org_provider(body.org_id),
        )
        store.record_founder_query(
            org_id=body.org_id,
            query=body.query,
            insufficient=result.insufficient_data,
            citations=result.citations,
            individual=True,  # the wiki path can always name people
        )
        return {
            "filter": result.filter.model_dump(),
            "narrative": result.narrative,
            "note_citations": result.note_citations,
            "compaction_citations": result.compaction_citations,
            "insufficient_data": result.insufficient_data,
            "coverage": result.coverage_note(),
            "drilled": result.drilled,
        }

    @app.get("/v1/founder/topics")
    def founder_topics(
        org_id: str,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        check_org(org_id)
        # Emergent topic clusters across the team. De-identified (>= floor
        # contributors, names dropped) unless the org waived anonymization.
        named = privacy_open(org_id)
        tops, cov = team_topics(store, config, org_id, named=named)
        store.record_founder_query(
            org_id=org_id, query="[topics]", insufficient=False, citations=[],
            individual=named,
        )
        return {
            "topics": [
                {
                    **t.deidentified(),
                    **(
                        {"contributors": sorted(t.contributors), "members": t.members}
                        if named
                        else {}
                    ),
                }
                for t in tops
            ],
            "coverage": {"matched": cov.matched, "used": cov.used, "truncated": cov.truncated},
        }

    @app.post("/v1/founder/thread")
    def founder_thread(
        body: FounderThreadBody,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        """The arc of one session across its released slices. Org-scoped: a founder
        token for another org gets 403. Named-ness follows the org's privacy mode."""
        check_org(body.org_id)
        named = privacy_open(body.org_id)
        comps = thread(store, body.org_id, body.session_id)
        store.record_founder_query(
            org_id=body.org_id,
            query=f"[thread] {body.session_id}",
            insufficient=not comps,
            citations=[c.id for c in comps],
            individual=named,
        )
        return {
            "session_id": body.session_id,
            "arc": [
                {"id": c.id, "actor": c.actor if named else None, "project": c.project,
                 "intent": c.task_intent, "outcome": str(c.outcome)}
                for c in comps
            ],
        }

    @app.post("/v1/founder/drill")
    def founder_drill(
        body: FounderDrillBody,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, Any]:
        # Tier-2 raw drill-down, org-scoped + audited. Returns the released raw
        # transcript, which was already redacted at sync (redact_turn per turn).
        check_org(body.org_id)
        key = store.get_raw_key(body.compaction_id, body.org_id)
        turns: list[Any] = []
        if key:
            blob = object_store.get(key)
            if blob:
                for line in blob.decode("utf-8", "replace").splitlines():
                    if not line.strip():
                        continue
                    try:  # tolerate a malformed line rather than 500 the caller
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        store.record_founder_query(
            org_id=body.org_id,
            query=f"[drill] {body.compaction_id}",
            insufficient=not turns,
            citations=[body.compaction_id] if turns else [],
            individual=privacy_open(body.org_id),
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

    @app.post("/v1/engineer-tokens")
    def mint_engineer_token(
        body: EngineerTokenBody,
        check_org: Annotated[Callable[[str], None], Depends(require_founder_access)],
    ) -> dict[str, str]:
        """Mint a wiki login for one named engineer.

        Deliberately NOT admin-only: a founder must be able to onboard their own
        team to the shared context without routing every hire through the
        operator, or the wiki stays a single-reader tool. Scoped to the caller's
        own org (``require_founder_access`` + ``check_org``), and it grants the
        WIKI only — read and teach, never the oversight surfaces.
        """
        check_org(body.org_id)
        actor = body.actor.strip()
        if not actor:
            raise HTTPException(status_code=422, detail="actor is required")
        token = issue_engineer_token(
            config.jwt_secret, org_id=body.org_id, actor=actor,
            expires_days=max(1, body.expires_days),
        )
        return {"token": token, "org_id": body.org_id, "actor": actor}

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
        a founder can see who queried THEIR org.

        ``individual`` is true when the lookup could resolve to a named person:
        it mirrors the ``allow_individual`` the query ran with, which is exactly
        the org's privacy mode ('open' = named, 'k_anon' = de-identified). So the
        flag stays truthful now that named access is the founder's own."""
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
            # Which pass spent it — the question a stalled org actually asks.
            "purposes": {
                r.purpose: {
                    "calls": r.calls,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "est_cost_usd": round(r.est_cost_usd, 6),
                }
                for r in store.list_llm_usage_purposes(org_id, month_key())
            },
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

    @app.get("/v1/admin/enrichment")
    def enrichment_status(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Enrichment backlog for an org, and the digests that are stuck.

        A pending digest with neither a ``native_summary`` nor an uploaded raw
        transcript WAITS rather than burning a model call — this endpoint is how
        that state is observable, including the ones that aged out ("abandoned")
        and will never be retried.
        """
        rows = store.list_enrichment_state(org_id)
        return {
            "org_id": org_id,
            "enabled": config.enable_enrichment,
            "model": config.enrich_model,
            "pending": store.count_pending_for_enrichment(org_id),
            "stuck": [
                {
                    "compaction_id": r.compaction_id,
                    "state": r.state,
                    "attempts": r.attempts,
                    "detail": r.detail,
                    "first_seen_at": r.first_seen_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ],
        }

    @app.post("/v1/admin/enrichment/run")
    def enrichment_run(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Run one enrichment pass for a single org now, instead of waiting for the
        background interval. Same bounded batch and same metering."""
        stats = enrich_org(
            store,
            object_store,
            org_provider(org_id) if enrich_provider is None
            else MeteredProvider(
                enrich_provider, store, org_id,
                store.get_org_quota(org_id) or config.llm_monthly_cap_usd,
                purpose="enrich",
            ),
            config,
            org_id=org_id,
            limit=config.enrich_batch_per_org,
        )
        return stats.as_dict()

    @app.post("/v1/admin/enrichment/retry")
    def enrichment_retry(
        org_id: str,
        _: Annotated[None, Depends(require_admin)],
        limit: int = 200,
        include_without_input: bool = False,
    ) -> dict[str, Any]:
        """Un-abandon enrichment for digests that gave up waiting for input.

        Recovery for the backlog stranded by the old waiting-counts-as-attempt
        semantics: their raw may exist NOW even though it was late then. By
        default only digests that could actually enrich (raw uploaded or a
        native summary present, still pending) are reset, so the retry queue is
        never refilled with the same nothing that abandoned them. Bounded —
        un-abandoning re-enters the metered queue, and a big backlog should
        drain in deliberate slices against the org's cap.
        """
        ids = store.reset_abandoned_enrichment(
            org_id, only_with_input=not include_without_input, limit=min(max(1, limit), 500)
        )
        return {"reset": len(ids), "ids": ids}

    @app.get("/v1/admin/consolidation")
    def consolidation_status(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Consolidation backlog for an org: what's waiting to become notes, what
        got stuck, and the current note counts by status."""
        notes = store.query_notes(org_id, exclude_superseded=False)
        by_status: dict[str, int] = {}
        for n in notes:
            by_status[str(n.status)] = by_status.get(str(n.status), 0) + 1
        return {
            "org_id": org_id,
            "enabled": config.enable_consolidation,
            "model": config.consolidate_model,
            "unconsolidated": store.count_unconsolidated(org_id),
            "notes": by_status,
            "stuck": [
                {
                    "compaction_id": r.compaction_id,
                    "state": r.state,
                    "attempts": r.attempts,
                    "detail": r.detail,
                    "updated_at": r.updated_at,
                }
                for r in store.list_consolidation_state(org_id)
                if r.state != "done"
            ],
        }

    @app.post("/v1/admin/consolidation/run")
    def consolidation_run(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Run one consolidation pass for a single org now, instead of waiting for
        the background interval. Same bounded batch and same metering."""
        inner = consolidate_provider if consolidate_provider is not None else provider
        stats = consolidate_org(
            store,
            MeteredProvider(
                inner, store, org_id,
                store.get_org_quota(org_id) or config.llm_monthly_cap_usd,
                purpose="consolidate",
            ),
            config,
            org_id=org_id,
            limit=config.consolidate_batch_per_org,
        )
        return stats.as_dict()

    @app.post("/v1/admin/purge")
    def purge_compactions(
        body: PurgeBody, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """Purge compactions for an org — DRY RUN unless ``confirm`` is true.

        Admin-only, always audited, and refuses an unfiltered request. The dry run
        returns the count plus a sample of what WOULD be deleted so the operator
        can eyeball it before committing; ``confirm=true`` then deletes the rows,
        their raw object-store blobs, and their cached embedding vectors together.
        """
        if body.source is not None and body.source not in ("pending", "full", "claude_summary"):
            raise HTTPException(
                status_code=422,
                detail="source must be 'pending', 'full', or 'claude_summary'",
            )
        selector = PurgeSelector(
            source=body.source,
            contains=body.contains,
            self_generated=body.self_generated,
            structural_junk=body.structural_junk,
        )
        # Reject an unfiltered request up front — it is a bad request (422), not a
        # downstream failure, and checking here keeps it distinguishable from the
        # blob-failure case below.
        if selector.is_empty():
            raise HTTPException(
                status_code=422,
                detail=(
                    "refusing an unfiltered purge — set source, contains, "
                    "self_generated, or structural_junk"
                ),
            )
        report = purge(
            store, object_store, org_id=body.org_id, selector=selector,
            confirm=body.confirm, actor="admin",
        )
        if report.error:
            # Only reachable on an object-store failure, in which case NOTHING was
            # deleted — surface it rather than reporting a success the operator
            # cannot verify.
            raise HTTPException(status_code=502, detail=report.error)
        return report.as_dict()

    @app.get("/v1/admin/purge-audit")
    def purge_audit(
        org_id: str, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, Any]:
        """The org's purge audit trail — dry runs and confirmed deletes alike."""
        return {
            "entries": [
                {
                    "id": r.id,
                    "dry_run": r.dry_run,
                    "matched": r.matched,
                    "deleted": r.deleted,
                    "created_at": r.created_at,
                    "selector": r.data.get("selector"),
                    "actor": r.data.get("actor"),
                    "error": r.data.get("error"),
                }
                for r in store.list_purge_audit(org_id)
            ]
        }

    @app.put("/v1/admin/orgs/{org_id}/privacy")
    def set_privacy(
        org_id: str, body: SetPrivacyBody, _: Annotated[None, Depends(require_admin)]
    ) -> dict[str, str]:
        """Set an org's privacy posture: 'open' (named, per-individual) or 'k_anon'."""
        if body.mode not in ("open", "k_anon"):
            raise HTTPException(status_code=422, detail="mode must be 'open' or 'k_anon'")
        store.set_org_privacy(org_id, body.mode)
        return {"org_id": org_id, "mode": body.mode}

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
        """Cross-engineer org mining over released compactions. k-anonymized
        (>=K_ANON_FLOOR distinct contributors; names dropped). Proposals are enqueued
        for human approval (the action-queue seam) rather than applied.

        BOUNDED: one run covers the last ``mine_window_days`` of released sessions,
        newest first, capped at ``mine_max_items`` — and the response reports that
        coverage so a capped run is never mistaken for a complete one. This endpoint
        stays synchronous (scripted callers want the result, and quota exhaustion must
        still surface as 429); the console's button runs the same work in the
        background, since a browser sits behind a gateway timeout."""
        run = run_mining(store, config, body.org_id, provider=org_provider(body.org_id))
        if run.state == QUOTA:
            raise HTTPException(status_code=429, detail=run.detail)
        if run.state == FAILED:
            raise HTTPException(status_code=500, detail=run.detail)
        return {"proposals": run.proposals, "queued": run.queued, "coverage": run.as_dict()}

    mount_ui(app, config, store, provider, object_store, provider_for=org_provider)
    # The wiki console shares mount_ui's cookie session (path='/ui'), so it must
    # be mounted on the same app and under the same path prefix. Exactly one of
    # these is mounted: once the Next.js client is being served in front of this
    # process, the HTML wiki is retired to redirects (see config.retire_html_wiki
    # — off by default, because the client is a separate deployable).
    if config.retire_html_wiki:
        mount_retired_wiki(app)
    else:
        mount_wiki_ui(app, config, store, provider, provider_for=org_provider)
    # JSON twin of the wiki for the browser client. Same cookie, same /ui prefix
    # (the cookie is path-scoped there), same teach functions — a transport, not
    # a second product.
    mount_wiki_api(app, config, store, provider, provider_for=org_provider)
    if mcp_asgi is not None:
        app.mount("/mcp", mcp_asgi)
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
