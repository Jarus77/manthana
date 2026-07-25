"""The founder console's read API.

One property dominates this file: **a founder cannot read another org's anything.**
Every endpoint takes an ``org_id`` and every one of them must ignore it for a
founder, because a client-supplied tenant id is the single most dangerous input on
this surface. The isolation lives in one function (``scope_org``), which is what
makes it reviewable — so the tests exercise it through every route rather than
trusting the function once.

After that: engineers are refused (they hold a wiki login, not a management one),
admins may switch orgs because they can see all of them, and de-identified orgs
must not leak actor names into the payload at all — hiding them in the UI is not
hiding them.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_engineer_token, issue_founder_token
from manthana.server.console_api import API
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore
from manthana.server.ui import COOKIE

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECRET = "x" * 40

#: Every endpoint that accepts an org_id. Kept as data so a new route cannot be
#: added without either appearing here or being a deliberate omission.
ORG_SCOPED = ["/audit", "/members", "/invites", "/sessions", "/topics", "/cost", "/usage"]


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
        approach="did the thing",
        outcome=Outcome.success,
        est_cost_usd=0.5,
        tier_used="opus",
        released=True,
    )


def _make(**config_kwargs):
    config = ServerConfig(
        jwt_secret=_SECRET, admin_token="adm", **config_kwargs
    )
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])),
        follow_redirects=False,
    )
    return client, config, store


def _seed(store: ServerStore) -> None:
    """Two tenants with distinguishable contents, so a leak is visible rather than
    merely possible."""
    for org, actor, project in (("org-a", "alice@a.com", "alpha"), ("org-b", "bob@b.com", "beta")):
        store.create_org(org, org.upper())
        store.create_team(f"{org}-core", org, "core")
        store.upsert_actor(actor, org, f"{org}-core")
        for i in range(3):
            store.ingest_compaction(
                _comp(f"{org}-c{i}", actor, project), org_id=org, team_id=f"{org}-core"
            )


def _as_founder(client, org_id: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, issue_founder_token(_SECRET, org_id=org_id))


def _as_admin(client) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, "adm")


def _as_engineer(client, org_id: str) -> None:
    client.cookies.clear()
    client.cookies.set(
        COOKIE, issue_engineer_token(_SECRET, org_id=org_id, actor="eng@a.com")
    )


# ── the property that matters most ─────────────────────────────────────────
@pytest.mark.parametrize("path", ORG_SCOPED)
def test_a_founder_asking_for_another_org_gets_their_own(path: str) -> None:
    """The forged-org_id test, run against every endpoint that takes one.

    Not a 403 — the request succeeds, scoped to the caller. That is deliberate:
    scope_org OVERRIDES rather than rejects, so a bug that forgets to check cannot
    become a bug that serves the wrong tenant.
    """
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    resp = client.get(f"{API}{path}", params={"org_id": "org-b"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["org_id"] == "org-a"


def test_a_founder_never_sees_another_orgs_sessions() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.get(f"{API}/sessions", params={"org_id": "org-b"}).json()

    assert body["org_id"] == "org-a"
    assert {s["id"] for s in body["sessions"]} == {"org-a-c0", "org-a-c1", "org-a-c2"}
    assert body["projects"] == ["alpha"]


def test_session_detail_is_org_scoped() -> None:
    """The id is a real compaction — just not this founder's. It must 404 rather
    than resolve, because get_compaction is scoped and the scoping is the point."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    resp = client.get(f"{API}/sessions/org-b-c0", params={"org_id": "org-b"})

    assert resp.status_code == 404


def test_a_founder_only_ever_lists_their_own_org() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.get(f"{API}/orgs").json()

    assert [o["id"] for o in body["orgs"]] == ["org-a"]


# ── who may be here at all ─────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/me", "/orgs", *ORG_SCOPED])
def test_engineers_are_refused_everywhere(path: str) -> None:
    """An engineer holds a wiki login, not a management one. The oversight
    surfaces — cost, audit, members, sessions — are not theirs."""
    client, _, store = _make()
    _seed(store)
    _as_engineer(client, "org-a")

    resp = client.get(f"{API}{path}", params={"org_id": "org-a"})

    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["/me", "/orgs", *ORG_SCOPED])
def test_anonymous_callers_are_refused_everywhere(path: str) -> None:
    client, _, store = _make()
    _seed(store)

    resp = client.get(f"{API}{path}", params={"org_id": "org-a"})

    assert resp.status_code == 401


def test_admin_sees_every_org_and_may_switch() -> None:
    client, _, store = _make()
    _seed(store)
    _as_admin(client)

    me = client.get(f"{API}/me").json()
    assert me["can_switch_org"] is True
    assert {o["id"] for o in me["orgs"]} == {"org-a", "org-b"}

    # And an admin's org_id IS honoured, because they may see all of them.
    a = client.get(f"{API}/sessions", params={"org_id": "org-a"}).json()
    b = client.get(f"{API}/sessions", params={"org_id": "org-b"}).json()
    assert a["org_id"] == "org-a" and b["org_id"] == "org-b"
    assert {s["id"] for s in b["sessions"]} == {"org-b-c0", "org-b-c1", "org-b-c2"}


def test_founder_is_told_not_to_draw_a_switcher() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    me = client.get(f"{API}/me").json()

    assert me["can_switch_org"] is False
    assert me["role"] == "founder"
    assert [o["id"] for o in me["orgs"]] == ["org-a"]


