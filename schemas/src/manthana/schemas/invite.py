"""Onboarding invite blob — a copy-pasteable token wrapping {server_url, code}.

The admin's ``enroll`` emits ``manthana setup <blob>``; the engineer's ``setup`` decodes
it to know WHERE to redeem (``server_url``) and WHAT (``code``). The blob carries no
secret — the team token is only issued on redemption at ``POST /v1/enroll``. Lives in the
Apache ``manthana-schemas`` package so both the agent (Apache) and server (AGPL) share one
encoder/decoder.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import base64
import json

_PREFIX = "mia_"  # "manthana invite" — a recognizable, greppable marker


def encode_invite(server_url: str, code: str) -> str:
    """Pack (server_url, code) into a single URL-safe token string."""
    raw = json.dumps({"s": server_url.rstrip("/"), "c": code}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return _PREFIX + body


def decode_invite(blob: str) -> tuple[str, str]:
    """Unpack an invite blob → (server_url, code). Raises ValueError if malformed."""
    token = blob.strip()
    if token.startswith(_PREFIX):
        token = token[len(_PREFIX) :]
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad).decode("utf-8")
        data = json.loads(raw)
        server_url, code = str(data["s"]), str(data["c"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"not a valid Manthana invite: {exc}") from exc
    if not server_url or not code:
        raise ValueError("invite is missing server_url or code")
    return server_url, code


__all__ = ["encode_invite", "decode_invite"]
