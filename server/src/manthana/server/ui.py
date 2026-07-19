"""Founder web console (server-rendered HTML + htmx).

A browser GUI for the org side — founder natural-language query, org/team overview,
and org skill mining — beyond the Swagger ``/docs`` page. Two session roles share
the same cookie login: the operator's ADMIN token sees every org, while an
org-scoped FOUNDER token (hosted multi-tenant; a JWT minted at onboarding) sees
only its own org — every handler derives the org from the SESSION for founders,
ignoring any client-supplied org field, so cross-tenant reads are impossible.

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
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from manthana.skills import mine_org

from .analyzer import analyze_counterfactual_costs
from .auth import AuthError, verify_founder_token
from .config import ServerConfig
from .digest import build_weekly_digest
from .founder import run_query, team_topics, thread
from .llm import LLMProvider
from .metering import QuotaExceededError, month_key
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
    msg = "<p class='warn'>Invalid token.</p>" if error else ""
    return _page(
        "Login",
        f"{msg}<form method='post' action='/ui/login'>"
        "<p>Admin or founder token: <input type='password' name='token' autofocus></p>"
        "<button>Sign in</button></form>",
    )


def _quota_page(exc: QuotaExceededError, back: str = "/ui") -> str:
    return _page(
        "AI quota reached",
        "<p class='warn'>Monthly AI quota reached for this org "
        f"(${exc.spent_usd:.2f} of ${exc.cap_usd:.2f} used) — resets next month; "
        "contact your Manthana admin to raise the budget.</p>"
        f"<p><a href='{back}'>← back</a></p>",
    )


def mount_ui(
    app: FastAPI,
    config: ServerConfig,
    store: ServerStore,
    provider: LLMProvider,
    object_store: ObjectStore | None = None,
    provider_for: Callable[[str], LLMProvider] | None = None,
) -> None:
    def _privacy_open(org_id: str) -> bool:
        """Org waived anonymization → named, per-individual results in the console."""
        return (store.get_org_privacy(org_id) or config.privacy_mode) == "open"

    def _provider(org_id: str) -> LLMProvider:
        # Per-org metered provider when the app supplies one (hosted quotas);
        # the shared provider otherwise (self-hosted / tests).
        return provider_for(org_id) if provider_for is not None else provider

    def _session(cookie: str) -> tuple[str, str | None] | None:
        """Resolve the console cookie to ``(role, org_id)``.

        ``("admin", None)`` = the operator (every org); ``("founder", org)`` = a
        hosted customer's founder, locked to their org. None = not signed in.
        The cookie holds the credential itself (admin token or founder JWT), so
        sessions survive restarts without server-side state.
        """
        if not cookie:
            return None
        if _ct_eq(cookie, config.admin_token):
            return ("admin", None)
        try:
            claims = verify_founder_token(config.jwt_secret, cookie)
        except AuthError:
            return None
        return ("founder", claims.org_id)

    def _scope_org(sess: tuple[str, str | None], requested: str) -> str:
        """The org a handler may act on: founders are FORCED to their own org —
        the client-supplied form/query value is ignored. This (not the form) is
        the tenant-isolation enforcement for the console."""
        _role, sess_org = sess
        return sess_org if sess_org is not None else requested

    @app.get("/ui/login", response_class=HTMLResponse)
    def login_form() -> str:
        return _login_page()

    @app.post("/ui/login")
    def login(token: Annotated[str, Form()] = "") -> Response:
        if _session(token) is None:
            return HTMLResponse(_login_page(error=True), status_code=401)
        resp = RedirectResponse(url="/ui", status_code=303)
        # Scope the cookie to the console routes; httponly keeps it out of JS.
        resp.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure,
        )
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
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        _role, sess_org = sess
        if sess_org is None:
            orgs = store.list_orgs()
        else:  # founder session: their org only — no cross-tenant listing
            org = store.get_org(sess_org)
            orgs = [org] if org else []
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
        month = month_key()
        for o in orgs:
            teams = len(store.list_teams(o.id))
            comps = store.count_compactions(o.id)
            pending = len(store.list_queue(o.id))
            spent = store.get_llm_usage(o.id, month).est_cost_usd
            override = store.get_org_quota(o.id)
            cap = override if override is not None else config.llm_monthly_cap_usd
            budget = f"${spent:.2f} / " + (f"${cap:.2f}" if cap > 0 else "∞")
            rows.append(
                f"<tr><td>{_e(o.name)} <span class='muted'>({_e(o.id)})</span></td>"
                f"<td>{teams}</td><td>{comps}</td><td>{pending}</td>"
                f"<td title='month-to-date server AI spend / monthly cap'>{_e(budget)}</td>"
                f"<td><a href='/ui/sessions?org_id={_e(o.id)}'>Sessions</a> · "
                f"<a href='/ui/topics?org_id={_e(o.id)}'>Topics</a> · "
                f"<a href='/ui/router?org_id={_e(o.id)}'>Cost $</a> · "
                f"<a href='/ui/digest?org_id={_e(o.id)}'>Digest</a> · "
                f"<form method='post' action='/ui/mine'>"
                f"<input type='hidden' name='org_id' value='{_e(o.id)}'>"
                "<button>Mine org skills</button></form></td></tr>"
            )
        table = (
            "<table><tr><th>org</th><th>teams</th><th>compactions</th>"
            "<th>pending skills</th><th>AI budget (mo)</th><th></th></tr>"
            f"{''.join(rows) or '<tr><td colspan=6>no orgs yet</td></tr>'}</table>"
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
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        try:
            result = run_query(
                store, config, org_id=org_id, query=query,
                provider=_provider(org_id), source=source or None,
                allow_individual=_privacy_open(org_id),
            )
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc), status_code=429)
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
                + (
                    f"<p>by engineer: {_e(r.by_engineer)}</p>" if r.by_engineer else ""
                )
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
        resp.set_cookie(
            MANAGER_COOKIE, token, httponly=True, samesite="lax", path="/ui/manager",
            secure=config.cookie_secure,
        )
        return resp

    @app.post("/ui/manager/query", response_class=HTMLResponse)
    def manager_ui_query(
        org_id: Annotated[str, Form()],
        query: Annotated[str, Form()],
        manthana_manager: Annotated[str, Cookie()] = "",
    ) -> Response:
        if not _manager_authed(manthana_manager):
            return RedirectResponse(url="/ui/manager", status_code=303)
        try:
            result = run_query(
                store, config, org_id=org_id, query=query,
                provider=_provider(org_id), allow_individual=True,
            )
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc, back="/ui/manager"), status_code=429)
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
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        try:
            d = build_weekly_digest(store, config, org_id=org_id, provider=_provider(org_id))
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc), status_code=429)
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
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
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

    @app.get("/ui/sessions", response_class=HTMLResponse)
    def ui_sessions(
        org_id: str,
        project: str = "",
        engineer: str = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Browse the org's released session digests (no raw transcripts) — the
        founder-facing answer to 'let me actually read what my team did'."""
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        named = _privacy_open(org_id)
        comps = store.query_compactions(
            org_id=org_id, project=project or None, actor=engineer or None, limit=300
        )
        projects = store.list_projects(org_id)
        opts = "".join(
            f"<option value='{_e(p)}'{' selected' if p == project else ''}>{_e(p)}</option>"
            for p in projects
        )
        who = (
            "".join(
                f"<option value='{_e(a.id)}'{' selected' if a.id == engineer else ''}>"
                f"{_e(a.display_name or a.id)}</option>"
                for a in store.list_actors(org_id)
            )
            if named
            else ""
        )
        filters = (
            "<div class='bar'><form method='get' action='/ui/sessions'>"
            f"<input type='hidden' name='org_id' value='{_e(org_id)}'>"
            f"<select name='project'><option value=''>all projects</option>{opts}</select> "
            + (
                f"<select name='engineer'><option value=''>all engineers</option>{who}</select> "
                if named
                else ""
            )
            + "<button>Filter</button></form></div>"
        )
        rows = "".join(
            f"<tr><td class='muted'>{_e(str(c.started_at)[:16])}</td>"
            + (f"<td>{_e(c.actor.split('@')[0])}</td>" if named else "")
            + f"<td>{_e(c.project)}</td>"
            f"<td><a href='/ui/session?org_id={_e(org_id)}&compaction_id={_e(c.id)}'>"
            f"{_e(c.task_intent[:80])}</a></td>"
            f"<td>{_e(str(c.outcome))}</td></tr>"
            for c in comps
        )
        head = (
            "<tr><th>when</th>"
            + ("<th>engineer</th>" if named else "")
            + "<th>project</th><th>what they set out to do</th><th>outcome</th></tr>"
        )
        empty = f"<tr><td colspan={5 if named else 4}>no sessions yet</td></tr>"
        body = (
            f"<p class='muted'>org: {_e(org_id)} · {len(comps)} session(s) · digests only"
            f"{'' if named else ' · de-identified'}</p>{filters}"
            f"<table>{head}{rows or empty}</table><p><a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page("Sessions", body))

    @app.get("/ui/session", response_class=HTMLResponse)
    def ui_session_detail(
        org_id: str, compaction_id: str, manthana_admin: Annotated[str, Cookie()] = ""
    ) -> Response:
        """One session's full digest. Never raw turns — the founder path has no drill."""
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        named = _privacy_open(org_id)
        c = store.get_compaction(compaction_id, org_id)
        if c is None:
            return HTMLResponse(
                _page("Session", "<p class='warn'>not found in this org</p>"), status_code=404
            )
        friction = "".join(
            f"<li><b>{_e(str(fp.category))}</b> — {_e(fp.description)}</li>"
            for fp in (getattr(c, "friction_points", None) or [])
        )
        files = "".join(f"<li>{_e(f)}</li>" for f in (getattr(c, "files_touched", None) or []))
        meta = (
            f"<p class='muted'>{_e(str(c.started_at)[:16])} · project {_e(c.project)}"
            + (f" · {_e(c.actor)}" if named else "")
            + f" · outcome <b>{_e(str(c.outcome))}</b> · {_e(str(c.surface))}</p>"
        )
        body = (
            f"<h3>{_e(c.task_intent)}</h3>{meta}"
            f"<h4>Approach</h4><pre>{_e(getattr(c, 'approach', '') or '—')}</pre>"
            f"<h4>Friction</h4><ul>{friction or '<li>none recorded</li>'}</ul>"
            f"<h4>Files touched</h4><ul>{files or '<li>none recorded</li>'}</ul>"
            f"<p class='muted'>session {_e(c.session_id)} · "
            f"~${(c.est_cost_usd or 0):.2f} API-list-equiv</p>"
            f"<p><a href='/ui/sessions?org_id={_e(org_id)}'>← all sessions</a></p>"
        )
        return HTMLResponse(_page("Session", body))

    @app.get("/ui/topics", response_class=HTMLResponse)
    def ui_topics(org_id: str, manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        tops, cov = team_topics(store, config, org_id, named=_privacy_open(org_id))
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
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        # Mining touches the provider/embedder; degrade to a clean redirect (no
        # 500 on the admin console) if anything raises.
        try:
            compactions = store.query_compactions(org_id=org_id, limit=100_000)
            for proposal in mine_org(compactions, provider=_provider(org_id)):
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
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc), status_code=429)
        except Exception:  # noqa: BLE001 - console action degrades, never 500s
            _log.exception("org skill mining failed for %s", org_id)
        return RedirectResponse(url="/ui", status_code=303)


__all__ = ["mount_ui", "COOKIE", "MANAGER_COOKIE"]