def test_admin_without_an_org_gets_a_clear_error() -> None:
    """An admin is not scoped to anything, so an org-scoped call with no org is a
    bad request rather than a silent empty result."""
    client, _, store = _make()
    _seed(store)
    _as_admin(client)

    assert client.get(f"{API}/sessions").status_code == 400


# ── privacy ────────────────────────────────────────────────────────────────
def test_deidentified_orgs_omit_actors_from_the_payload() -> None:
    """Not hidden in the UI — absent from the response. Data that reaches the
    browser has left the server, whatever the client then chooses to render."""
    client, _, store = _make(privacy_mode="k_anon")
    _seed(store)
    _as_founder(client, "org-a")

    listing = client.get(f"{API}/sessions", params={"org_id": "org-a"}).json()
    detail = client.get(f"{API}/sessions/org-a-c0", params={"org_id": "org-a"}).json()

    assert listing["named"] is False
    assert listing["engineers"] == []
    assert all("actor" not in s for s in listing["sessions"])
    assert "actor" not in detail
    assert "alice@a.com" not in listing.__str__()


def test_open_orgs_name_people() -> None:
    client, _, store = _make(privacy_mode="open")
    _seed(store)
    _as_founder(client, "org-a")

    listing = client.get(f"{API}/sessions", params={"org_id": "org-a"}).json()

    assert listing["named"] is True
    assert {e["id"] for e in listing["engineers"]} == {"alice@a.com"}
    assert all(s["actor"] == "alice@a.com" for s in listing["sessions"])


def test_per_org_privacy_overrides_the_server_default() -> None:
    """One consenting org must not open up the others, and vice versa."""
    client, _, store = _make(privacy_mode="k_anon")
    _seed(store)
    store.set_org_privacy("org-a", "open")

    _as_founder(client, "org-a")
    assert client.get(f"{API}/sessions", params={"org_id": "org-a"}).json()["named"] is True
    _as_founder(client, "org-b")
    assert client.get(f"{API}/sessions", params={"org_id": "org-b"}).json()["named"] is False


# ── the numbers ────────────────────────────────────────────────────────────
def test_budget_reports_spend_against_the_effective_cap() -> None:
    """The cap a founder sees must be the one metering actually enforces, override
    included — a page that disagrees with the thing blocking the pass is worse than
    no page."""
    client, _, store = _make(llm_monthly_cap_usd=100.0)
    _seed(store)
    store.set_org_quota("org-a", 5.0)
    _as_founder(client, "org-a")

    usage = client.get(f"{API}/usage", params={"org_id": "org-a"}).json()

    assert usage["cap_usd"] == 5.0
    assert usage["cap_is_override"] is True
    assert usage["blocked"] is False
    assert usage["spent_usd"] == 0.0


def test_unlimited_cap_is_reported_as_zero_not_omitted() -> None:
    """0 means unlimited. The client has to be able to tell that apart from a cap
    of nothing, so it renders "no cap" instead of a bar that is 0% full."""
    client, _, store = _make(llm_monthly_cap_usd=0.0)
    _seed(store)
    _as_founder(client, "org-a")

    usage = client.get(f"{API}/usage", params={"org_id": "org-a"}).json()

    assert usage["cap_usd"] == 0.0
    assert usage["blocked"] is False


def test_cost_report_is_free_and_shaped_for_a_chart() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.get(f"{API}/cost", params={"org_id": "org-a"}).json()

    assert body["org_id"] == "org-a"
    for key in ("sessions", "priced", "current_usd", "projected_usd", "savings_usd", "rows"):
        assert key in body, key


def test_topics_reports_its_own_coverage() -> None:
    """A truncated clustering is a partial answer, and a partial answer that does
    not say so is a wrong one."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.get(f"{API}/topics", params={"org_id": "org-a"}).json()

    assert body["coverage"]["matched"] >= 0
    assert "truncated" in body["coverage"]
    assert body["k_anon_floor"] >= 1


def test_orgs_overview_counts_what_the_console_shows() -> None:
    client, _, store = _make()
    _seed(store)
    _as_admin(client)

    rows = {o["id"]: o for o in client.get(f"{API}/orgs").json()["orgs"]}

    assert rows["org-a"]["compactions"] == 3
    assert rows["org-a"]["teams"] == 1
    assert "spent_usd" in rows["org-a"]["budget"]


def test_exhausted_invites_are_not_listed() -> None:
    """This is a list of what still works, not a history."""
    from datetime import timedelta

    client, _, store = _make()
    _seed(store)
    store.create_invite(
        "live", org_id="org-a", team_id="org-a-core", uses=3,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    store.create_invite(
        "spent", org_id="org-a", team_id="org-a-core", uses=0,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    _as_founder(client, "org-a")

    body = client.get(f"{API}/invites", params={"org_id": "org-a"}).json()
    codes = {i["code"] for i in body["invites"]}

    assert codes == {"live"}


def test_revoked_session_loses_the_console() -> None:
    client, _, store = _make()
    _seed(store)
    token = issue_founder_token(_SECRET, org_id="org-a")
    client.cookies.set(COOKIE, token)
    assert client.get(f"{API}/me").status_code == 200

    store.revoke_token(token, reason="test", revoked_by="admin")

    assert client.get(f"{API}/me").status_code == 401
