"""JSON twin of the signup flow, for the React client.

Same split as ``wiki_api.py`` is to the wiki: ``signup.py`` owns what must run
server-side — the Google handshake, cookie setting, provisioning — and this module
exposes it to the browser. Neither renders HTML.

AUTHENTICATION works without anything new. The console cookie is scoped
``path='/ui'``, so a React page served from the domain root still sends it on calls
to ``/ui/api/signup/*``. That is exactly how the wiki client already talks to the
server, and it is why moving these pages into React needed no change to the auth
model at all.

CSRF is already handled: ``hardening.py`` rejects any write under ``/ui/api/`` that
does not arrive as ``application/json``, and an HTML form cannot send that content
type. Combined with the ``samesite=lax`` cookie, a cross-site page can neither
forge these calls nor read their replies.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Cookie, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from manthana.schemas import encode_invite
from pydantic import BaseModel

from .auth import issue_founder_token
from .config import ServerConfig
from .signup import (
    INVITE_DAYS,
    PENDING_COOKIE,
    domain_org_for,
    join_domain_org,
    landing_path,
    live_invite,
    provision_org,
    read_pending,
    session_token,
    set_session_cookie,
    suggested_org_name,
)
from .store import ServerStore
from .ui import session_for

_log = logging.getLogger(__name__)

API = "/ui/api/signup"

#: The install line an engineer runs. Lives here rather than in the client so the
#: release URL is owned by the thing that knows the release exists.
INSTALL_LINE = (
    "curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/"
    "install.sh | sh"
)


class CreateOrgBody(BaseModel):
    org_name: str = ""


class JoinOrgBody(BaseModel):
    org_id: str = ""


def mount_signup_api(app: FastAPI, config: ServerConfig, store: ServerStore) -> None:
    """Mount the signup JSON API. Guarded by the same flag as ``mount_signup``."""

    def _org_name(org_id: str) -> str:
        org = store.get_org(org_id)
        return org.name if org is not None else org_id

    # ── the in-flight signup ──────────────────────────────────────────────
    @app.get(f"{API}/pending")
    def pending(manthana_signup_pending: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        """The verified Google profile behind the pending cookie, plus whether their
        work domain already belongs to an org.

        A 401 here means the cookie expired mid-decision, which is a normal state
        rather than a failure — the client sends them back to start again.
        """
        p = read_pending(config, manthana_signup_pending)
        if p is None:
            raise HTTPException(status_code=401, detail="sign-in expired")
        existing = domain_org_for(store, p.email)
        return {
            "email": p.email,
            "display_name": p.display_name or None,
            "suggested_org_name": suggested_org_name(p.email),
            # Present only for a work domain someone already claimed. Personal
            # addresses never match, so they always create their own org.
            "join_org_id": existing,
            "join_org_name": _org_name(existing) if existing else None,
        }

    @app.post(f"{API}/create")
    def create(
        body: CreateOrgBody, manthana_signup_pending: Annotated[str, Cookie()] = ""
    ) -> Response:
        p = read_pending(config, manthana_signup_pending)
        if p is None:
            raise HTTPException(status_code=401, detail="sign-in expired")
        org_id = provision_org(store, p, body.org_name)
        if org_id is None:
            raise HTTPException(
                status_code=409, detail="Please pick a different organization name."
            )
        resp = JSONResponse({"org_id": org_id, "next": "/welcome"})
        resp.delete_cookie(PENDING_COOKIE, path="/ui")
        return set_session_cookie(
            config, resp, session_token(config, org_id=org_id, role="founder", actor=p.email)
        )

    @app.post(f"{API}/join")
    def join(
        body: JoinOrgBody, manthana_signup_pending: Annotated[str, Cookie()] = ""
    ) -> Response:
        """Join the org that owns your email domain, as an ENGINEER.

        Never a founder: controlling a domain is not authorisation to read the
        company's costs and every engineer's activity. A founder promotes them.
        """
        p = read_pending(config, manthana_signup_pending)
        if p is None:
            raise HTTPException(status_code=401, detail="sign-in expired")
        org_id = join_domain_org(store, p, body.org_id)
        if org_id is None:
            raise HTTPException(
                status_code=400, detail="That organization is not open to your email domain."
            )
        resp = JSONResponse({"org_id": org_id, "next": "/home"})
        resp.delete_cookie(PENDING_COOKIE, path="/ui")
        return set_session_cookie(
            config, resp, session_token(config, org_id=org_id, role="engineer", actor=p.email)
        )

    # ── invite links ──────────────────────────────────────────────────────
    @app.get(f"{API}/invite")
    def invite(code: str = Query(default="")) -> dict[str, Any]:
        """Who is inviting you. UNAUTHENTICATED by design — the code is the
        credential, exactly as at ``/v1/enroll``. Only the org's display name is
        returned, and only for a code the caller already holds.

        The code is checked for SHAPE here and consumed later, atomically, once
        Google has confirmed who the person is.
        """
        row = store.get_invite(code) if code else None
        if row is None:
            raise HTTPException(status_code=404, detail="That invitation link is not valid.")
        return {"org_name": _org_name(row.org_id)}

    @app.get(f"{API}/conflict")
    def conflict(
        code: str = Query(default=""), manthana_admin: Annotated[str, Cookie()] = ""
    ) -> dict[str, Any]:
        """Why an invite could not be honoured: the reader already belongs to a
        different org, and one account belongs to one org.

        Both names are resolved server-side from the session and the invite rather
        than passed through the URL, so the page cannot be made to claim someone
        belongs somewhere they do not.
        """
        sess = session_for(config, manthana_admin, store)
        if sess is None or sess.org_id is None:
            raise HTTPException(status_code=401, detail="not signed in")
        row = store.get_invite(code) if code else None
        if row is None:
            raise HTTPException(status_code=404, detail="That invitation link is not valid.")
        return {
            "your_org_name": _org_name(sess.org_id),
            "invited_org_name": _org_name(row.org_id),
            "continue_to": landing_path(sess.role),
        }

    # ── the page that replaced the hand-written welcome email ─────────────
    @app.get(f"{API}/welcome")
    def welcome(manthana_admin: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        sess = session_for(config, manthana_admin, store)
        if sess is None:
            raise HTTPException(status_code=401, detail="not signed in")
        if sess.is_engineer:
            # Engineers have no team to set up; this page is the founder's.
            raise HTTPException(status_code=403, detail="founders only")
        if sess.org_id is None:
            raise HTTPException(status_code=400, detail="admin session has no single org")

        code = live_invite(store, sess.org_id)
        return {
            "org_id": sess.org_id,
            "org_name": _org_name(sess.org_id),
            "install_line": INSTALL_LINE,
            "setup_line": f"manthana setup {encode_invite(config.public_url, code)}",
            "join_url": f"{config.public_url}/ui/join?code={code}",
            "invite_days": INVITE_DAYS,
            "session_days": config.session_days,
        }

    @app.post(f"{API}/api-token")
    def api_token(manthana_admin: Annotated[str, Cookie()] = "") -> dict[str, Any]:
        """Mint the long-lived founder credential, deliberately and on request.

        This is the credential that used to be emailed at onboarding. Separating it
        from the browser session is the point: a stolen cookie dies in
        ``session_days``, while this one is created knowingly and can be killed with
        ``manthana-server revoke-token``.
        """
        sess = session_for(config, manthana_admin, store)
        if sess is None or sess.is_engineer or sess.org_id is None:
            raise HTTPException(status_code=401, detail="founders only")
        token = issue_founder_token(config.jwt_secret, org_id=sess.org_id, expires_days=365)
        _log.info("api token minted for org=%s", sess.org_id)
        return {"token": token}


__all__ = ["mount_signup_api", "API", "INSTALL_LINE"]
