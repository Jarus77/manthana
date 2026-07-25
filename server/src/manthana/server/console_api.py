"""JSON twin of the founder console, for the React client.

Third and last of the API modules, after ``wiki_api.py`` and ``signup_api.py``, and
the same split: the data functions live where they always did, and this exposes them
to the browser without rendering anything.

TENANT ISOLATION IS ONE FUNCTION. Every handler resolves its org through
``scope_org`` (ui.py), which forces a founder to their own org and IGNORES whatever
``org_id`` the client sent. That is deliberate and load-bearing: a client-supplied
org id must never be trusted anywhere on this surface, and keeping the decision in
one function is what makes that reviewable. An admin session (``org_id is None``) is
the only one that can name an org, and only because it may see all of them.

PRIVACY IS NOT A RENDERING CHOICE. ``_privacy_open`` decides whether actors appear
by name or the view is de-identified, and it is applied HERE rather than left to the
client — a client that forgets is a client that leaks names an org asked to have
hidden.

COST. This module is deliberately free to call. Everything here is a database read,
an embedding lookup, or arithmetic; nothing reaches a model. The three paths that
spend money — query, digest, mining — arrive in Phase 4b with their own quota
handling, so this half cannot cost a customer a cent.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import BackgroundTasks, Cookie, FastAPI, HTTPException, Query
from manthana.schemas import encode_invite
from pydantic import BaseModel

from .analyzer import analyze_counterfactual_costs
from .auth import issue_engineer_token
from .config import ServerConfig
from .digest import build_weekly_digest
from .founder import run_query, team_topics
from .llm import LLMProvider
from .metering import QuotaExceededError, month_key
from .mining import MineRun, MineRunRegistry, check_quota, run_mining
from .signup import INVITE_DAYS, INVITE_USES, default_team_id
from .store import ServerStore
from .ui import ConsoleSession, scope_org, session_for

_log = logging.getLogger(__name__)

API = "/ui/api/console"


class OrgBody(BaseModel):
    org_id: str = ""


class QueryBody(OrgBody):
    query: str = ""
    source: str = ""


class InviteBody(OrgBody):
    actor: str = ""


class RevokeBody(OrgBody):
    code: str = ""


class EngineerTokenBody(OrgBody):
    actor: str = ""


class PromoteBody(OrgBody):
    identity_id: str = ""

#: Newest-first cap on the session browser. Matches the HTML console it replaces —
#: a founder scanning what the team did wants the recent end, and an unbounded list
#: is a slow page nobody reads to the bottom of.
SESSION_LIMIT = 300


def mount_console_api(app: FastAPI, config: ServerConfig, store: ServerStore) -> None:
    """Mount the console's read API. Everything here is free to call."""

    def _session(cookie: str) -> ConsoleSession:
        """The caller, or an HTTP error. Engineers are refused rather than
        redirected: they hold a wiki login, and the oversight surfaces are not
        theirs. A JSON caller wants a status, not a login page."""
        sess = session_for(config, cookie, store)
        if sess is None:
            raise HTTPException(status_code=401, detail="not signed in")
        if sess.is_engineer:
            raise HTTPException(status_code=403, detail="the console is founders only")
        return sess

    def _org(sess: ConsoleSession, requested: str) -> str:
        """The org this call may act on. A founder is forced to their own; only an
        admin can name one. See scope_org — this is the isolation boundary."""
        org_id = scope_org(sess, requested)
        if not org_id:
            raise HTTPException(status_code=400, detail="org_id is required")
        return org_id

    def _named(org_id: str) -> bool:
        """Org waived anonymisation → named, per-individual views."""
        return (store.get_org_privacy(org_id) or config.privacy_mode) == "open"

    def _visible_orgs(sess: ConsoleSession) -> list[Any]:
        if sess.org_id is None:  # admin: every tenant
            return store.list_orgs()
        org = store.get_org(sess.org_id)
        return [org] if org else []

    def _budget(org_id: str) -> dict[str, Any]:
        month = month_key()
        spent = store.get_llm_usage(org_id, month).est_cost_usd
        override = store.get_org_quota(org_id)
        cap = override if override is not None else config.llm_monthly_cap_usd
        return {
            "month": month,
            "spent_usd": round(spent, 6),
            # 0 means unlimited, which the client must render as such rather than
            # as a bar that is 0% full.
            "cap_usd": cap,
            "cap_is_override": override is not None,
            "blocked": cap > 0 and spent >= cap,
        }

    # ── who am I ──────────────────────────────────────────────────────────
    @app.get(f"{API}/me")
    def me(manthana_admin: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        """Role plus the orgs this caller may switch between. The client uses
        ``can_switch_org`` to decide whether to draw a switcher at all — a founder
        has exactly one org and should not see a control that implies otherwise."""
        sess = _session(manthana_admin)
        orgs = _visible_orgs(sess)
        return {
            "role": sess.role,
            "org_id": sess.org_id,
            "can_switch_org": sess.org_id is None,
            "orgs": [{"id": o.id, "name": o.name} for o in orgs],
        }

    # ── the overview ──────────────────────────────────────────────────────
    @app.get(f"{API}/orgs")
    def orgs(manthana_admin: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        """One row per org the caller may see: size, backlog, and spend against cap.

        The budget is the reason this page exists. A hit cap has no other symptom —
        enrichment simply stops and the wiki fills with unsummarised sessions that
        read as a bug — so it is on the first screen rather than behind a click.
        """
        sess = _session(manthana_admin)
        return {
            "orgs": [
                {
                    "id": o.id,
                    "name": o.name,
                    "teams": len(store.list_teams(o.id)),
                    "compactions": store.count_compactions(o.id),
                    "pending_skills": len(store.list_queue(o.id)),
                    "budget": _budget(o.id),
                }
                for o in _visible_orgs(sess)
            ]
        }

    @app.get(f"{API}/audit")
    def audit(
        org_id: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=200),
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Every founder question asked of this org, answered or withheld.

        Governance, and deliberately not hidden from the person it records: a
        founder can see their own query history, which is what makes the k-anon
        floor and the withholding legible rather than mysterious.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        return {
            "org_id": scoped,
            "entries": [
                {
                    "id": r.id,
                    "query": r.query,
                    "insufficient": r.insufficient,
                    "citation_count": r.citation_count,
                    "created_at": r.created_at,
                }
                for r in store.list_founder_audit(scoped, limit=limit)
            ],
        }

    # ── people ────────────────────────────────────────────────────────────
    @app.get(f"{API}/members")
    def members(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """People who can sign in to this org, and what they can see.

        Everyone who joins through a domain match or an invite link lands as an
        ENGINEER; a founder promotes them. That is why this list exists.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        return {
            "org_id": scoped,
            "members": [
                {
                    "id": m.id,
                    "email": m.email,
                    "display_name": m.display_name,
                    "role": m.role,
                    "created_at": m.created_at,
                }
                for m in store.list_identities(scoped)
            ],
        }

    @app.get(f"{API}/invites")
    def invites(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Outstanding invites. Exhausted and expired ones are dropped rather than
        listed as history — this is a list of what still works."""
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        return {
            "org_id": scoped,
            "invites": [
                {
                    "code": inv.code,
                    "team_id": inv.team_id,
                    "actor": inv.actor,
                    "uses_left": inv.uses_left,
                    "expires_at": inv.expires_at,
                }
                for inv in store.list_invites(scoped)
                if inv.uses_left > 0
            ],
        }

    # ── sessions ──────────────────────────────────────────────────────────
    @app.get(f"{API}/sessions")
    def sessions(
        org_id: str = Query(default=""),
        project: str = Query(default=""),
        engineer: str = Query(default=""),
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """The org's released session digests — never raw transcripts.

        ``named`` tells the client whether it may show who did what. When it is
        false the actor is omitted from the payload entirely rather than sent and
        hidden in the UI, because data that reaches the browser has left the server.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        named = _named(scoped)
        comps = store.query_compactions(
            org_id=scoped,
            project=project or None,
            actor=engineer or None,
            limit=SESSION_LIMIT,
        )
        return {
            "org_id": scoped,
            "named": named,
            "limit": SESSION_LIMIT,
            "projects": store.list_projects(scoped),
            "engineers": (
                [
                    {"id": a.id, "display_name": a.display_name or a.id}
                    for a in store.list_actors(scoped)
                ]
                if named
                else []
            ),
            "sessions": [
                {
                    "id": c.id,
                    "started_at": str(c.started_at),
                    "project": c.project,
                    "task_intent": c.task_intent,
                    "outcome": str(c.outcome),
                    **({"actor": c.actor} if named else {}),
                }
                for c in comps
            ],
        }

    @app.get(f"{API}/sessions/{{compaction_id}}")
    def session_detail(
        compaction_id: str,
        org_id: str = Query(default=""),
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """One session's digest. Never the raw turns — that path is the audited
        POST /v1/founder/drill, on purpose."""
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        named = _named(scoped)
        c = store.get_compaction(compaction_id, scoped)
        if c is None:
            raise HTTPException(status_code=404, detail="not found in this org")
        return {
            "org_id": scoped,
            "named": named,
            "id": c.id,
            "session_id": c.session_id,
            "started_at": str(c.started_at),
            "project": c.project,
            "surface": str(c.surface),
            "outcome": str(c.outcome),
            "task_intent": c.task_intent,
            "approach": getattr(c, "approach", "") or "",
            "est_cost_usd": c.est_cost_usd or 0.0,
            "friction_points": [
                {"category": str(fp.category), "description": fp.description}
                for fp in (getattr(c, "friction_points", None) or [])
            ],
            "files_touched": list(getattr(c, "files_touched", None) or []),
            **({"actor": c.actor} if named else {}),
        }

    # ── topics ────────────────────────────────────────────────────────────
    @app.get(f"{API}/topics")
    def topics(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Emergent clusters across the org's work, with a coverage signal.

        Free: this is embeddings and clustering, not a model call. ``coverage``
        matters because a truncated clustering is a partial answer, and a partial
        answer that does not say so is a wrong one.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        named = _named(scoped)
        tops, cov = team_topics(store, config, scoped, named=named)
        return {
            "org_id": scoped,
            "named": named,
            "k_anon_floor": config.k_anon_floor,
            "coverage": {"matched": cov.matched, "used": cov.used, "truncated": cov.truncated},
            "topics": [
                {
                    "id": t.id,
                    "label": t.label,
                    "contributors": len(t.contributors),
                    "sessions": len(t.sessions),
                    "sample_intents": t.sample_intents,
                }
                for t in tops
            ],
        }

    # ── cost ──────────────────────────────────────────────────────────────
    @app.get(f"{API}/cost")
    def cost(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """What the org's coding sessions cost, and what a cheaper route would have.

        Pure arithmetic over recorded token counts — no model is called, and no
        session is re-run. The numbers price sessions that already happened at
        another tier's rates; they never influence which route anything takes.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        return analyze_counterfactual_costs(store, scoped).as_dict()

    @app.get(f"{API}/usage")
    def usage(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Server-side AI spend: month by month, and what spent it.

        The founder-scoped twin of GET /v1/admin/usage, which is admin-only because
        it takes any org id. Same numbers, read from the same row the metering
        gates on, so this can never disagree with the thing actually blocking a pass.
        """
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        month = month_key()
        return {
            "org_id": scoped,
            **_budget(scoped),
            "purposes": [
                {
                    "purpose": r.purpose,
                    "calls": r.calls,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "est_cost_usd": round(r.est_cost_usd, 6),
                }
                for r in store.list_llm_usage_purposes(scoped, month)
            ],
            "months": [
                {
                    "month": r.month,
                    "calls": r.calls,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "est_cost_usd": round(r.est_cost_usd, 6),
                }
                for r in store.list_llm_usage(scoped, limit=12)
            ],
        }


def mount_console_write_api(
    app: FastAPI,
    config: ServerConfig,
    store: ServerStore,
    provider: LLMProvider,
    provider_for: Callable[[str], LLMProvider] | None = None,
    mine_runs: MineRunRegistry | None = None,
) -> None:
    """Mount the console's writes and the three paths that spend money.

    Separate from the reads because the cost boundary is real: everything in
    ``mount_console_api`` is free, and everything here either changes state or
    calls a model. Keeping them apart is what let the read half ship first.

    ``mine_runs`` is shared with ``mount_ui`` rather than created here: while both
    consoles are reachable, two registries would mean a run started in one is
    invisible to the other, and the don't-stack-runs guard would not span them.
    """

    mine_runs = mine_runs if mine_runs is not None else MineRunRegistry()

    def _session(cookie: str) -> ConsoleSession:
        sess = session_for(config, cookie, store)
        if sess is None:
            raise HTTPException(status_code=401, detail="not signed in")
        if sess.is_engineer:
            raise HTTPException(status_code=403, detail="the console is founders only")
        return sess

    def _org(sess: ConsoleSession, requested: str) -> str:
        org_id = scope_org(sess, requested)
        if not org_id:
            raise HTTPException(status_code=400, detail="org_id is required")
        return org_id

    def _provider(org_id: str) -> LLMProvider:
        return provider_for(org_id) if provider_for is not None else provider

    def _quota(exc: QuotaExceededError) -> HTTPException:
        """A 429 the client can render as a STATE rather than a sentence.

        The old page put the numbers in prose and the client would have had to
        parse them back out. Structured, the page can show spend against cap, say
        when it resets, and stay correct if the wording changes.
        """
        return HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "org_id": exc.org_id,
                "cap_usd": exc.cap_usd,
                "spent_usd": exc.spent_usd,
                "message": (
                    "This org has used its monthly AI budget. It resets at the start "
                    "of next month; until then, ask your Manthana operator to raise it."
                ),
            },
        )

    def _team_for(org_id: str) -> str:
        """The org's real team id.

        NOT the literal "core". ``TeamRow.id`` is a global primary key, so
        self-serve orgs get ``{org_id}-core`` — the HTML console hardcoded "core"
        here and would mint invites pointing at whichever org happened to own that
        row. Read the org's own team instead.
        """
        teams = store.list_teams(org_id)
        return teams[0].id if teams else default_team_id(org_id)

    # ── asking questions ──────────────────────────────────────────────────
    @app.post(f"{API}/query")
    def query(
        body: QueryBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Ask the org's own history a question, answered with citations.

        Spends money, and is audited either way — including when it is withheld,
        because "we did not answer this" is the part of the k-anonymity contract a
        founder is entitled to see.
        """
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        named = (store.get_org_privacy(org_id) or config.privacy_mode) == "open"
        try:
            result = run_query(
                store, config, org_id=org_id, query=body.query,
                provider=_provider(org_id), source=body.source or None,
                allow_individual=named,
            )
        except QuotaExceededError as exc:
            raise _quota(exc) from exc
        store.record_founder_query(
            org_id=org_id, query=body.query, insufficient=result.insufficient_data,
            citations=result.citations, individual=named,
        )
        rollup = result.rollup
        return {
            "org_id": org_id,
            "query": body.query,
            "insufficient": result.insufficient_data,
            "narrative": result.narrative,
            "citations": list(result.citations),
            "coverage": result.coverage.note() if result.coverage else None,
            "rollup": (
                {
                    "sessions": rollup.session_count,
                    "contributors": rollup.distinct_contributors,
                    "tokens": rollup.total_tokens,
                    "cost_usd": rollup.total_cost_usd,
                    "by_project": rollup.by_project,
                    "by_outcome": rollup.by_outcome,
                    # Only present on a consenting org; the k-anon path never
                    # attributes work to a person.
                    **({"by_engineer": rollup.by_engineer} if rollup.by_engineer else {}),
                }
                if rollup is not None
                else None
            ),
        }

    @app.get(f"{API}/digest")
    def digest(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """The founder's weekly digest. A GET that spends money, which is why it is
        here rather than with the free reads."""
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        try:
            d = build_weekly_digest(
                store, config, org_id=scoped, provider=_provider(scoped)
            )
        except QuotaExceededError as exc:
            raise _quota(exc) from exc
        return {
            "org_id": scoped,
            "since": d.since,
            "until": d.until,
            "sections": [
                {"title": s.title, "narrative": s.narrative, "citations": list(s.citations)}
                for s in d.sections
            ],
            # What did NOT make it, and why. A digest that silently drops a section
            # reads as "nothing happened there", which is a different claim.
            "omitted": list(d.omitted),
        }

    # ── mining ────────────────────────────────────────────────────────────
    @app.post(f"{API}/mine")
    def mine(
        body: OrgBody,
        background: BackgroundTasks,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> dict[str, Any]:
        """Start a skill-mining run. Returns immediately; the run reports itself on
        ``mine-status``. Mining a real corpus took long enough that the gateway
        returned 504 before the founder saw anything."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        # Pre-check the budget so an exhausted org gets its 429 on THIS click,
        # rather than a silent no-op in the background.
        try:
            check_quota(store, config, org_id)
        except QuotaExceededError as exc:
            raise _quota(exc) from exc
        if mine_runs.is_running(org_id):
            # Don't stack runs: a second click doubles the model spend for the same
            # corpus. Report the run already in flight instead.
            return {"org_id": org_id, "started": False, "already_running": True}
        mine_runs.start(
            org_id,
            MineRun(
                org_id=org_id,
                window_days=config.mine_window_days,
                max_items=config.mine_max_items,
            ),
        )
        background.add_task(
            run_mining, store, config, org_id,
            provider=_provider(org_id), registry=mine_runs,
        )
        return {"org_id": org_id, "started": True, "already_running": False}

    @app.get(f"{API}/mine-status")
    def mine_status(
        org_id: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """How the last run went. State is in-process and deliberately not
        persisted, so "no run yet" after a restart is normal rather than an error."""
        sess = _session(manthana_admin)
        scoped = _org(sess, org_id)
        run = mine_runs.get(scoped)
        pending = len(store.list_queue(scoped))
        if run is None:
            return {"org_id": scoped, "run": None, "pending_proposals": pending}
        return {
            "org_id": scoped,
            "pending_proposals": pending,
            "run": {
                "state": run.state,
                "detail": run.detail,
                "started_at": run.started_at,
                "finished_at": run.finished_at or None,
                "window_days": run.window_days,
                "since": run.since or None,
                "matched": run.matched,
                "scanned": run.scanned,
                "max_items": run.max_items,
                "queued": run.queued,
                # No silent caps: say when a bound bit, so a partial run is never
                # mistaken for a complete one.
                "capped": run.capped,
                "coverage_note": run.coverage_note(),
            },
        }

    # ── team ──────────────────────────────────────────────────────────────
    @app.post(f"{API}/invites")
    def create_invite(
        body: InviteBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Mint an invite and return the exact `manthana setup` line.

        The engineer redeems the code for a team token at setup, so the token never
        travels — only the invite. Bound to one email = single use; blank = an open
        team invite.
        """
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        bound = body.actor.strip() or None
        code = secrets.token_urlsafe(8)
        expires = datetime.now(UTC) + timedelta(days=INVITE_DAYS)
        store.create_invite(
            code, org_id=org_id, team_id=_team_for(org_id), actor=bound,
            uses=1 if bound else INVITE_USES, expires_at=expires,
        )
        return {
            "org_id": org_id,
            "code": code,
            "actor": bound,
            "single_use": bound is not None,
            "expires_at": expires.isoformat(),
            "setup_line": f"manthana setup {encode_invite(config.public_url, code)}",
            "join_url": f"{config.public_url}/ui/join?code={code}",
        }

    @app.post(f"{API}/invites/revoke")
    def revoke_invite(
        body: RevokeBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        # Scoped twice on purpose: the claimed org is forced to the caller's, and
        # revoke_invite re-checks the code belongs to it.
        store.revoke_invite(body.code, org_id=org_id)
        return {"org_id": org_id, "revoked": body.code}

    @app.post(f"{API}/engineer-token")
    def engineer_token(
        body: EngineerTokenBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """A wiki login for one named engineer — read and teach, never oversight.

        Shown once and never stored: it is a signed JWT, so there is nothing to
        store. A founder who assumes they can come back for it would be stuck.
        """
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        actor = body.actor.strip()
        if not actor:
            raise HTTPException(status_code=422, detail="an engineer email is required")
        return {
            "org_id": org_id,
            "actor": actor,
            "token": issue_engineer_token(config.jwt_secret, org_id=org_id, actor=actor),
            "login_url": f"{config.public_url}/login",
        }

    @app.post(f"{API}/members/promote")
    def promote(
        body: PromoteBody, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Raise a joiner to founder. Tenant-scoped by ``set_identity_role``, which
        checks the target belongs to the caller's org — so this cannot reach across
        tenants even if the id is forged."""
        sess = _session(manthana_admin)
        org_id = _org(sess, body.org_id)
        if not store.set_identity_role(body.identity_id.strip(), org_id, "founder"):
            raise HTTPException(
                status_code=404, detail="that member is not in your organization"
            )
        return {"org_id": org_id, "identity_id": body.identity_id, "role": "founder"}


__all__ = ["mount_console_api", "mount_console_write_api", "API", "SESSION_LIMIT"]
