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


__all__ = [
    "TeamClaims",
    "FounderClaims",
    "EngineerClaims",
    "AuthError",
    "issue_team_token",
    "verify_team_token",
    "issue_founder_token",
    "verify_founder_token",
    "issue_engineer_token",
    "verify_engineer_token",
    "ALGORITHM",
]
