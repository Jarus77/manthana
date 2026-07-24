"""Self-serve onboarding: a founder signs in with Google and their org provisions
itself, with no operator in the loop.

WHY THIS EXISTS. Onboarding used to be: the operator runs ``onboard-org`` with the
admin token, then emails a welcome block containing a 365-day founder JWT that the
founder pastes into a password box. That is one human keyboard per customer, and a
permanent credential living in an inbox forever. This module is the other half of
the model — **identity for humans, tokens for machines**:

  * humans sign in (Google) and get a SHORT session;
  * machines keep using the invite/token flow, which is already frictionless
    (``manthana setup mia_…``) and is not touched here.

WHAT IT DOES NOT CHANGE. ``session_for`` (ui.py) keeps its exact contract: the
cookie still holds a scoped JWT and is still resolved statelessly. OAuth is simply a
third way to OBTAIN that cookie, alongside pasting a token and the admin secret. The
``onboard-org`` CLI is untouched and remains the path for sales-led onboarding.

ROLES ON JOIN. Somebody joining an existing org always lands as an ENGINEER (wiki
read + teach), never a founder. Controlling an email domain is not authorisation to
read the company's costs, audit trail, and every engineer's activity — an intern's
account would otherwise see all of it. A founder promotes them from the console.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import re
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Cookie, FastAPI, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from manthana.schemas import encode_invite

from .auth import (
    AuthError,
    PendingSignup,
    issue_engineer_token,
    issue_founder_token,
    issue_oauth_state,
    issue_pending_signup,
    verify_oauth_state,
    verify_pending_signup,
)
from .config import ServerConfig
from .store import ServerStore
from .ui import _STYLE, COOKIE, _e, _page, session_for

_log = logging.getLogger(__name__)

#: Cookie holding the in-flight sign-in state (nonce + optional invite). Scoped to
#: the OAuth routes and deleted the moment the callback consumes it.
STATE_COOKIE = "manthana_oauth_state"
#: Cookie holding the verified Google profile between the callback and the
#: create-or-join choice, for the few seconds the human is deciding. It is a signed
#: state token too, so nothing here is client-forgeable.
PENDING_COOKIE = "manthana_signup_pending"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Email providers where the domain says nothing about WHICH company you belong to.
#: Used ONLY to decide whether to offer "join the existing org for this domain" —
#: never to block a signup. Anyone may sign up with any address; personal-email
#: users simply always create their own org and join others via an invite link.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "msn.com", "yahoo.com", "yahoo.co.in", "ymail.com", "proton.me",
        "protonmail.com", "pm.me", "icloud.com", "me.com", "mac.com", "aol.com",
        "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com", "fastmail.com",
        "hey.com", "tutanota.com", "duck.com", "rediffmail.com",
    }
)

#: How long a self-serve org's shared engineer invite stays valid.
INVITE_DAYS = 14
#: Effectively "as many engineers as you like" — matches the operator's --open path.
INVITE_USES = 10_000


def is_public_domain(domain: str) -> bool:
    return domain.strip().lower() in PUBLIC_EMAIL_DOMAINS


def _expires_at(invite: Any) -> datetime:
    """An invite's expiry as a UTC-aware datetime. Stored values carry an offset,
    but a naive one is treated as UTC rather than raising when compared to now()."""
    dt = datetime.fromisoformat(invite.expires_at)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def slugify(value: str) -> str:
    """Org display name → a URL/id-safe org_id. Empty result falls back to 'org',
    because an org id is a primary key and must never be blank."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:40] or "org"


#: Distinct orgs we will try to name from one slug before giving up. Only a
#: pathological number of same-named signups reaches this; the bound exists so a
#: bug can never spin here forever.
_MAX_ORG_ID_ATTEMPTS = 200


def claim_org_id(store: ServerStore, base: str, name: str) -> str | None:
    """Create the org under the first free ``base``, ``base-2``, ``base-3``… and
    return the id that was actually taken, or None if the space is exhausted.

    The CREATE is what arbitrates, not a preceding read. Two unrelated companies
    really can both call themselves "acme", and checking `get_org` first and
    inserting after leaves a window in which two simultaneous signups both see the
    same id free and the second silently takes over the first's tenant. So each
    candidate is attempted as an insert that fails if the id exists.
    """
    for n in range(1, _MAX_ORG_ID_ATTEMPTS + 1):
        candidate = base if n == 1 else f"{base}-{n}"
        if store.create_org_if_absent(candidate, name):
            return candidate
    return None


