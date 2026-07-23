"""Founder web console (server-rendered HTML + htmx).

A browser GUI for the org side — founder natural-language query, org/team overview,
and org skill mining — beyond the Swagger ``/docs`` page. THREE session roles share
the same cookie login: the operator's ADMIN token sees every org; an org-scoped
FOUNDER token (hosted multi-tenant; a JWT minted at onboarding) sees only its own
org; an ENGINEER token sees only its own org AND only the wiki (``wiki_ui.py``) —
the oversight pages in this module send engineers to ``/ui/home`` instead. Every
handler derives the org from the SESSION for non-admins, ignoring any
client-supplied org field, so cross-tenant reads are impossible.

NOTE: like ``app.py``, this module intentionally does NOT use ``from __future__
import annotations`` — FastAPI must resolve the ``Form``/``Cookie`` parameters in
these closure-scoped route functions at runtime, which stringized annotations
would break.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import hmac
import html
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import BackgroundTasks, Cookie, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from manthana.schemas import encode_invite

from .analyzer import analyze_counterfactual_costs
from .auth import (
    AuthError,
    issue_engineer_token,
    verify_engineer_token,
    verify_founder_token,
)
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
    ".warn{color:#a60}.muted{color:#666}"
    "button.link{background:none;border:0;color:#a60;padding:0;text-decoration:underline}"
    "pre{white-space:pre-wrap;background:#fafafa;"
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
        "<nav><a href='/ui/home'>Home</a><a href='/ui'>Console</a>"
        "<form method='post' action='/ui/logout'><button>Log out</button></form> "
        "<a href='/docs'>API</a></nav>"
        f"{body}</body></html>"
    )


# Session resolution + tenant scoping are MODULE-LEVEL (not closures inside
# mount_ui) so the wiki console can reuse the exact same cookie contract rather
# than duplicating it — one implementation, one place to get tenant isolation
# right. The console cookie is scoped path='/ui', so every wiki route must also
# live under /ui/... to be authenticated at all.
@dataclass(frozen=True)
class ConsoleSession:
    """Who is signed into the console, and what they may reach.

    Three roles, deliberately unequal:

      * ``admin``    — the operator. Every org; every surface.
      * ``founder``  — one org; every surface for that org (wiki + oversight:
                       cost, mining, audit, digests).
      * ``engineer`` — one org; the WIKI only. They read and TEACH the shared
                       context, but the founder's oversight surfaces are not
                       theirs to see. ``actor`` names them, so their edits are
                       attributed to a person rather than a shared role.
    """

    role: str
    org_id: str | None  # None = admin (not locked to any one org)
    actor: str | None = None  # set for engineers; the note author identity

    @property
    def is_engineer(self) -> bool:
        return self.role == "engineer"

    @property
    def author(self) -> str:
        """Attribution for anything this session writes."""
        return self.actor or self.role


def session_for(
    config: ServerConfig, cookie: str, store: ServerStore | None = None
) -> ConsoleSession | None:
    """Resolve the console cookie to a session, or None when not signed in.

    The cookie holds the credential itself (admin token or a scoped JWT), so
    sessions survive restarts without server-side state. Tried in order of
    privilege; each verifier rejects the other scopes, so a token can only ever
    authenticate as what it was issued for.

    ``store`` is how a leaked JWT is killed without rotating the shared secret:
    when supplied, a revoked token resolves to None (signed out) before any
    scope is granted. It is optional only so pure-unit callers can skip the DB;
    every real request path passes it.
    """
    if not cookie:
        return None
    if _ct_eq(cookie, config.admin_token):
        return ConsoleSession(role="admin", org_id=None)
    # A revoked JWT is dead on every path; the admin token above is a raw secret,
    # not a JWT, and is rotated differently, so it is not blocklist-checked.
    if store is not None and store.is_token_revoked(cookie):
        return None
    try:
        founder = verify_founder_token(config.jwt_secret, cookie)
    except AuthError:
        pass
    else:
        return ConsoleSession(role="founder", org_id=founder.org_id)
    try:
        engineer = verify_engineer_token(config.jwt_secret, cookie)
    except AuthError:
        return None
    return ConsoleSession(
        role="engineer", org_id=engineer.org_id, actor=engineer.actor
    )


def scope_org(sess: ConsoleSession, requested: str) -> str:
    """The org a handler may act on: founders and engineers are FORCED to their
    own org — the client-supplied form/query value is ignored. This (not the
    form) is the tenant-isolation enforcement for the console."""
    return sess.org_id if sess.org_id is not None else requested


def _login_page(error: bool = False) -> str:
    msg = "<p class='warn'>Invalid token.</p>" if error else ""
    return _page(
        "Login",
        f"{msg}<form method='post' action='/ui/login'>"
        "<p>Your Manthana token: <input type='password' name='token' autofocus></p>"
        "<button>Sign in</button></form>"
        "<p class='muted'>Founders and engineers both sign in here. Engineers land on "
        "the team wiki, where they can read and correct the shared context.</p>",
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

    def _session(cookie: str) -> ConsoleSession | None:
        return session_for(config, cookie, store)

    _scope_org = scope_org

    def _founder_session(cookie: str) -> ConsoleSession | Response:
        """Gate for the OVERSIGHT surfaces (orgs, cost, mining, audit, digests,
        session browsing). Engineers hold a wiki login, not a management one, so
        they are sent to the wiki rather than shown a permission error — from
        their side these pages simply are not part of their product."""
        sess = _session(cookie)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        if sess.is_engineer:
            return RedirectResponse(url="/ui/home", status_code=303)
        return sess

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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
        if sess.org_id is None:
            orgs = store.list_orgs()
        else:  # founder session: their org only — no cross-tenant listing
            org = store.get_org(sess.org_id)
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
        # Onboarding the team to the wiki has to be self-serve, or the shared
        # context ends up with exactly one reader.
        who = "".join(
            f"<option value='{_e(a.id)}'>{_e(a.display_name or a.id)}</option>"
            for o in orgs
            for a in store.list_actors(o.id)
        )
        team_access = (
            "<h3>Team access to the wiki</h3>"
            "<div class='bar'><form method='post' action='/ui/engineer-token'>"
            f"<select name='org_id'>{options or '<option>—</option>'}</select> "
            f"<input name='actor' list='known-actors' size='28' "
            "placeholder='engineer@yourcompany.com'>"
            f"<datalist id='known-actors'>{who}</datalist> "
            "<button>Create wiki login</button></form>"
            "<p class='muted'>Gives this person a link to read and correct the team "
            "wiki. They do not see cost, mining, or audit pages.</p></div>"
        )
        # Invites are the PRIMARY onboarding: a wiki login only lets someone read,
        # while an invite is what an engineer redeems with `manthana setup` so their
        # coding sessions start flowing INTO the wiki. Self-serve, scoped to this
        # founder's own org — the whole point is a startup grows its team without
        # asking the operator for anything.
        pending = [inv for inv in _invites_for(orgs) if inv.uses_left > 0]
        pending_rows = "".join(
            f"<tr><td class='muted'>{_e(inv.org_id)}</td><td>{_e(inv.team_id)}</td>"
            f"<td>{_e(inv.actor or '(open)')}</td><td>{inv.uses_left}</td>"
            f"<td class='muted'>{_e(inv.expires_at[:10])}</td>"
            f"<td><form method='post' action='/ui/invite/revoke'>"
            f"<input type='hidden' name='org_id' value='{_e(inv.org_id)}'>"
            f"<input type='hidden' name='code' value='{_e(inv.code)}'>"
            "<button class='link'>revoke</button></form></td></tr>"
            for inv in pending
        )
        invites = (
            "<h3>Invite engineers</h3>"
            "<div class='bar'><form method='post' action='/ui/invite'>"
            f"<select name='org_id'>{options or '<option>—</option>'}</select> "
            "<input name='actor' size='28' "
            "placeholder='engineer@yourcompany.com (optional)'> "
            "<button>Create invite</button></form>"
            "<p class='muted'>Prints a one-line <code>manthana setup …</code> to send "
            "them — that is their whole onboarding, and it captures their sessions "
            "into the wiki. Leave the email blank for one shared invite the whole "
            "team can use; fill it in for a single-use, per-person invite.</p>"
            "<table><tr><th>org</th><th>team</th><th>for</th><th>uses left</th>"
            "<th>expires</th><th></th></tr>"
            f"{pending_rows or '<tr><td colspan=6 class=muted>no open invites</td></tr>'}"
            "</table></div>"
        )
        return HTMLResponse(
            _page("Console", query_form + table + invites + team_access + audit)
        )

    def _invites_for(orgs: list) -> list:  # noqa: ANN001 - list[OrgRow]
        out = []
        for o in orgs:
            out.extend(store.list_invites(o.id))
        return out

    @app.post("/ui/invite", response_class=HTMLResponse)
    def ui_invite(
        org_id: Annotated[str, Form()],
        actor: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Mint an onboarding invite and show the exact `manthana setup` line.

        The engineer redeems the code for a team token at setup, so the token
        itself never travels — only the invite. Bound to one email = single use;
        blank = an open, multi-use team invite.
        """
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
        org_id = _scope_org(sess, org_id)  # a founder is forced to their own org
        bound_actor = actor.strip() or None
        code = secrets.token_urlsafe(8)
        expires = datetime.now(UTC) + timedelta(days=14)
        store.create_invite(
            code, org_id=org_id, team_id="core", actor=bound_actor,
            uses=1 if bound_actor else 10_000, expires_at=expires,
        )
        line = f"manthana setup {encode_invite(config.public_url, code)}"
        bound = (
            f" for <b>{_e(bound_actor)}</b>" if bound_actor
            else " (shared — anyone on the team)"
        )
        body = (
            f"<h3>Invite created{bound}</h3>"
            "<p>Send them this one line. That is their entire onboarding — it "
            "installs nothing you have to explain, and their sessions start flowing "
            "into the wiki once they run it.</p>"
            f"<pre>{_e(line)}</pre>"
            "<p class='muted'>Expires in 14 days. "
            + ("Single-use." if bound_actor else "Reusable by the whole team.")
            + " Manage or revoke it from the console.</p>"
            "<p><a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page("Invite", body))

    @app.post("/ui/invite/revoke", response_class=HTMLResponse)
    def ui_invite_revoke(
        org_id: Annotated[str, Form()],
        code: Annotated[str, Form()],
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
        # Scope BOTH the claimed org and the store call: a founder can only revoke
        # within their own org, and revoke_invite re-checks the code belongs to it.
        org_id = _scope_org(sess, org_id)
        store.revoke_invite(code, org_id=org_id)
        return RedirectResponse(url="/ui", status_code=303)

    @app.post("/ui/engineer-token", response_class=HTMLResponse)
    def ui_engineer_token(
        org_id: Annotated[str, Form()],
        actor: Annotated[str, Form()],
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Mint an engineer's wiki login and show the exact link to send them.

        The token is displayed ONCE and never stored server-side (it is a signed
        JWT, so there is nothing to store) — the page says so, because a founder
        who assumes they can come back for it later will be stuck re-minting.
        """
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
        org_id = _scope_org(sess, org_id)
        actor = actor.strip()
        if not actor:
            return HTMLResponse(
                _page("Team access", "<p class='warn'>An engineer email is required.</p>"
                      "<p><a href='/ui'>← console</a></p>"),
                status_code=422,
            )
        token = issue_engineer_token(config.jwt_secret, org_id=org_id, actor=actor)
        body = (
            f"<h3>Wiki login for {_e(actor)}</h3>"
            "<p>Send them this token and the link. Sign-in is the token itself — "
            "there is no password.</p>"
            f"<p><b>Link:</b> <code>{_e(config.public_url)}/ui/login</code></p>"
            f"<p><b>Token:</b></p><pre>{_e(token)}</pre>"
            "<p class='warn'>Shown once — it is not stored anywhere. If it is lost, "
            "just create another; both keep working.</p>"
            "<p class='muted'>They will land on the team wiki and can correct anything "
            "they know is wrong. Their edits are recorded under their own name.</p>"
            "<p><a href='/ui'>← console</a></p>"
        )
        return HTMLResponse(_page("Team access", body))

    @app.post("/ui/query", response_class=HTMLResponse)
    def ui_query(
        org_id: Annotated[str, Form()],
        query: Annotated[str, Form()],
        source: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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
        sess = _founder_session(manthana_admin)
        if isinstance(sess, Response):
            return sess
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


__all__ = ["mount_ui", "COOKIE", "session_for", "scope_org"]
