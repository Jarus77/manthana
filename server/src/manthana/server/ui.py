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
import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import BackgroundTasks, Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .analyzer import analyze_counterfactual_costs
from .auth import AuthError, verify_founder_token
from .config import ServerConfig
from .digest import build_weekly_digest
from .founder import run_query, team_topics
from .llm import LLMProvider
from .metering import QuotaExceededError, month_key
from .mining import (
    FAILED,
    QUOTA,
    RUNNING,
    MineRun,
    MineRunRegistry,
    check_quota,
    run_mining,
)
from .storage import ObjectStore
from .store import ServerStore

_log = logging.getLogger(__name__)

COOKIE = "manthana_admin"

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


# Plain-language answer to the founder's question: "what is this route column, on what
# basis are we suggesting that route?" Mirrors analyzer/router.py::_safe_to_downgrade —
# if that rule changes, this copy must change with it.
_ROUTE_EXPLAINER = (
    "<div class='bar'>"
    "<h3>What the “route” column means</h3>"
    "<p>Each row shows the model your team actually used for that session and the "
    "cheaper model we think could have handled it — written as "
    "<code>opus→sonnet</code>. A dash means we are not suggesting a change.</p>"
    "<p><b>How we pick it.</b> We suggest the next model down only when the session "
    "looks like it went smoothly. All three must be true:</p>"
    "<ul>"
    "<li>the work was not abandoned;</li>"
    "<li>the engineer never got stuck going in circles or hit a dead end;</li>"
    "<li>no more than two friction points were recorded in the whole session.</li>"
    "</ul>"
    "<p>If any of those fail we leave the session where it is — a session that was a "
    "struggle is exactly the one that needed the stronger model. We only ever step "
    "down one level (Opus → Sonnet, Sonnet → Haiku). Haiku is the cheapest tier, so "
    "it is never downgraded.</p>"
    "<p><b>This is advice, not an action.</b> Manthana does not route your team's "
    "work and does not change which model anyone uses. Nothing on this page has "
    "been applied — it is a suggestion to take to your team, or ignore.</p>"
    "<p><b>Looking at this page is free.</b> It is pure arithmetic over token counts "
    "we already recorded. No AI model is called to produce it, so opening it costs "
    "nothing and does not touch your monthly AI budget.</p>"
    "<p><b>About the numbers.</b> Token counts are used only to re-price the same "
    "session at another tier's rates. They never influence <i>which</i> route we "
    "suggest — that comes entirely from the three signals above.</p>"
    "<p><b>Skipped sessions.</b> We can only price a session when we know which model "
    "tier it ran on and we have its token breakdown. Older sessions recorded before "
    "we stored that detail are skipped — they are counted and shown above, never "
    "quietly dropped from the totals.</p>"
    "</div>"
)


def _ct_eq(a: str, b: str) -> bool:
    """Constant-time compare on UTF-8 bytes — hmac.compare_digest raises TypeError on a
    non-ASCII str, which must yield a failed-auth (401/redirect), never a 500."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Manthana — {_e(title)}</title>"
        f"{_STYLE}</head><body><h1>Manthana — Founder Console</h1>"
        "<nav><a href='/ui'>Console</a>"
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
    # Last mining run per org (in-process; see MineRunRegistry on why it isn't stored).
    mine_runs = MineRunRegistry()

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
                "<button>Mine org skills</button></form> · "
                f"<a href='/ui/mine-status?org_id={_e(o.id)}'>Mining status</a></td></tr>"
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
        allow_individual = _privacy_open(org_id)
        try:
            result = run_query(
                store, config, org_id=org_id, query=query,
                provider=_provider(org_id), source=source or None,
                allow_individual=allow_individual,
            )
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc), status_code=429)
        # allow_individual drives the audit flag: a named lookup is recorded as one.
        store.record_founder_query(
            org_id=org_id,
            query=query,
            insufficient=result.insufficient_data,
            citations=result.citations,
            individual=allow_individual,
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
            f" · {rep.skipped_no_tokens} skipped (unknown tier or no token breakdown)"
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
            f"{_ROUTE_EXPLAINER}"
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
        """One session's full digest. Never raw turns — the console shows digests
        only; raw drill-down lives behind the audited POST /v1/founder/drill."""
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
        org_id: Annotated[str, Form()],
        background: BackgroundTasks,
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Start a bounded mining run in the background and redirect to its status.

        This handler used to do the whole job inline — load every compaction,
        re-embed it, cluster it, then make one model call per cluster — which took
        long enough that the gateway returned 504 before the founder saw anything.
        It now returns immediately; the run reports itself on /ui/mine-status.
        """
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        # Pre-check the budget so an exhausted org still gets its 429 on THIS click
        # rather than a silent no-op in the background.
        try:
            check_quota(store, config, org_id)
        except QuotaExceededError as exc:
            return HTMLResponse(_quota_page(exc), status_code=429)
        status_url = f"/ui/mine-status?org_id={org_id}"
        if mine_runs.is_running(org_id):
            # Don't stack runs: a second click would double the model spend for the
            # same corpus. Send the founder to the run already in flight.
            return RedirectResponse(url=status_url, status_code=303)
        mine_runs.start(org_id, MineRun(org_id=org_id, window_days=config.mine_window_days,
                                        max_items=config.mine_max_items))
        background.add_task(
            run_mining, store, config, org_id,
            provider=_provider(org_id), registry=mine_runs,
        )
        return RedirectResponse(url=status_url, status_code=303)

    @app.get("/ui/mine-status", response_class=HTMLResponse)
    def ui_mine_status(org_id: str, manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        sess = _session(manthana_admin)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = _scope_org(sess, org_id)
        run = mine_runs.get(org_id)
        if run is None:
            body = (
                f"<p class='muted'>org: {_e(org_id)}</p>"
                "<p>No mining run yet in this server process.</p>"
                "<p><a href='/ui'>← console</a></p>"
            )
            return HTMLResponse(_page("Mining", body))
        if run.state == RUNNING:
            status = (
                "<p><b>Mining now.</b> This runs in the background — you can leave this "
                "page. Reload to see the result.</p>"
            )
        elif run.state == QUOTA:
            status = (
                "<p class='warn'>Stopped: this org's monthly AI budget is spent. "
                f"{_e(run.detail)}</p>"
            )
        elif run.state == FAILED:
            status = f"<p class='warn'>Run failed: {_e(run.detail)}</p>"
        else:
            status = f"<p><b>Done.</b> {_e(run.detail)}</p>"
        # No silent caps: always state the scope, and flag it loudly when a bound bit.
        coverage = (
            f"<p class='warn'>{_e(run.coverage_note())}</p>"
            if run.capped
            else f"<p class='muted'>{_e(run.coverage_note())}</p>"
        )
        pending = len(store.list_queue(org_id))
        body = (
            f"<p class='muted'>org: {_e(org_id)} · started {_e(run.started_at)}"
            f"{' · finished ' + _e(run.finished_at) if run.finished_at else ''}</p>"
            f"{status}{coverage}"
            f"<p class='muted'>window: since {_e(run.since or '—')} "
            f"({run.window_days} days) · cap: {run.max_items} sessions per run · "
            f"{pending} proposal(s) awaiting approval</p>"
            "<p>Mining only proposes skills — nothing is published until you approve it.</p>"
            f"<p><a href='/ui/mine-status?org_id={_e(org_id)}'>Reload</a> · "
            "<a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page("Mining", body))


__all__ = ["mount_ui", "COOKIE"]