def default_team_id(org_id: str) -> str:
    """Namespaced, and this is load-bearing rather than cosmetic: ``TeamRow.id`` is
    a GLOBAL primary key and ``create_team`` upserts on it, so giving every
    self-serve org a team literally called "core" would make each new signup
    overwrite the previous org's team row."""
    return f"{org_id}-core"


def exchange_code_for_profile(
    config: ServerConfig, code: str, redirect_uri: str
) -> dict[str, Any]:
    """Trade an authorization code for the caller's Google profile.

    Returns ``{"sub", "email", "email_verified", "name"}``.

    The ``id_token`` is decoded WITHOUT signature verification, deliberately: we
    received it ourselves, directly from Google's token endpoint, over TLS, in a
    server-to-server request. OpenID Connect §3.1.3.7 explicitly permits skipping
    verification in exactly this case, and doing so keeps us out of the business of
    fetching, caching, and rotating Google's JWKS. (This would NOT be safe for an
    id_token arriving from a browser — that one is attacker-supplied.)
    """
    import httpx
    import jwt

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise AuthError(f"google token exchange failed ({resp.status_code})")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise AuthError("google response carried no id_token")
    claims = jwt.decode(id_token, options={"verify_signature": False, "verify_exp": True})
    if not claims.get("sub") or not claims.get("email"):
        raise AuthError("google profile is missing sub/email")
    return claims


