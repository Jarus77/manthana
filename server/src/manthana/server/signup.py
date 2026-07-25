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

WHAT LIVES WHERE. This module owns the parts that must run server-side: the OAuth
handshake, cookie setting, and provisioning. It renders nothing — every page is a
React route in ``web/``, fed by the JSON endpoints in ``signup_api.py``. The
handshake cannot move: it talks to Google with the client secret and sets an
httponly cookie, neither of which a browser client can do.

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

from fastapi import Cookie, FastAPI, Query
from fastapi.responses import RedirectResponse, Response

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
from .ui import COOKIE

_log = logging.getLogger(__name__)

#: Cookie holding the in-flight sign-in state (nonce + optional invite). Scoped to
#: the OAuth routes and deleted the moment the callback consumes it.
STATE_COOKIE = "manthana_oauth_state"
#: Cookie holding the verified Google profile between the callback and the
#: create-or-join choice, for the few seconds the human is deciding. It is a signed
#: token too, so nothing here is client-forgeable.
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

#: Client routes the server hands people back to. Kept together because they are the
#: seam between the two halves of the app: if a route is renamed in `web/`, every
#: redirect that has to follow it is here.
CLIENT_SIGNUP = "/signup"
CLIENT_CHOOSE = "/signup/choose"
CLIENT_CONFLICT = "/signup/conflict"
CLIENT_JOIN = "/join"
CLIENT_WELCOME = "/welcome"


def is_public_domain(domain: str) -> bool:
    return domain.strip().lower() in PUBLIC_EMAIL_DOMAINS


def expires_at(invite: Any) -> datetime:
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


# ── shared server-side logic ───────────────────────────────────────────────
# Module-level rather than closures so signup_api.py can call exactly the same
# code. Provisioning must not exist twice: a second implementation is how the
# create path and the join path drift apart on who becomes a founder.


def session_token(config: ServerConfig, *, org_id: str, role: str, actor: str) -> str:
    """A browser session for one member. Founders get the org-scoped console token;
    engineers get the wiki token that names them, so their edits stay attributable.
    Both expire in ``session_days``, not a year."""
    if role == "founder":
        return issue_founder_token(
            config.jwt_secret, org_id=org_id, expires_days=config.session_days
        )
    return issue_engineer_token(
        config.jwt_secret, org_id=org_id, actor=actor, expires_days=config.session_days
    )


def set_session_cookie(config: ServerConfig, resp: Response, token: str) -> Response:
    """Same cookie contract as ui.py's token login — name, path, and flags must
    match exactly or the console will not see the session."""
    resp.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", path="/ui",
        secure=config.cookie_secure,
    )
    return resp


def landing_path(role: str) -> str:
    """Where a RETURNING member goes. Founders get the console, not the welcome
    page: that page is onboarding, right exactly once. It stays reachable from the
    console for whenever they need the install lines again."""
    return "/ui" if role == "founder" else "/ui/home"


def read_pending(config: ServerConfig, cookie: str) -> PendingSignup | None:
    """The verified Google profile from the pending-signup cookie, or None."""
    if not cookie:
        return None
    try:
        return verify_pending_signup(config.jwt_secret, cookie)
    except AuthError:
        return None


def live_invite(store: ServerStore, org_id: str) -> str:
    """The org's shared open invite, minted on demand. Reused while it is still
    valid so the founder can reload the welcome page and hand out the same line."""
    now = datetime.now(UTC)
    for inv in store.list_invites(org_id):
        if inv.actor is None and inv.uses_left > 0 and expires_at(inv) > now:
            return inv.code
    code = secrets.token_urlsafe(8)
    teams = store.list_teams(org_id)
    team_id = teams[0].id if teams else default_team_id(org_id)
    store.create_invite(
        code, org_id=org_id, team_id=team_id, actor=None, uses=INVITE_USES,
        expires_at=now + timedelta(days=INVITE_DAYS),
    )
    return code


