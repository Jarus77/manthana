"""Authentication: team-scoped JWTs for agents, org-scoped JWTs for founders and
engineers, and a static admin token for the operator.

v1 mechanism (decisions doc): JWT + team-scoped tokens; admin bootstraps tokens.

  * **agent** — carries org/team/actor; the laptop daemon's sync credential.
  * **founder** — carries only the org; grants that org's full console.
  * **engineer** — carries org AND actor; grants the org WIKI only (read + teach),
    not the founder's oversight surfaces. Separate from the agent scope on
    purpose: a sync credential sitting in a config file on a laptop should not
    also be a browser login, and the console needs a human identity to attribute
    edits to, which is what makes team teaching auditable.

The scopes are mutually exclusive — each ``verify_*`` rejects the others — so a
token can never be replayed against a surface it was not issued for. The
operator's admin token retains cross-org access.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"


@dataclass(frozen=True)
class TeamClaims:
    actor: str
    org_id: str
    team_id: str


@dataclass(frozen=True)
class FounderClaims:
    org_id: str


@dataclass(frozen=True)
class EngineerClaims:
    """A named human in one org, for the wiki console."""

    org_id: str
    actor: str


class AuthError(Exception):
    """Raised on invalid/expired tokens."""


def issue_team_token(
    secret: str, *, org_id: str, team_id: str, actor: str, expires_days: int = 365
) -> str:
    payload = {
        "sub": actor,
        "org": org_id,
        "team": team_id,
        "scope": "agent",
        "exp": datetime.now(UTC) + timedelta(days=expires_days),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_team_token(secret: str, token: str) -> TeamClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "org", "team"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get("scope") != "agent":
        raise AuthError("not an agent token")
    try:
        return TeamClaims(actor=payload["sub"], org_id=payload["org"], team_id=payload["team"])
    except KeyError as exc:
        raise AuthError(f"missing claim: {exc}") from exc


def issue_founder_token(secret: str, *, org_id: str, expires_days: int = 365) -> str:
    payload = {
        "sub": "founder",
        "org": org_id,
        "scope": "founder",
        "exp": datetime.now(UTC) + timedelta(days=expires_days),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_founder_token(secret: str, token: str) -> FounderClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "org"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get("scope") != "founder":
        raise AuthError("not a founder token")
    return FounderClaims(org_id=payload["org"])


def issue_engineer_token(
    secret: str, *, org_id: str, actor: str, expires_days: int = 365
) -> str:
    """A named engineer's WIKI login. Carries the actor so every note they write
    is attributable to a person rather than to a shared role."""
    if not actor:
        raise ValueError("engineer token requires an actor")
    payload = {
        "sub": actor,
        "org": org_id,
        "scope": "engineer",
        "exp": datetime.now(UTC) + timedelta(days=expires_days),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_engineer_token(secret: str, token: str) -> EngineerClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "org"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get("scope") != "engineer":
        raise AuthError("not an engineer token")
    return EngineerClaims(org_id=payload["org"], actor=payload["sub"])


@dataclass(frozen=True)
class OAuthState:
    """The CSRF token for one in-flight Google sign-in.

    Carries the ``nonce`` (echoed in a cookie, so a callback forged by another
    site cannot match) and, when the user arrived through a join link, the
    ``invite`` code they should be enrolled against — the invite must survive the
    round trip to Google, and putting it in a signed state is what stops a
    stranger from swapping in someone else's code on the way back.

    Deliberately short-lived (minutes): it is a hop, not a session.
    """

    nonce: str
    invite: str = ""


def issue_oauth_state(
    secret: str, *, nonce: str, invite: str = "", expires_minutes: int = 10
) -> str:
    payload = {
        "nonce": nonce,
        "invite": invite,
        "scope": "oauth_state",
        "exp": datetime.now(UTC) + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_oauth_state(secret: str, token: str) -> OAuthState:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "nonce"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    # The scope check is what keeps this out of session_for's reach: a state
    # token is signed with the same secret, so without it a forged state could
    # be presented as a login cookie.
    if payload.get("scope") != "oauth_state":
        raise AuthError("not an oauth state token")
    return OAuthState(nonce=payload["nonce"], invite=payload.get("invite", ""))


@dataclass(frozen=True)
class PendingSignup:
    """A Google profile we have VERIFIED but not yet placed in an org — held for the
    few seconds between the callback and the human choosing create-or-join.

    It gets its own scope rather than reusing ``oauth_state``, and that separation
    is a security boundary, not tidiness: both are signed with the same secret, so
    if they shared a scope an attacker could start their own sign-in, take the
    resulting valid state token, and POST it as a pending cookie — handing
    themselves an arbitrary attacker-chosen email, and with it the power to claim
    another company's domain.
    """

    identity_id: str
    email: str
    display_name: str = ""


def issue_pending_signup(
    secret: str,
    *,
    identity_id: str,
    email: str,
    display_name: str = "",
    expires_minutes: int = 30,
) -> str:
    payload = {
        "sub": identity_id,
        "email": email,
        "name": display_name,
        "scope": "signup_pending",
        "exp": datetime.now(UTC) + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_pending_signup(secret: str, token: str) -> PendingSignup:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "email"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get("scope") != "signup_pending":
        raise AuthError("not a pending-signup token")
    return PendingSignup(
        identity_id=payload["sub"],
        email=payload["email"],
        display_name=payload.get("name", "") or "",
    )


__all__ = [
    "TeamClaims",
    "FounderClaims",
    "EngineerClaims",
    "OAuthState",
    "PendingSignup",
    "AuthError",
    "issue_team_token",
    "verify_team_token",
    "issue_founder_token",
    "verify_founder_token",
    "issue_engineer_token",
    "verify_engineer_token",
    "issue_oauth_state",
    "verify_oauth_state",
    "issue_pending_signup",
    "verify_pending_signup",
    "ALGORITHM",
]