def mount_signup(app: FastAPI, config: ServerConfig, store: ServerStore) -> None:
    """Mount the self-serve routes. Called only when the feature flag is on, so a
    self-hosted deploy has no signup surface at all."""

    redirect_uri = f"{config.public_url}/ui/auth/google/callback"

    def _set_session(resp: Response, token: str) -> Response:
        """Same cookie contract as ui.py's token login — name, path, and flags must
        match exactly or the console will not see the session."""
        resp.set_cookie(
            COOKIE, token, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure,
        )
        return resp

    def _session_token(org_id: str, role: str, actor: str) -> str:
        """A browser session for one member. Founders get the org-scoped console
        token; engineers get the wiki token that names them, so their edits stay
        attributable. Both expire in ``session_days``, not a year."""
        if role == "founder":
            return issue_founder_token(
                config.jwt_secret, org_id=org_id, expires_days=config.session_days
            )
        return issue_engineer_token(
            config.jwt_secret, org_id=org_id, actor=actor, expires_days=config.session_days
        )

    def _land(role: str) -> str:
        """Where a RETURNING member goes. Founders get the console, not the welcome
        page: that page is onboarding, right exactly once. It stays reachable from
        the console for whenever they need the install lines again."""
        return "/ui" if role == "founder" else "/ui/home"

    def _sign_in_page(title: str, lead: str, invite: str = "") -> str:
        target = "/ui/auth/google"
        if invite:
            target += f"?invite={urllib.parse.quote(invite)}"
        return _shell(
            title,
            f"{lead}"
            f"<p><a class='cta' href='{_e(target)}'>Sign in with Google</a></p>"
            "<p class='muted'>We only ever read your name and email address.</p>",
        )

    def _shell(title: str, body: str) -> str:
        """A page for people who are NOT signed in yet — so, unlike ui.py's _page,
        it carries no console nav and no log-out button."""
        return (
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Manthana — {_e(title)}</title>{_STYLE}"
            "<style>a.cta{display:inline-block;background:#1a73e8;color:#fff;padding:8px 16px;"
            "border-radius:6px;text-decoration:none}a.cta:hover{background:#1557b0}"
            ".card{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}"
            "</style></head><body><h1>Manthana</h1>"
            f"{body}</body></html>"
        )

    def _fail(title: str, message: str, status: int = 400) -> Response:
        return HTMLResponse(
            _shell(title, f"<p class='warn'>{_e(message)}</p>"
                          "<p><a href='/ui/signup'>← start again</a></p>"),
            status_code=status,
        )

    # ── entry points ──────────────────────────────────────────────────────
    @app.get("/ui/signup", response_class=HTMLResponse)
    def signup_page() -> Response:
        return HTMLResponse(
            _sign_in_page(
                "Get started",
                "<p>Create your organization in about a minute. You'll get a command "
                "to send your engineers — nothing to configure.</p>",
            )
        )

    @app.get("/ui/join", response_class=HTMLResponse)
    def join_page(code: str = Query(default="")) -> Response:
        """The browser twin of ``manthana setup mia_…``: the link a founder shares
        with someone whose email domain can't identify their org (anyone on a
        personal address). The code is validated for SHAPE only here — it is
        consumed atomically after Google confirms who the person is."""
        invite = store.get_invite(code) if code else None
        if invite is None:
            return _fail("Invitation", "That invitation link is not valid.", status=404)
        org = store.get_org(invite.org_id)
        org_name = org.name if org is not None else invite.org_id
        return HTMLResponse(
            _sign_in_page(
                "Join a team",
                f"<p>You've been invited to <b>{_e(org_name)}</b> on Manthana.</p>",
                invite=code,
            )
        )

    # ── the Google round trip ─────────────────────────────────────────────
    @app.get("/ui/auth/google")
    def auth_start(invite: str = Query(default="")) -> Response:
        nonce = secrets.token_urlsafe(16)
        state = issue_oauth_state(config.jwt_secret, nonce=nonce, invite=invite)
        params = {
            "client_id": config.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # Always show the chooser: people onboarding a work org are very often
            # signed into a personal Google account in the same browser, and
            # silently using it would put the org under the wrong identity.
            "prompt": "select_account",
        }
        resp = RedirectResponse(
            url=f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}", status_code=303
        )
        resp.set_cookie(
            STATE_COOKIE, state, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure, max_age=600,
        )
        return resp

    @app.get("/ui/auth/google/callback")
    def auth_callback(
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
        manthana_oauth_state: Annotated[str, Cookie()] = "",
    ) -> Response:
        if error:
            return _fail("Sign-in", f"Google reported: {error}")
        if not code or not state:
            return _fail("Sign-in", "That sign-in link is incomplete.")
        # The state must be BOTH validly signed and the exact one we set as a
        # cookie. Signature alone is not enough: an attacker who starts their own
        # sign-in gets a validly signed state, and could otherwise replay it into
        # someone else's browser to log them into an account they control.
        if state != manthana_oauth_state:
            return _fail("Sign-in", "This sign-in could not be verified. Please try again.")
        try:
            st = verify_oauth_state(config.jwt_secret, state)
        except AuthError:
            return _fail("Sign-in", "This sign-in expired. Please try again.")
        try:
            profile = exchange_code_for_profile(config, code, redirect_uri)
        except AuthError as exc:
            _log.warning("google sign-in failed: %s", exc)
            return _fail("Sign-in", "We could not complete sign-in with Google.")
        if not profile.get("email_verified", False):
            return _fail("Sign-in", "That Google account has no verified email address.")

        email = str(profile["email"]).strip().lower()
        identity_id = f"google:{profile['sub']}"
        display_name = profile.get("name") or None
        domain = email.rpartition("@")[2]

        # 1. Someone we already know — straight back to where they belong.
        known = store.get_identity(identity_id)
        if known is not None:
            store.upsert_identity(
                identity_id, email=email, org_id=known.org_id, role=known.role,
                display_name=display_name,
            )
            # They followed an invite into a DIFFERENT org. One account belongs to
            # one org, so we cannot honour it — but silently landing them back in
            # their own org looks like the link was broken. Say what happened, and
            # leave their existing session alone rather than half-signing them in.
            if st.invite:
                other = store.get_invite(st.invite)
                if other is not None and other.org_id != known.org_id:
                    mine = store.get_org(known.org_id)
                    theirs = store.get_org(other.org_id)
                    resp = HTMLResponse(
                        _shell(
                            "Already in a team",
                            f"<p>{_e(email)} is already part of "
                            f"<b>{_e(mine.name if mine else known.org_id)}</b>, so it "
                            f"can't also join <b>"
                            f"{_e(theirs.name if theirs else other.org_id)}</b> — one "
                            "account belongs to one organization.</p>"
                            "<p class='muted'>To join with a separate identity, sign in "
                            "with a different Google account. The invitation is "
                            "untouched and still works.</p>"
                            f"<p><a class='cta' href='{_e(_land(known.role))}'>"
                            "Continue to your team</a></p>",
                        ),
                        status_code=409,
                    )
                    resp.delete_cookie(STATE_COOKIE, path="/ui")
                    return _set_session(resp, _session_token(known.org_id, known.role, email))
            resp = RedirectResponse(url=_land(known.role), status_code=303)
            resp.delete_cookie(STATE_COOKIE, path="/ui")
            return _set_session(resp, _session_token(known.org_id, known.role, email))

        # 2. Arrived through a join link — consume the invite now that we know who
        #    they are. Atomic, so a shared link can't outlive its use count.
        if st.invite:
            invite = store.get_invite(st.invite)
            if invite is None or store.redeem_invite(st.invite) is None:
                return _fail("Invitation", "That invitation has expired or been used up.")
            store.upsert_actor(email, invite.org_id, invite.team_id, display_name)
            store.upsert_identity(
                identity_id, email=email, org_id=invite.org_id, role="engineer",
                display_name=display_name,
            )
            resp = RedirectResponse(url="/ui/home", status_code=303)
            resp.delete_cookie(STATE_COOKIE, path="/ui")
            return _set_session(resp, _session_token(invite.org_id, "engineer", email))

        # 3. New person. Hold the verified profile in a signed cookie while they
        #    choose, then show create-or-join.
        pending = issue_pending_signup(
            config.jwt_secret,
            identity_id=identity_id,
            email=email,
            display_name=display_name or "",
        )
        existing = None if is_public_domain(domain) else store.get_org_by_domain(domain)
        resp = HTMLResponse(_choice_page(email, domain, existing, display_name))
        resp.delete_cookie(STATE_COOKIE, path="/ui")
        resp.set_cookie(
            PENDING_COOKIE, pending, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure, max_age=1800,
        )
        return resp

    def _choice_page(
        email: str, domain: str, existing_org: str | None, display_name: str | None
    ) -> str:
        who = display_name or email
        suggested = (
            domain.rpartition(".")[0].replace("-", " ").title()
            if not is_public_domain(domain)
            else (email.partition("@")[0].replace(".", " ").title())
        )
        create = (
            "<div class='card'><h3>Create a new organization</h3>"
            "<form method='post' action='/ui/signup/create'>"
            f"<p>Name: <input name='org_name' value='{_e(suggested)}' required></p>"
            "<button>Create organization</button></form></div>"
        )
        if existing_org is None:
            return _shell(
                "Welcome",
                f"<p>Signed in as <b>{_e(who)}</b>.</p>{create}",
            )
        org = store.get_org(existing_org)
        org_name = org.name if org is not None else existing_org
        return _shell(
            "Welcome",
            f"<p>Signed in as <b>{_e(who)}</b>.</p>"
            f"<div class='card'><h3>Join {_e(org_name)}</h3>"
            f"<p>Someone from <b>{_e(domain)}</b> is already using Manthana. Join them "
            "and you'll get access to the team wiki — a founder can give you the full "
            "console afterwards.</p>"
            "<form method='post' action='/ui/signup/join'>"
            f"<input type='hidden' name='org_id' value='{_e(existing_org)}'>"
            f"<button>Join {_e(org_name)}</button></form></div>"
            f"{create}"
        )

    def _pending(cookie: str) -> PendingSignup | None:
        """The verified Google profile from the pending-signup cookie, or None."""
        if not cookie:
            return None
        try:
            return verify_pending_signup(config.jwt_secret, cookie)
        except AuthError:
            return None

    # ── provisioning ──────────────────────────────────────────────────────
    @app.post("/ui/signup/create")
    def signup_create(
        org_name: Annotated[str, Form()] = "",
        manthana_signup_pending: Annotated[str, Cookie()] = "",
    ) -> Response:
        pending = _pending(manthana_signup_pending)
        if pending is None:
            return _fail("Sign-up", "Your sign-in expired. Please start again.")
        identity_id, email = pending.identity_id, pending.email
        name = org_name.strip() or email.partition("@")[0]

        org_id = claim_org_id(store, slugify(name), name)
        if org_id is None:
            return _fail("Sign-up", "Please pick a different organization name.", status=409)
        team_id = default_team_id(org_id)
        store.create_team(team_id, org_id, "core")
        # NO OrgQuotaRow is written: an absent row means the org falls back to
        # config.llm_monthly_cap_usd, which defaults to 0 = unlimited. That is the
        # current deliberate policy (grow first, meter later); to reintroduce caps,
        # set MANTHANA_SERVER_LLM_MONTHLY_CAP_USD or write a row here.
        domain = email.rpartition("@")[2]
        if not is_public_domain(domain):
            store.claim_domain(domain, org_id)
        who = pending.display_name or None
        store.upsert_actor(email, org_id, team_id, who)
        store.upsert_identity(
            identity_id, email=email, org_id=org_id, role="founder", display_name=who
        )
        _log.info("self-serve org created: org=%s by=%s", org_id, email)

        resp = RedirectResponse(url="/ui/welcome", status_code=303)
        resp.delete_cookie(PENDING_COOKIE, path="/ui")
        return _set_session(resp, _session_token(org_id, "founder", email))

    @app.post("/ui/signup/join")
    def signup_join(
        org_id: Annotated[str, Form()] = "",
        manthana_signup_pending: Annotated[str, Cookie()] = "",
    ) -> Response:
        pending = _pending(manthana_signup_pending)
        if pending is None:
            return _fail("Sign-up", "Your sign-in expired. Please start again.")
        identity_id, email = pending.identity_id, pending.email
        # Re-derive the org from the email domain rather than trusting the form:
        # otherwise anyone could post an arbitrary org_id and join a tenant they
        # have no relationship with.
        domain = email.rpartition("@")[2]
        owner = None if is_public_domain(domain) else store.get_org_by_domain(domain)
        if owner is None or owner != org_id:
            return _fail("Sign-up", "That organization is not open to your email domain.")
        teams = store.list_teams(owner)
        team_id = teams[0].id if teams else default_team_id(owner)
        who = pending.display_name or None
        store.upsert_actor(email, owner, team_id, who)
        store.upsert_identity(
            identity_id, email=email, org_id=owner, role="engineer", display_name=who
        )
        resp = RedirectResponse(url="/ui/home", status_code=303)
        resp.delete_cookie(PENDING_COOKIE, path="/ui")
        return _set_session(resp, _session_token(owner, "engineer", email))

    # ── the page that replaces the hand-written welcome email ─────────────
    @app.get("/ui/welcome", response_class=HTMLResponse)
    def welcome(manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        sess = session_for(config, manthana_admin, store)
        if sess is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        if sess.is_engineer:
            return RedirectResponse(url="/ui/home", status_code=303)
        org_id = sess.org_id
        if org_id is None:  # admin session — no single org to show
            return RedirectResponse(url="/ui", status_code=303)
        return HTMLResponse(_welcome_body(org_id))

    def _live_invite(org_id: str) -> str:
        """The org's shared open invite, minted on demand. Reused while it is still
        valid so the founder can reload this page and hand out the same line."""
        now = datetime.now(UTC)
        for inv in store.list_invites(org_id):
            if inv.actor is None and inv.uses_left > 0 and _expires_at(inv) > now:
                return inv.code
        code = secrets.token_urlsafe(8)
        teams = store.list_teams(org_id)
        team_id = teams[0].id if teams else default_team_id(org_id)
        store.create_invite(
            code, org_id=org_id, team_id=team_id, actor=None, uses=INVITE_USES,
            expires_at=now + timedelta(days=INVITE_DAYS),
        )
        return code

    def _welcome_body(org_id: str) -> str:
        org = store.get_org(org_id)
        org_name = org.name if org is not None else org_id
        code = _live_invite(org_id)
        setup = f"manthana setup {encode_invite(config.public_url, code)}"
        install = (
            "curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/"
            "install.sh | sh"
        )
        join_url = f"{config.public_url}/ui/join?code={urllib.parse.quote(code)}"
        return _page(
            f"Welcome — {org_name}",
            f"<h2>{_e(org_name)} is ready</h2>"
            "<h3>1. Send this to your engineers</h3>"
            "<p class='muted'>Two lines on their laptop. No accounts, no configuration. "
            f"This invite is good for {INVITE_DAYS} days.</p>"
            f"<pre id='eng'>{_e(install)}\n{_e(setup)}</pre>"
            "<button onclick=\"navigator.clipboard.writeText("
            "document.getElementById('eng').innerText)\">Copy both lines</button>"
            "<h3>2. Or invite them to the wiki in a browser</h3>"
            "<p class='muted'>Same invite, for anyone who wants to read and correct the "
            "team's shared context without installing anything.</p>"
            f"<pre id='joinlink'>{_e(join_url)}</pre>"
            "<button onclick=\"navigator.clipboard.writeText("
            "document.getElementById('joinlink').innerText)\">Copy link</button>"
            "<h3>3. Connect Claude Code or scripts (optional)</h3>"
            "<p class='muted'>Your browser session lasts "
            f"{config.session_days} days. For the MCP gateway or the API you need a "
            "long-lived token — generate one when you need it.</p>"
            "<form method='post' action='/ui/api-token'>"
            "<button>Generate API token</button></form>"
            "<p style='margin-top:2rem'><a href='/ui'>Go to the console →</a></p>",
        )

    @app.post("/ui/api-token", response_class=HTMLResponse)
    def api_token(manthana_admin: Annotated[str, Cookie()] = "") -> Response:
        """Mint the long-lived founder credential, deliberately and on request.

        This is the credential that used to be emailed at onboarding. Separating it
        from the browser session is the point: a stolen cookie dies in
        ``session_days``, while this one is created knowingly and can be killed with
        ``manthana-server revoke-token``.
        """
        sess = session_for(config, manthana_admin, store)
        if sess is None or sess.is_engineer or sess.org_id is None:
            return RedirectResponse(url="/ui/login", status_code=303)
        token = issue_founder_token(config.jwt_secret, org_id=sess.org_id, expires_days=365)
        return HTMLResponse(
            _page(
                "API token",
                "<h3>Your API token</h3>"
                f"<pre>{_e(token)}</pre>"
                "<p class='warn'>Shown once — it is not stored anywhere. If you lose it, "
                "generate another; both keep working until revoked.</p>"
                "<p class='muted'>Use it as a bearer token against this server's API, or "
                "as the credential for the founder MCP gateway.</p>"
                "<p><a href='/ui/welcome'>← back</a></p>",
            )
        )

    # ── membership ────────────────────────────────────────────────────────
    @app.post("/ui/members/promote", response_class=HTMLResponse)
    def promote(
        identity_id: Annotated[str, Form()] = "",
        manthana_admin: Annotated[str, Cookie()] = "",
    ) -> Response:
        """Raise a joiner to founder. Founder-only, and scoped: ``set_identity_role``
        checks the target belongs to the caller's org, so this cannot reach across
        tenants even if a caller forges the id."""
        sess = session_for(config, manthana_admin, store)
        if sess is None or sess.is_engineer:
            return RedirectResponse(url="/ui/login", status_code=303)
        org_id = sess.org_id
        if org_id is None:
            return _fail("Members", "Sign in as a founder to manage members.", status=403)
        if not store.set_identity_role(identity_id.strip(), org_id, "founder"):
            return _fail("Members", "That member is not in your organization.", status=404)
        return RedirectResponse(url="/ui", status_code=303)


__all__ = [
    "mount_signup",
    "is_public_domain",
    "slugify",
    "claim_org_id",
    "default_team_id",
    "exchange_code_for_profile",
    "PUBLIC_EMAIL_DOMAINS",
    "STATE_COOKIE",
    "PENDING_COOKIE",
]
