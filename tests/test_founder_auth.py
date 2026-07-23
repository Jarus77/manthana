"""Per-org founder auth: org-scoped founder JWTs for API + console (hosted).

The isolation contract under test: a startup's founder token grants exactly
their org — API queries against another org 403, the console renders only
their org, and a forged org_id in a console form is overridden by the session.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import (
    AuthError,
    issue_founder_token,
    issue_team_token,
    verify_founder_token,
    verify_team_token,
)
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECRET = "x" * 40
ADMIN = {"X-Admin-Token": "adm"}


def _comp(cid: str, actor: str, project: str = "demo") -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=cid,
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=f"intent {cid}",
        approach="a",
        outcome=Outcome.success,
        est_cost_usd=0.5,
        tier_used="opus",
        released=True,
    )


def _make(provider: ScriptedProvider | None = None):
    config = ServerConfig(jwt_secret=_SECRET, admin_token="adm")
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, provider or ScriptedProvider([])))
    return client, config, store, obj


def _seed_two_orgs(store: ServerStore) -> None:
    store.create_org("org-a", "Org A")
    store.create_org("org-b", "Org B")
    for i in range(5):
        store.ingest_compaction(_comp(f"a{i}", f"a{i}@a.com"), org_id="org-a", team_id="t1")
        store.ingest_compaction(_comp(f"b{i}", f"b{i}@b.com"), org_id="org-b", team_id="t1")


def _founder_auth(org: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_founder_token(_SECRET, org_id=org)}"}


# ── token mechanics ────────────────────────────────────────────────────────
def test_founder_token_roundtrip_and_scope_exclusivity() -> None:
    founder = issue_founder_token(_SECRET, org_id="org-a")
    assert verify_founder_token(_SECRET, founder).org_id == "org-a"
    # a founder token is NOT an agent token …
    with pytest.raises(AuthError):
        verify_team_token(_SECRET, founder)
    # … and an agent token is NOT a founder token
    agent = issue_team_token(_SECRET, org_id="org-a", team_id="t1", actor="e@a.com")
    with pytest.raises(AuthError):
        verify_founder_token(_SECRET, agent)


def test_mint_founder_token_requires_admin() -> None:
    client, *_ = _make()
    assert client.post("/v1/admin/founder-tokens", json={"org_id": "o"}).status_code == 401
    resp = client.post("/v1/admin/founder-tokens", json={"org_id": "org-a"}, headers=ADMIN)
    assert resp.is_success
    assert verify_founder_token(_SECRET, resp.json()["token"]).org_id == "org-a"


# ── API isolation ──────────────────────────────────────────────────────────
def test_founder_token_queries_own_org_only() -> None:
    provider = ScriptedProvider(["{}", "Org A shipped things [a0]."])
    client, _config, store, _obj = _make(provider)
    _seed_two_orgs(store)
    own = client.post(
        "/v1/founder/query",
        json={"org_id": "org-a", "query": "what shipped?"},
        headers=_founder_auth("org-a"),
    )
    assert own.status_code == 200
    cross = client.post(
        "/v1/founder/query",
        json={"org_id": "org-a", "query": "what shipped?"},
        headers=_founder_auth("org-b"),
    )
    assert cross.status_code == 403


def test_founder_endpoints_reject_agent_tokens_and_anonymous() -> None:
    client, _config, store, _obj = _make()
    _seed_two_orgs(store)
    agent = issue_team_token(_SECRET, org_id="org-a", team_id="t1", actor="e@a.com")
    for headers in ({}, {"Authorization": f"Bearer {agent}"}):
        resp = client.post(
            "/v1/founder/query",
            json={"org_id": "org-a", "query": "q"},
            headers=headers,
        )
        assert resp.status_code == 401


def test_founder_topics_digest_audit_scoped() -> None:
    client, _config, store, _obj = _make()
    _seed_two_orgs(store)
    auth_a = _founder_auth("org-a")
    assert client.get("/v1/founder/topics", params={"org_id": "org-a"}, headers=auth_a).is_success
    assert (
        client.get("/v1/founder/topics", params={"org_id": "org-b"}, headers=auth_a).status_code
        == 403
    )
    assert client.get("/v1/founder/digest", params={"org_id": "org-a"}, headers=auth_a).is_success
    assert (
        client.get("/v1/founder/digest", params={"org_id": "org-b"}, headers=auth_a).status_code
        == 403
    )
    assert client.get("/v1/founder/audit", params={"org_id": "org-a"}, headers=auth_a).is_success
    assert (
        client.get("/v1/founder/audit", params={"org_id": "org-b"}, headers=auth_a).status_code
        == 403
    )
    # admin token still reaches every org
    assert client.get("/v1/founder/topics", params={"org_id": "org-b"}, headers=ADMIN).is_success


def test_admin_endpoints_still_reject_founder_tokens() -> None:
    client, _config, store, _obj = _make()
    store.create_org("org-a", "Org A")
    resp = client.post(
        "/v1/admin/orgs",
        json={"org_id": "evil", "name": "E"},
        headers=_founder_auth("org-a"),
    )
    assert resp.status_code == 401


def test_agent_ingest_rejects_founder_token() -> None:
    client, _config, store, _obj = _make()
    store.create_org("org-a", "Org A")
    resp = client.post(
        "/v1/compactions", json={"compactions": []}, headers=_founder_auth("org-a")
    )
    assert resp.status_code == 401


# ── console isolation ──────────────────────────────────────────────────────
def _founder_login(client: TestClient, org: str) -> None:
    resp = client.post(
        "/ui/login",
        data={"token": issue_founder_token(_SECRET, org_id=org)},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # redirect to /ui = login accepted


def test_console_founder_sees_only_own_org() -> None:
    client, _config, store, _obj = _make()
    _seed_two_orgs(store)
    _founder_login(client, "org-a")
    page = client.get("/ui").text
    assert "Org A" in page
    assert "Org B" not in page


def test_console_admin_still_sees_all_orgs() -> None:
    client, _config, store, _obj = _make()
    _seed_two_orgs(store)
    client.post("/ui/login", data={"token": "adm"})
    page = client.get("/ui").text
    assert "Org A" in page and "Org B" in page


def test_console_forged_org_field_is_overridden_by_session() -> None:
    # Founder of org-a posts org_id=org-b in the form → the query runs against org-a.
    provider = ScriptedProvider(["{}", "Org A work [a0]."])
    client, _config, store, _obj = _make(provider)
    _seed_two_orgs(store)
    _founder_login(client, "org-a")
    page = client.post("/ui/query", data={"org_id": "org-b", "query": "what shipped?"}).text
    assert "org: org-a" in page
    assert "org-b" not in page
    # the audit row landed under org-a, not org-b
    assert len(store.list_founder_audit("org-a")) == 1
    assert len(store.list_founder_audit("org-b")) == 0


def test_console_forged_org_in_get_routes_overridden() -> None:
    client, _config, store, _obj = _make()
    _seed_two_orgs(store)
    _founder_login(client, "org-a")
    for path in ("/ui/topics", "/ui/router", "/ui/digest"):
        page = client.get(path, params={"org_id": "org-b"}).text
        assert "org: org-a" in page, path


def test_console_rejects_garbage_token() -> None:
    client, *_ = _make()
    assert client.post("/ui/login", data={"token": "not-a-token"}).status_code == 401
    # unauthenticated console → login redirect
    assert client.get("/ui", follow_redirects=False).status_code == 303


# ── founder self-serve invites (the two-startups case) ─────────────────────
#
# The capability a hosted startup's founder most needs: onboard their own
# engineers without the operator admin token, which is server-wide and would
# expose every other tenant. Every test here doubles as an isolation test —
# org-a's founder must never touch org-b.
def _two_orgs_with_teams() -> tuple[TestClient, ServerStore]:
    # Invites carry team_id as a scoped string; nothing joins it to TeamRow, so
    # the orgs alone are enough. (TeamRow.id is a GLOBAL primary key, so creating
    # a same-named team in both orgs would collide — a separate, cosmetic issue.)
    client, _config, store, _obj = _make()
    store.create_org("org-a", "Org A")
    store.create_org("org-b", "Org B")
    return client, store


def test_founder_mints_an_invite_for_their_own_org() -> None:
    client, _ = _two_orgs_with_teams()
    resp = client.post(
        "/v1/founder/invites", json={"org_id": "org-a"}, headers=_founder_auth("org-a")
    )
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert resp.json()["team_id"] == "core"  # the onboard-org default

    # …and it actually works: an engineer redeems it for a team token.
    redeemed = client.post("/v1/enroll", json={"code": code, "actor": "eng@a.com"})
    assert redeemed.status_code == 200
    assert verify_team_token(_SECRET, redeemed.json()["token"]).org_id == "org-a"


def test_founder_cannot_mint_into_another_org() -> None:
    client, _ = _two_orgs_with_teams()
    # org-a's founder naming org-b in the body is refused by check_org.
    resp = client.post(
        "/v1/founder/invites", json={"org_id": "org-b"}, headers=_founder_auth("org-a")
    )
    assert resp.status_code == 403


def test_founder_invite_needs_a_founder_or_admin_token() -> None:
    client, _ = _two_orgs_with_teams()
    assert client.post("/v1/founder/invites", json={"org_id": "org-a"}).status_code == 401
    # An engineer/team token is scope 'agent', not 'founder' → rejected.
    team = issue_team_token(_SECRET, org_id="org-a", team_id="core", actor="e@a.com")
    resp = client.post(
        "/v1/founder/invites", json={"org_id": "org-a"},
        headers={"Authorization": f"Bearer {team}"},
    )
    assert resp.status_code == 401


def test_founder_lists_only_their_own_orgs_invites() -> None:
    client, _ = _two_orgs_with_teams()
    client.post("/v1/founder/invites", json={"org_id": "org-a"}, headers=_founder_auth("org-a"))
    client.post("/v1/founder/invites", json={"org_id": "org-b"}, headers=_founder_auth("org-b"))

    a_list = client.get(
        "/v1/founder/invites", params={"org_id": "org-a"}, headers=_founder_auth("org-a")
    ).json()["invites"]
    assert len(a_list) == 1

    # org-a's founder cannot list org-b's invites.
    assert client.get(
        "/v1/founder/invites", params={"org_id": "org-b"}, headers=_founder_auth("org-a")
    ).status_code == 403


def test_founder_revokes_own_invite_but_not_another_orgs() -> None:
    client, _ = _two_orgs_with_teams()
    b_code = client.post(
        "/v1/founder/invites", json={"org_id": "org-b"}, headers=_founder_auth("org-b")
    ).json()["code"]

    # org-a's founder cannot revoke org-b's invite — and the 404 doesn't confirm
    # the code exists elsewhere. (check_org fires first: naming org-b is a 403.)
    assert client.post(
        "/v1/founder/invites/revoke", json={"org_id": "org-b", "code": b_code},
        headers=_founder_auth("org-a"),
    ).status_code == 403
    # Even claiming it's their own org, a foreign code is a 404, not a revoke.
    assert client.post(
        "/v1/founder/invites/revoke", json={"org_id": "org-a", "code": b_code},
        headers=_founder_auth("org-a"),
    ).status_code == 404
    # org-b's own code is still live.
    assert client.post("/v1/enroll", json={"code": b_code, "actor": "e@b.com"}).status_code == 200

    # org-b's founder revokes their own → the code stops working.
    assert client.post(
        "/v1/founder/invites/revoke", json={"org_id": "org-b", "code": b_code},
        headers=_founder_auth("org-b"),
    ).status_code == 200


def test_founder_invite_team_defaults_to_core_and_accepts_an_explicit_one() -> None:
    client, _config, store, _obj = _make()
    store.create_org("org-a", "Org A")

    default = client.post(
        "/v1/founder/invites", json={"org_id": "org-a"}, headers=_founder_auth("org-a")
    )
    assert default.status_code == 200 and default.json()["team_id"] == "core"

    # An explicit team is taken as-is — a team token is just a scoped string, and
    # the org boundary (not the team) is what check_org enforces.
    chosen = client.post(
        "/v1/founder/invites", json={"org_id": "org-a", "team_id": "platform"},
        headers=_founder_auth("org-a"),
    )
    assert chosen.status_code == 200 and chosen.json()["team_id"] == "platform"


def test_admin_token_can_still_drive_the_founder_invite_endpoints() -> None:
    # The operator (admin token) retains full access — check_org allows any org.
    client, _ = _two_orgs_with_teams()
    resp = client.post("/v1/founder/invites", json={"org_id": "org-a"}, headers=ADMIN)
    assert resp.status_code == 200