def suggested_org_name(email: str) -> str:
    """A first guess at what to call the org, for the create form's default. From
    the work domain where there is one, otherwise from the person's own name —
    'gmail' would be a poor thing to call a company."""
    domain = email.rpartition("@")[2]
    if not is_public_domain(domain):
        return domain.rpartition(".")[0].replace("-", " ").title()
    return email.partition("@")[0].replace(".", " ").title()


def domain_org_for(store: ServerStore, email: str) -> str | None:
    """The org that already claimed this WORK domain, if any. Always None for a
    public provider — a gmail.com claim would put every personal signup into one
    enormous shared org."""
    domain = email.rpartition("@")[2]
    return None if is_public_domain(domain) else store.get_org_by_domain(domain)


def provision_org(
    store: ServerStore, pending: PendingSignup, org_name: str
) -> str | None:
    """Create a brand-new tenant for a verified person. Returns its org id, or None
    if the whole ``name``, ``name-2``, ``name-3``… space is exhausted."""
    email = pending.email
    name = org_name.strip() or email.partition("@")[0]

    org_id = claim_org_id(store, slugify(name), name)
    if org_id is None:
        return None
    team_id = default_team_id(org_id)
    store.create_team(team_id, org_id, "core")
    # NO OrgQuotaRow is written: an absent row means the org falls back to
    # config.llm_monthly_cap_usd. That is the current deliberate policy (grow
    # first, meter later); to give self-serve orgs their own cap, write a row here.
    domain = email.rpartition("@")[2]
    if not is_public_domain(domain):
        store.claim_domain(domain, org_id)
    who = pending.display_name or None
    store.upsert_actor(email, org_id, team_id, who)
    store.upsert_identity(
        pending.identity_id, email=email, org_id=org_id, role="founder", display_name=who
    )
    _log.info("self-serve org created: org=%s by=%s", org_id, email)
    return org_id


def join_domain_org(store: ServerStore, pending: PendingSignup, org_id: str) -> str | None:
    """Add a verified person to the org that owns their email domain, as an
    ENGINEER. Returns the org id joined, or None when their domain does not own the
    org they asked for.

    The org is re-derived from the verified email rather than trusted from the
    request: otherwise anyone could name an arbitrary org and join a tenant they
    have no relationship with.
    """
    owner = domain_org_for(store, pending.email)
    if owner is None or owner != org_id:
        return None
    teams = store.list_teams(owner)
    team_id = teams[0].id if teams else default_team_id(owner)
    who = pending.display_name or None
    store.upsert_actor(pending.email, owner, team_id, who)
    store.upsert_identity(
        pending.identity_id, email=pending.email, org_id=owner, role="engineer",
        display_name=who,
    )
    return owner


def redeem_invite_join(
    store: ServerStore, pending_email: str, identity_id: str, code: str,
    display_name: str | None,
) -> str | None:
    """Consume an invite and add the person to its org as an engineer. Returns the
    org id, or None when the invite is unknown, expired, or used up. Atomic, so a
    shared link cannot outlive its use count."""
    invite = store.get_invite(code)
    if invite is None or store.redeem_invite(code) is None:
        return None
    store.upsert_actor(pending_email, invite.org_id, invite.team_id, display_name)
    store.upsert_identity(
        identity_id, email=pending_email, org_id=invite.org_id, role="engineer",
        display_name=display_name,
    )
    return invite.org_id


