"""Founder web console (server-rendered HTML + htmx).

A browser GUI for the org side — founder natural-language query, org/team overview,
and org skill mining — beyond the Swagger ``/docs`` page. Org-wide data, so it is
gated by a cookie-based admin login (``hmac.compare_digest`` vs the configured
admin token; httponly cookie). Self-hosted, single-admin for v1.

NOTE: like ``app.py``, this module intentionally does NOT use ``from __future__
import annotations`` — FastAPI must resolve the ``Form``/``Cookie`` parameters in
these closure-scoped route functions at runtime, which stringized annotations
would break.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import hmac
import html
import json
import logging
from typing import Annotated

from fastapi import Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from manthana.skills import mine_org

from .analyzer import analyze_counterfactual_costs
from .config import ServerConfig
from .digest import build_weekly_digest
from .founder import run_query, team_topics, thread
from .llm import LLMProvider
from .storage import ObjectStore
from .store import ServerStore

_log = logging.getLogger(__name__)

COOKIE = "manthana_admin"
MANAGER_COOKIE = "manthana_manager"

_STYLE = (
    "<style>body{font:14px system-ui;margin:2rem;max-width:1000px}"
    "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px 8px;"
    "text-align:left}th{background:#f5f5f5}button{cursor:pointer;padding:4px 10px}"
    "textarea,select,input{font:inherit;padding:4px}form{display:inline}"
    ".bar{margin:0 0 1rem;padding:.6rem;background:#f7f7f7;border:1px solid #eee;border-radius:6px}"
    ".warn{color:#a60}.muted{color:#666}pre{white-space:pre-wrap;background:#fafafa;"
    "border:1px solid #eee;padding:8px}nav a{margin-right:1rem}</style>"
)


def _e(value: object) -> str:
    return html.escape(str(value))


def _ct_eq(a: str, b: str) -> bool:
    """Constant-time compare on UTF-8 bytes — hmac.compare_digest raises TypeError on a
    non-ASCII str, which must yield a failed-auth (401/redirect), never a 500."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Manthana — {_e(title)}</title>"
        f"{_STYLE}</head><body><h1>Manthana — Founder Console</h1>"
        "<nav><a href='/ui'>Console</a><a href='/ui/manager'>Manager</a>"
        "<form method='post' action='/ui/logout'><button>Log out</button></form> "
        "<a href='/docs'>API</a></nav>"
        f"{body}</body></html>"
    )


def _login_page(error: bool = False) -> str:
    msg = "<p class='warn'>Invalid admin token.</p>" if error else ""
    return _page(
        "Login",
        f"{msg}<form method='post' action='/ui/login'>"
        "<p>Admin token: <input type='password' name='token' autofocus></p>"
        "<button>Sign in</button></form>",
    )


