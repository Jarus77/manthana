"""onboard-org: one-command hosted customer onboarding over the admin HTTP API.

Drives the real FastAPI app via TestClient (which is httpx-compatible), so the
whole flow — org, teams, invites, founder token, quota, welcome block — is
exercised end-to-end without a network.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import verify_founder_token
from manthana.server.cli import _onboard_org_via_api
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_SECRET = "x" * 40


def _make():
    config = ServerConfig(jwt_secret=_SECRET, admin_token="adm")
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), ScriptedProvider([]))
    )
    return client, store


def test_onboard_org_open_invite_full_flow() -> None:
    client, store = _make()
    block = _onboard_org_via_api(
        client,
        org_id="acme", org_name="Acme Inc", server_url="https://api.example.com",
        admin_token="adm", teams=["platform", "ml"], emails_path="",
        open_invite=True, quota_usd=25.0, expires_days=14,
    )
    # org + teams exist
    assert store.get_org("acme") is not None
    assert {t.id for t in store.list_teams("acme")} == {"platform", "ml"}
    # one open invite per team
    assert len(store.list_invites("acme")) == 2
    # quota applied
    assert store.get_org_quota("acme") == 25.0
    # welcome block has the setup one-liners, console URL, and a working founder token
    assert "manthana setup " in block
    assert "[team platform]" in block and "[team ml]" in block
    assert "https://api.example.com/ui" in block
    token = next(
        line.split()[-1] for line in block.splitlines() if "sign-in token" in line
    )
    assert verify_founder_token(_SECRET, token).org_id == "acme"
    assert "$25.00/month" in block


def test_onboard_org_bound_email_invites(tmp_path: Path) -> None:
    client, store = _make()
    emails = tmp_path / "emails.txt"
    emails.write_text("a@acme.com\n# comment\nb@acme.com\n")
    block = _onboard_org_via_api(
        client,
        org_id="acme", org_name="Acme", server_url="https://api.example.com",
        admin_token="adm", teams=["core"], emails_path=str(emails),
        open_invite=False, quota_usd=-1.0, expires_days=14,
    )
    invites = store.list_invites("acme")
    assert sorted(i.actor for i in invites) == ["a@acme.com", "b@acme.com"]
    assert all(i.uses_left == 1 for i in invites)
    assert "a@acme.com →" in block
    assert "server default" in block  # no quota override requested
    assert store.get_org_quota("acme") is None


def test_onboard_org_requires_invite_mode_and_valid_admin() -> None:
    client, _store = _make()
    with pytest.raises(RuntimeError, match="--open .*OR --emails"):
        _onboard_org_via_api(
            client,
            org_id="acme", org_name="Acme", server_url="https://x", admin_token="adm",
            teams=["core"], emails_path="", open_invite=False, quota_usd=-1.0,
            expires_days=14,
        )
    with pytest.raises(RuntimeError, match="401"):
        _onboard_org_via_api(
            client,
            org_id="acme", org_name="Acme", server_url="https://x", admin_token="WRONG",
            teams=["core"], emails_path="", open_invite=True, quota_usd=-1.0,
            expires_days=14,
        )


def test_onboarded_engineer_can_enroll_and_ingest() -> None:
    # The printed setup blob redeems for a working agent token (org-of-one case).
    from manthana.schemas import decode_invite

    client, store = _make()
    block = _onboard_org_via_api(
        client,
        org_id="jane", org_name="Jane Doe", server_url="https://api.example.com",
        admin_token="adm", teams=["solo"], emails_path="",
        open_invite=True, quota_usd=5.0, expires_days=14,
    )
    blob = next(
        line.split("manthana setup ", 1)[1]
        for line in block.splitlines()
        if "manthana setup " in line
    )
    _url, code = decode_invite(blob)
    resp = client.post("/v1/enroll", json={"code": code, "actor": "jane@doe.dev"})
    assert resp.is_success
    token = resp.json()["token"]
    ingest = client.post(
        "/v1/compactions",
        json={"compactions": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ingest.is_success  # the redeemed token authenticates against the agent API
