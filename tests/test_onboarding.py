"""Org onboarding — invite blob, persisted secrets, invite store + endpoints (phase P2).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import decode_invite, encode_invite
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import verify_team_token
from manthana.server.config import persisted_secrets
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 3, 1, tzinfo=UTC)


# ── invite blob (shared, Apache) ─────────────────────────────────────────────
def test_invite_blob_roundtrips_and_rejects_garbage() -> None:
    blob = encode_invite("https://manthana.acme.com/", "code123")
    assert blob.startswith("mia_")
    assert decode_invite(blob) == ("https://manthana.acme.com", "code123")  # trailing / stripped
    assert decode_invite("  " + blob + "  ") == ("https://manthana.acme.com", "code123")
    with pytest.raises(ValueError):
        decode_invite("not-a-real-invite")


# ── persisted secrets (quickstart) ───────────────────────────────────────────
def test_persisted_secrets_generate_then_reuse(tmp_path: Path) -> None:
    jwt1, admin1 = persisted_secrets(tmp_path)
    assert jwt1 and admin1 and jwt1 != admin1
    jwt2, admin2 = persisted_secrets(tmp_path)  # second call reuses the file
    assert (jwt2, admin2) == (jwt1, admin1)  # stable → existing agent tokens keep working
    assert (tmp_path / "server-secrets.toml").exists()


# ── invite store methods ─────────────────────────────────────────────────────
def _store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    return store


def test_bound_single_use_invite_redeems_once() -> None:
    store = _store()
    store.create_invite("c1", org_id="o1", team_id="t1", actor="bob@x.com", uses=1,
                        expires_at=_T0 + timedelta(days=14))
    row = store.redeem_invite("c1", now=_T0)
    assert row is not None and row.actor == "bob@x.com"
    assert store.redeem_invite("c1", now=_T0) is None  # single-use → gone
    assert store.get_invite("c1").redeemed_at is not None  # type: ignore[union-attr]


def test_open_multi_use_invite_decrements() -> None:
    store = _store()
    store.create_invite("open1", org_id="o1", team_id="t1", uses=3,
                        expires_at=_T0 + timedelta(days=14))
    assert store.redeem_invite("open1", now=_T0) is not None
    assert store.redeem_invite("open1", now=_T0) is not None
    assert store.get_invite("open1").uses_left == 1  # type: ignore[union-attr]


def test_expired_and_unknown_invites_rejected() -> None:
    store = _store()
    store.create_invite("old", org_id="o1", team_id="t1", uses=1, expires_at=_T0)
    assert store.redeem_invite("old", now=_T0 + timedelta(days=1)) is None  # expired
    assert store.redeem_invite("nope", now=_T0) is None  # unknown


# ── endpoints: admin mint + public redeem ────────────────────────────────────
def _client() -> tuple[TestClient, ServerStore]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = _store()
    client = TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])))
    return client, store


def test_admin_invites_endpoint_is_gated_and_enroll_is_open() -> None:
    client, _ = _client()
    body = {"org_id": "o1", "team_id": "t1", "actor": "bob@x.com"}
    assert client.post("/v1/admin/invites", json=body).status_code == 401  # no admin token
    resp = client.post("/v1/admin/invites", json=body, headers={"X-Admin-Token": "adm"})
    assert resp.status_code == 200
    code = resp.json()["code"]

    # redeem (unauthenticated) → a valid team token for the bound actor
    r = client.post("/v1/enroll", json={"code": code})
    assert r.status_code == 200
    claims = verify_team_token("x" * 40, r.json()["token"])
    assert claims.actor == "bob@x.com" and claims.org_id == "o1" and claims.team_id == "t1"
    # single-use → second redeem fails
    assert client.post("/v1/enroll", json={"code": code}).status_code == 400


def test_open_invite_requires_actor_without_consuming() -> None:
    client, store = _client()
    resp = client.post(
        "/v1/admin/invites", json={"org_id": "o1", "team_id": "t1", "uses": 5},
        headers={"X-Admin-Token": "adm"},
    )
    code = resp.json()["code"]
    # open invite with no actor → 400, and NO use consumed
    assert client.post("/v1/enroll", json={"code": code}).status_code == 400
    assert store.get_invite(code).uses_left == 5  # type: ignore[union-attr]
    # with an actor → succeeds
    r = client.post("/v1/enroll", json={"code": code, "actor": "carol@x.com"})
    assert r.status_code == 200
    assert verify_team_token("x" * 40, r.json()["token"]).actor == "carol@x.com"


def test_enroll_unknown_code_400() -> None:
    client, _ = _client()
    assert client.post("/v1/enroll", json={"code": "bogus"}).status_code == 400