def mount_ui(
    app: FastAPI,
    config: ServerConfig,
    store: ServerStore,
    provider: LLMProvider,
    object_store: ObjectStore | None = None,
) -> None:
    def _authed(cookie: str) -> bool:
        return bool(cookie) and _ct_eq(cookie, config.admin_token)

    @app.get("/ui/login", response_class=HTMLResponse)
    def login_form() -> str:
        return _login_page()

    @app.post("/ui/login")
    def login(token: Annotated[str, Form()] = "") -> Response:
        if not _ct_eq(token, config.admin_token):
            return HTMLResponse(_login_page(error=True), status_code=401)
        resp = RedirectResponse(url="/ui", status_code=303)
        # Scope the cookie to the console routes; httponly keeps it out of JS.
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/ui")
        return resp

    @app.post("/ui/logout")
    def logout() -> Response:
        # POST (not GET): logout mutates auth state, so it must not be triggerable
        # by a GET (link prefetch / cross-site image).
        resp = RedirectResponse(url="/ui/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/ui")  # path must match set_cookie to clear
        resp.delete_cookie(MANAGER_COOKIE, path="/ui/manager")  # also end any manager session
        return resp

    @app.get("/ui", response_class=HTMLResponse)
    def console(manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        orgs = store.list_orgs()
        options = "".join(f"<option value='{_e(o.id)}'>{_e(o.name)}</option>" for o in orgs)
        query_form = (
            "<div class='bar'><form method='post' action='/ui/query'>"
            f"<select name='org_id'>{options or '<option>—</option>'}</select> "
            "<input name='query' size='44' placeholder='what has the team been working on?'> "
            "<select name='source'><option value=''>all sources</option>"
            "<option value='full'>full only</option>"
            "<option value='claude_summary'>Claude summaries only</option></select> "
            "<button>Ask</button></form></div>"
        )
        rows = []
        for o in orgs:
            teams = len(store.list_teams(o.id))
            comps = store.count_compactions(o.id)
            pending = len(store.list_queue(o.id))
            rows.append(
                f"<tr><td>{_e(o.name)} <span class='muted'>({_e(o.id)})</span></td>"
                f"<td>{teams}</td><td>{comps}</td><td>{pending}</td>"
                f"<td><a href='/ui/topics?org_id={_e(o.id)}'>Topics</a> · "
                f"<a href='/ui/router?org_id={_e(o.id)}'>Cost $</a> · "
                f"<a href='/ui/digest?org_id={_e(o.id)}'>Digest</a> · "
                f"<form method='post' action='/ui/mine'>"
                f"<input type='hidden' name='org_id' value='{_e(o.id)}'>"
                "<button>Mine org skills</button></form></td></tr>"
            )
        table = (
            "<table><tr><th>org</th><th>teams</th><th>compactions</th>"
            "<th>pending skills</th><th></th></tr>"
            f"{''.join(rows) or '<tr><td colspan=5>no orgs yet</td></tr>'}</table>"
        )
        # Recent founder queries across orgs (governance / transparency).
        audit_rows = []
        for o in orgs:
            for entry in store.list_founder_audit(o.id, limit=5):
                state = "withheld" if entry.insufficient else f"{entry.citation_count} cites"
                audit_rows.append(
                    f"<tr><td class='muted'>{_e(entry.created_at)}</td><td>{_e(o.id)}</td>"
                    f"<td>{_e(entry.query)}</td><td>{_e(state)}</td></tr>"
                )
        audit = (
            "<h3>Recent founder queries</h3>"
            "<table><tr><th>when</th><th>org</th><th>query</th><th>result</th></tr>"
            f"{''.join(audit_rows) or '<tr><td colspan=4>none yet</td></tr>'}</table>"
        )
        return HTMLResponse(_page("Console", query_form + table + audit))

    @app.post("/ui/query", response_class=HTMLResponse)
    def ui_query(
        org_id: Annotated[str, Form()],
        query: Annotated[str, Form()],
        source: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        result = run_query(
            store, config, org_id=org_id, query=query, provider=provider, source=source or None
        )
        store.record_founder_query(
            org_id=org_id,
            query=query,
            insufficient=result.insufficient_data,
            citations=result.citations,
        )
        if result.rollup is None:
            roll = "<p class='warn'>insufficient data (k-anonymity floor not met)</p>"
        else:
            r = result.rollup
            roll = (
                f"<p>compactions={r.session_count} · contributors={r.distinct_contributors} · "
                f"tokens={r.total_tokens:,} · "
                f"<span title='API list-price equivalent — NOT subscription spend'>"
                f"~${r.total_cost_usd:,.2f} API-list-equiv</span></p>"
                f"<p>by project: {_e(r.by_project)}<br>by outcome: {_e(r.by_outcome)}</p>"
            )
        cites = ", ".join(_e(c) for c in result.citations) or "—"
        cov = f"<p class='muted'>{_e(result.coverage.note())}</p>" if result.coverage else ""
        body = (
            f"<p class='muted'>query: {_e(query)} · org: {_e(org_id)}</p>{roll}{cov}"
            f"<h3>Narrative</h3><pre>{_e(result.narrative)}</pre>"
            f"<p>citations: {cites}</p><p><a href='/ui'>← back</a></p>"
        )
        return HTMLResponse(_page("Query", body))

    # ── Manager view (per-individual, k-anon-bypassing, AUDITED) ──────────────
    def _manager_authed(cookie: str) -> bool:
        return (
            bool(config.manager_token)
            and bool(cookie)
            and _ct_eq(cookie, config.manager_token)
        )

    @app.get("/ui/manager", response_class=HTMLResponse)
    def manager_console(manthana_manager: Annotated[str, Cookie()] = "") -> Response:
        if not config.manager_token:
            return HTMLResponse(
                _page(
                    "Manager",
                    "<p class='warn'>Manager view is disabled — set "
                    "MANTHANA_SERVER_MANAGER_TOKEN to enable per-individual queries.</p>",
                )
            )
        if not _manager_authed(manthana_manager):
            return HTMLResponse(
                _page(
                    "Manager login",
                    "<form method='post' action='/ui/manager/login'>"
                    "<p>Manager token: <input type='password' name='token' autofocus></p>"
                    "<button>Sign in</button></form>",
                )
            )
        orgs = store.list_orgs()
        options = "".join(f"<option value='{_e(o.id)}'>{_e(o.name)}</option>" for o in orgs)
        sel = f"<select name='org_id'>{options or '<option>—</option>'}</select> "
        form = (
            "<div class='bar'><b>Manager view</b> — can name individuals; every query "
            "is <u>logged</u>.<br>"
            f"<form method='post' action='/ui/manager/query'>{sel}"
            "<input name='query' size='40' placeholder='what did Suraj work on this week?'> "
            "<button>Ask (named)</button></form><br>"
            f"<form method='get' action='/ui/manager/topics'>{sel}"
            "<button>Named topics</button></form> "
            f"<form method='post' action='/ui/manager/thread'>{sel}"
            "<input name='session_id' size='28' placeholder='session id'> "
            "<button>Thread (arc)</button></form><br>"
            f"<form method='post' action='/ui/manager/drill'>{sel}"
            "<input name='compaction_id' size='28' placeholder='compaction id'> "
            "<button>Drill raw</button></form></div>"
        )
        return HTMLResponse(_page("Manager", form))

    @app.post("/ui/manager/login")
    def manager_login(token: Annotated[str, Form()] = "") -> Response:
        if not config.manager_token or not _ct_eq(token, config.manager_token):
            return HTMLResponse(
                _page("Manager login", "<p class='warn'>Invalid manager token.</p>"
                      "<p><a href='/ui/manager'>← back</a></p>"),
                status_code=401,
            )
        resp = RedirectResponse(url="/ui/manager", status_code=303)
        resp.set_cookie(MANAGER_COOKIE, token, httponly=True, samesite="lax", path="/ui/manager")
        return resp

    @app.post("/ui/manager/query", response_class=HTMLResponse)
    def manager_ui_query(
        org_id: Annotated[str, Form()],
        query: Annotated[str, Form()],
        manthana_manager: Annotated[str, Cookie()] = "",
    ) -> Response:
        if not _manager_authed(manthana_manager):
            return RedirectResponse(url="/ui/manager", status_code=303)
        result = run_query(
            store, config, org_id=org_id, query=query, provider=provider, allow_individual=True
        )
        store.record_founder_query(
            org_id=org_id,
            query=query,
            insufficient=result.insufficient_data,
            citations=result.citations,
            individual=True,
        )
        if result.rollup is None:
            roll = "<p class='warn'>insufficient data</p>"
        else:
            r = result.rollup
            roll = (
                f"<p>compactions={r.session_count} · contributors={r.distinct_contributors} · "
                f"tokens={r.total_tokens:,}</p>"
            )
        cites = ", ".join(_e(c) for c in result.citations) or "—"
        body = (
            "<p class='warn'>⚠ named (manager) query — this lookup is logged.</p>"
            f"<p class='muted'>query: {_e(query)} · org: {_e(org_id)}</p>{roll}"
            f"<h3>Narrative</h3><pre>{_e(result.narrative)}</pre>"
            f"<p>citations: {cites}</p><p><a href='/ui/manager'>← back</a></p>"
        )
        return HTMLResponse(_page("Manager query", body))

    @app.get("/ui/manager/topics", response_class=HTMLResponse)
    def manager_topics_page(
        org_id: str, manthana_manager: Annotated[str, Cookie()] = ""
    ) -> Response:
        if not _manager_authed(manthana_manager):
            return RedirectResponse(url="/ui/manager", status_code=303)
        tops, _cov = team_topics(store, config, org_id, named=True)
        store.record_founder_query(
            org_id=org_id, query="[topics]", insufficient=False, citations=[], individual=True
        )
        rows = "".join(
            f"<tr><td>{_e(t.label)}</td>"
            f"<td>{_e(', '.join(sorted(a.split('@')[0] for a in t.contributors)))}</td>"
            f"<td>{len(t.sessions)}</td></tr>"
            for t in tops
        )
        body = (
            "<p class='warn'>⚠ named (manager) topics — logged.</p>"
            f"<p class='muted'>org: {_e(org_id)}</p>"
            "<table><tr><th>topic</th><th>people</th><th>sessions</th></tr>"
            f"{rows or '<tr><td colspan=3>no topics</td></tr>'}</table>"
            "<p><a href='/ui/manager'>← back</a></p>"
        )
        return HTMLResponse(_page("Manager topics", body))

    @app.post("/ui/manager/thread", response_class=HTMLResponse)
    def manager_thread_page(
        org_id: Annotated[str, Form()],
        session_id: Annotated[str, Form()],
        manthana_manager: Annotated[str, Cookie()] = "",
    ) -> Response:
        if not _manager_authed(manthana_manager):
            return RedirectResponse(url="/ui/manager", status_code=303)
        comps = thread(store, org_id, session_id)
        store.record_founder_query(
            org_id=org_id, query=f"[thread] {session_id}", insufficient=not comps,
            citations=[c.id for c in comps], individual=True,
        )
        rows = "".join(
            f"<tr><td class='muted'>{_e(str(c.started_at)[:16])}</td>"
            f"<td>{_e(c.actor.split('@')[0])}</td><td>{_e(c.project)}</td>"
            f"<td>{_e(c.task_intent[:70])}</td></tr>"
            for c in comps
        )
        body = (
            "<p class='warn'>⚠ named (manager) thread — logged.</p>"
            f"<p class='muted'>arc of {_e(session_id)} · org: {_e(org_id)}</p>"
            "<table><tr><th>when</th><th>who</th><th>project</th><th>intent</th></tr>"
            f"{rows or '<tr><td colspan=4>no compactions in this thread</td></tr>'}</table>"
            "<p><a href='/ui/manager'>← back</a></p>"
        )
        return HTMLResponse(_page("Manager thread", body))

    @app.post("/ui/manager/drill", response_class=HTMLResponse)
    def manager_drill_page(
        org_id: Annotated[str, Form()],
        compaction_id: Annotated[str, Form()],
        manthana_manager: Annotated[str, Cookie()] = "",
    ) -> Response:
        if not _manager_authed(manthana_manager):
            return RedirectResponse(url="/ui/manager", status_code=303)
        turns: list[dict[str, object]] = []
        key = store.get_raw_key(compaction_id, org_id)
        if key and object_store is not None:
            blob = object_store.get(key)
            if blob:
                for line in blob.decode("utf-8", "replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        store.record_founder_query(
            org_id=org_id, query=f"[drill] {compaction_id}", insufficient=not turns,
            citations=[compaction_id] if turns else [], individual=True,
        )
        rows = ""
        for t in turns:
            txt = str(t.get("content") or t.get("tool_output") or "")[:2000]
            rows += (
                f"<tr><td>{_e(t.get('seq'))}</td><td>{_e(t.get('role'))}</td>"
                f"<td><pre>{_e(txt)}</pre></td></tr>"
            )
        body = (
            "<p class='warn'>⚠ named (manager) raw drill — logged. Released+redacted raw only.</p>"
            f"<p class='muted'>{_e(compaction_id)} · org: {_e(org_id)}</p>"
            "<table><tr><th>seq</th><th>role</th><th>content (redacted)</th></tr>"
            f"{rows or '<tr><td colspan=3>no released raw for this compaction</td></tr>'}</table>"
            "<p><a href='/ui/manager'>← back</a></p>"
        )
        return HTMLResponse(_page("Manager drill", body))

    @app.get("/ui/digest", response_class=HTMLResponse)
    def ui_digest(org_id: str, manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        d = build_weekly_digest(store, config, org_id=org_id, provider=provider)
        secs = "".join(
            f"<h3>{_e(s.title)}</h3><p>{_e(s.narrative)}</p>"
            f"<p class='muted'>sources: {_e(', '.join(s.citations))}</p>"
            for s in d.sections
        )
        omitted = (
            f"<p class='muted'>omitted (k-anon / no data): {_e(', '.join(d.omitted))}</p>"
            if d.omitted
            else ""
        )
        body = (
            f"<p class='muted'>org: {_e(org_id)} · {_e(d.since)} → {_e(d.until)} · "
            "founder-aggregate, k-anon enforced</p>"
            f"{secs or '<p>no sections cleared the k-anonymity floor for this window.</p>'}"
            f"{omitted}<p><a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page(f"Digest — {org_id}", body))

    @app.get("/ui/router", response_class=HTMLResponse)
    def ui_router(org_id: str, manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        rep = analyze_counterfactual_costs(store, org_id)
        rows = "".join(
            f"<tr><td>{_e(r.project)}</td><td>{_e(r.tier)}→{_e(r.target_tier or '—')}</td>"
            f"<td>${r.current_usd:.2f}</td><td>${r.projected_usd:.2f}</td>"
            f"<td>${r.savings_usd:.2f}</td></tr>"
            for r in rep.rows[:25]
            if r.savings_usd > 0
        )
        skip = (
            f" · {rep.skipped_no_tokens} pre-breakdown digests skipped"
            if rep.skipped_no_tokens
            else ""
        )
        body = (
            f"<p class='muted'>org: {_e(org_id)} · re-priced {rep.priced}/{rep.sessions} "
            f"released sessions{skip} · API-list-equivalent (not subscription spend)</p>"
            f"<h3>Estimated savings: ${rep.savings_usd:.2f} ({rep.savings_pct:.1f}%) "
            f"by routing {sum(rep.by_target.values())} low-risk session(s) one tier down</h3>"
            f"<p class='muted'>current ~${rep.current_usd:.2f} → "
            f"projected ~${rep.projected_usd:.2f} · downgrades: {_e(str(rep.by_target) or '—')}</p>"
            "<table><tr><th>project</th><th>route</th><th>now</th><th>projected</th>"
            "<th>save</th></tr>"
            f"{rows or '<tr><td colspan=5>no downgrade candidates</td></tr>'}</table>"
            "<p><a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page(f"Cost — {org_id}", body))

    @app.get("/ui/topics", response_class=HTMLResponse)
    def ui_topics(org_id: str, manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        tops, cov = team_topics(store, config, org_id)
        rows = "".join(
            f"<tr><td>{_e(t.label)}</td><td>{len(t.contributors)}</td>"
            f"<td>{len(t.sessions)}</td>"
            f"<td class='muted'>{_e('; '.join(t.sample_intents))}</td></tr>"
            for t in tops
        )
        empty = "<tr><td colspan=4>no cross-cutting topics meet the k-anon floor yet</td></tr>"
        trunc = (
            f"<p class='warn'>clustered over the {cov.used} most recent of {cov.matched} "
            "compactions — older work not shown</p>"
            if cov.truncated
            else ""
        )
        body = (
            f"<p class='muted'>org: {_e(org_id)} · de-identified, "
            f"≥{config.k_anon_floor} contributors per topic</p>{trunc}"
            "<table><tr><th>topic</th><th>people</th><th>sessions</th><th>sample work</th></tr>"
            f"{rows or empty}</table><p><a href='/ui'>← back</a></p>"
        )
        return HTMLResponse(_page("Team topics", body))

    @app.post("/ui/mine")
    def ui_mine(
        org_id: Annotated[str, Form()], manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        if not _authed(manthana_admin):
            return RedirectResponse(url="/ui/login", status_code=303)
        # Mining touches the provider/embedder; degrade to a clean redirect (no
        # 500 on the admin console) if anything raises.
        try:
            compactions = store.query_compactions(org_id=org_id, limit=100_000)
            for proposal in mine_org(compactions, provider=provider):
                store.enqueue_action(
                    action_id="auto_draft_org_skill",
                    org_id=org_id,
                    payload={
                        "name": proposal.draft.name,
                        "description": proposal.draft.description,
                        "skill_md": proposal.skill_md,
                        "contributor_count": proposal.provenance.contributor_count,
                    },
                )
        except Exception:  # noqa: BLE001 - console action degrades, never 500s
            _log.exception("org skill mining failed for %s", org_id)
        return RedirectResponse(url="/ui", status_code=303)


__all__ = ["mount_ui", "COOKIE", "MANAGER_COOKIE"]
