"""Authentication: team-scoped JWTs for agents, org-scoped JWTs for founders,
and a static admin token for the operator.

v1 mechanism (decisions doc): JWT + team-scoped tokens; admin bootstraps tokens.
An agent token carries org/team/actor. A founder token (hosted multi-tenant)
carries only the org — it grants that org's console/query view and nothing else.
The two scopes are mutually exclusive: agent endpoints reject founder tokens and
vice versa. The operator's admin token retains cross-org access.

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


__all__ = [
    "TeamClaims",
    "FounderClaims",
    "AuthError",
    "issue_team_token",
    "verify_team_token",
    "issue_founder_token",
    "verify_founder_token",
    "ALGORITHM",
]