def mount_signup(app: FastAPI, config: ServerConfig, store: ServerStore) -> None:
    """Mount the OAuth handshake and the redirects into the client. Called only when
    the feature flag is on, so a self-hosted deploy has no signup surface at all."""

    redirect_uri = f"{config.public_url}/ui/auth/google/callback"

    def _to(path: str) -> Response:
        return RedirectResponse(url=path, status_code=303)

    def _fail(reason: str) -> Response:
        """Hand the client a reason slug rather than an error page. The client owns
        the wording; this owns what went wrong."""
        return _to(f"{CLIENT_SIGNUP}?error={reason}")

    # ── legacy entry points ───────────────────────────────────────────────
    # These URLs are printed in welcome emails, pasted into Slack, and linked from
    # the console, so they keep working — they just hand off to the client now.
    @app.get("/ui/signup")
    def signup_page() -> Response:
        return _to(CLIENT_SIGNUP)

    @app.get("/ui/join")
    def join_page(code: str = Query(default="")) -> Response:
        return _to(f"{CLIENT_JOIN}?code={urllib.parse.quote(code)}" if code else CLIENT_JOIN)

    @app.get("/ui/welcome")
    def welcome_page() -> Response:
        return _to(CLIENT_WELCOME)

    # ── the Google round trip (must stay server-side) ─────────────────────
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
            return _fail("google")
        if not code or not state:
            return _fail("incomplete")
        # The state must be BOTH validly signed and the exact one we set as a
        # cookie. Signature alone is not enough: an attacker who starts their own
        # sign-in gets a validly signed state, and could otherwise replay it into
        # someone else's browser to log them into an account they control.
        if state != manthana_oauth_state:
            return _fail("state")
        try:
            st = verify_oauth_state(config.jwt_secret, state)
        except AuthError:
            return _fail("expired")
        try:
            profile = exchange_code_for_profile(config, code, redirect_uri)
        except AuthError as exc:
            _log.warning("google sign-in failed: %s", exc)
            return _fail("google")
        if not profile.get("email_verified", False):
            return _fail("unverified")

        email = str(profile["email"]).strip().lower()
        identity_id = f"google:{profile['sub']}"
        display_name = profile.get("name") or None

        # 1. Someone we already know — straight back to where they belong.
        known = store.get_identity(identity_id)
        if known is not None:
            store.upsert_identity(
                identity_id, email=email, org_id=known.org_id, role=known.role,
                display_name=display_name,
            )
            token = session_token(
                config, org_id=known.org_id, role=known.role, actor=email
            )
            # They followed an invite into a DIFFERENT org. One account belongs to
            # one org, so we cannot honour it — but silently landing them back in
            # their own org looks like the link was broken. Sign them in as
            # themselves and send them somewhere that explains it; the invite is
            # left untouched and still works for someone else.
            target = landing_path(known.role)
            if st.invite:
                other = store.get_invite(st.invite)
                if other is not None and other.org_id != known.org_id:
                    target = f"{CLIENT_CONFLICT}?code={urllib.parse.quote(st.invite)}"
            resp = _to(target)
            resp.delete_cookie(STATE_COOKIE, path="/ui")
            return set_session_cookie(config, resp, token)

        # 2. Arrived through a join link — consume the invite now that we know who
        #    they are.
        if st.invite:
            org_id = redeem_invite_join(
                store, email, identity_id, st.invite, display_name
            )
            if org_id is None:
                return _fail("invite")
            resp = _to("/ui/home")
            resp.delete_cookie(STATE_COOKIE, path="/ui")
            return set_session_cookie(
                config, resp, session_token(config, org_id=org_id, role="engineer", actor=email)
            )

        # 3. New person. Hold the verified profile in a signed cookie while they
        #    choose, then hand them to the create-or-join screen.
        pending = issue_pending_signup(
            config.jwt_secret,
            identity_id=identity_id,
            email=email,
            display_name=display_name or "",
        )
        resp = _to(CLIENT_CHOOSE)
        resp.delete_cookie(STATE_COOKIE, path="/ui")
        resp.set_cookie(
            PENDING_COOKIE, pending, httponly=True, samesite="lax", path="/ui",
            secure=config.cookie_secure, max_age=1800,
        )
        return resp


__all__ = [
    "mount_signup",
    "is_public_domain",
    "slugify",
    "claim_org_id",
    "default_team_id",
    "exchange_code_for_profile",
    "session_token",
    "set_session_cookie",
    "landing_path",
    "read_pending",
    "live_invite",
    "suggested_org_name",
    "domain_org_for",
    "provision_org",
    "join_domain_org",
    "redeem_invite_join",
    "expires_at",
    "PUBLIC_EMAIL_DOMAINS",
    "STATE_COOKIE",
    "PENDING_COOKIE",
    "INVITE_DAYS",
    "CLIENT_SIGNUP",
    "CLIENT_CHOOSE",
    "CLIENT_CONFLICT",
    "CLIENT_JOIN",
    "CLIENT_WELCOME",
]
