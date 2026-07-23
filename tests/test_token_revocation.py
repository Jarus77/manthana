"""Surgical JWT revocation — kill ONE leaked token without rotating the shared
secret (which would log out every engineer, agent, and founder at once).

The contract under test: a revoked token is dead on EVERY path (agent API,
founder API, console cookie, wiki), the token itself is never stored (only its
hash), and no other token is affected.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import (
    issue_engineer_token,
    issue_founder_token,
    issue_team_token,
)
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore

_SECRET = "x" * 40
ADMIN = {"X-Admin-Token": "adm"}


def _make() -> tuple[TestClient, ServerStore, ServerConfig]:
    config = ServerConfig(jwt_secret=_SECRET, admin_token="adm")
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Org One")
    store.create_team("core", "o1", "Core")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    return client, store, config


def _revoke(client: TestClient, token: str, reason: str = "leaked in chat") -> dict:
    resp = client.post(
        "/v1/admin/revoke-token", json={"token": token, "reason": reason}, headers=ADMIN
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the token itself is never stored ───────────────────────────────────────
def test_only_a_hash_is_stored_never_the_token() -> None:
    _client, store, config = _make()
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor="eng@o1.com")
    fp = store.revoke_token(token, reason="test")

    assert fp == store.token_fingerprint(token)
    assert len(fp) == 64  # sha256 hex
    rows = store.list_revoked_tokens()
    assert len(rows) == 1
    # The raw token appears nowhere in the stored row.
    dumped = str(rows[0].model_dump())
    assert token not in dumped
    assert rows[0].token_hash == fp


# ── a revoked token is dead on every path ──────────────────────────────────
def test_revoked_engineer_token_cannot_sign_into_the_console() -> None:
    client, _store, config = _make()
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor="eng@o1.com")
    # It works before revocation.
    client.post("/ui/login", data={"token": token})
    assert client.get("/ui/home").status_code == 200

    _revoke(client, token)

    # A NEW client (fresh cookie jar) presenting the same token is rejected.
    fresh = TestClient(client.app, follow_redirects=False)  # type: ignore[arg-type]
    fresh.post("/ui/login", data={"token": token})
    assert fresh.get("/ui/home").status_code == 303  # bounced to login


def test_revoked_engineer_token_cannot_reach_the_wiki_api() -> None:
    client, _store, config = _make()
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor="eng@o1.com")
    _revoke(client, token)
    # The wiki API resolves the session from the same cookie.
    client.cookies.set("manthana_admin", token)
    assert client.get("/ui/api/wiki/home").status_code in (401, 403)


def test_revoked_agent_token_cannot_ingest() -> None:
    client, _store, config = _make()
    agent = issue_team_token(config.jwt_secret, org_id="o1", team_id="core", actor="eng@o1.com")
    ok = client.post(
        "/v1/compactions", json={"compactions": []}, headers={"Authorization": f"Bearer {agent}"}
    )
    assert ok.status_code == 200  # works before

    _revoke(client, agent)
    denied = client.post(
        "/v1/compactions", json={"compactions": []}, headers={"Authorization": f"Bearer {agent}"}
    )
    assert denied.status_code == 401
    assert "revoked" in denied.json()["detail"]


def test_revoked_founder_token_cannot_query() -> None:
    client, _store, config = _make()
    founder = issue_founder_token(config.jwt_secret, org_id="o1")
    _revoke(client, founder)
    denied = client.post(
        "/v1/founder/query",
        json={"org_id": "o1", "query": "what happened?"},
        headers={"Authorization": f"Bearer {founder}"},
    )
    assert denied.status_code == 401
    assert "revoked" in denied.json()["detail"]


# ── revocation is surgical: nobody else is affected ────────────────────────
def test_revoking_one_token_leaves_every_other_working() -> None:
    client, _store, config = _make()
    leaked = issue_engineer_token(config.jwt_secret, org_id="o1", actor="leaked@o1.com")
    other_eng = issue_engineer_token(config.jwt_secret, org_id="o1", actor="fine@o1.com")
    an_agent = issue_team_token(config.jwt_secret, org_id="o1", team_id="core", actor="fine@o1.com")

    _revoke(client, leaked)

    # The other engineer's console login still works.
    other = TestClient(client.app, follow_redirects=False)  # type: ignore[arg-type]
    other.post("/ui/login", data={"token": other_eng})
    assert other.get("/ui/home").status_code == 200
    # The agent token still ingests.
    assert client.post(
        "/v1/compactions", json={"compactions": []},
        headers={"Authorization": f"Bearer {an_agent}"},
    ).status_code == 200


# ── operational shape ──────────────────────────────────────────────────────
def test_revocation_is_idempotent_and_records_provenance() -> None:
    client, store, config = _make()
    token = issue_founder_token(config.jwt_secret, org_id="o1")
    first = _revoke(client, token, reason="one")
    second = _revoke(client, token, reason="two")
    assert first["fingerprint"] == second["fingerprint"]
    assert len(store.list_revoked_tokens()) == 1  # merged, not duplicated
    # Provenance was decoded for the audit view.
    assert first["scope"] == "founder"
    assert first["org_id"] == "o1"


def test_a_garbage_token_can_still_be_revoked() -> None:
    # Revocation works by hash, so even an unparseable string is blocklistable —
    # the point is to kill exactly what was pasted, whatever shape it is in.
    client, store, _config = _make()
    resp = client.post(
        "/v1/admin/revoke-token", json={"token": "not-a-real-jwt"}, headers=ADMIN
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] is None  # nothing decoded
    assert store.is_token_revoked("not-a-real-jwt")


def test_revoke_and_audit_endpoints_require_admin() -> None:
    client, _store, config = _make()
    token = issue_founder_token(config.jwt_secret, org_id="o1")
    assert client.post("/v1/admin/revoke-token", json={"token": token}).status_code == 401
    assert client.get("/v1/admin/revoked-tokens").status_code == 401


def test_audit_log_lists_hashes_not_tokens() -> None:
    client, _store, config = _make()
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor="eng@o1.com")
    _revoke(client, token, reason="leaked in a screenshot")
    audit = client.get("/v1/admin/revoked-tokens", headers=ADMIN).json()["revoked"]
    assert len(audit) == 1
    assert audit[0]["reason"] == "leaked in a screenshot"
    assert token not in str(audit)  # the token never appears in the audit view
