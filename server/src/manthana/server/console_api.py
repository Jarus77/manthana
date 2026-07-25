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
from typing import Annotated, Any

from fastapi import Cookie, FastAPI, HTTPException, Query

from .analyzer import analyze_counterfactual_costs
from .config import ServerConfig
from .founder import team_topics
from .metering import month_key
from .store import ServerStore
from .ui import ConsoleSession, scope_org, session_for

_log = logging.getLogger(__name__)

API = "/ui/api/console"

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


__all__ = ["mount_console_api", "API", "SESSION_LIMIT"]
