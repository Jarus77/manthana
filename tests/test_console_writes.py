"""The console's writes and the three paths that spend money.

What matters here, beyond the isolation already proven for the reads:

  * an exhausted budget returns a STRUCTURED 429 — cap and spend as numbers, not
    prose the client has to parse back out of a sentence;
  * writes are founder-only and tenant-scoped, including the forged-org_id case;
  * an invite points at the org's REAL team, not the literal "core" the HTML
    console hardcoded — that id is a global primary key;
  * mining does not stack runs, because a second click doubles the model spend for
    the same corpus;
  * retiring the HTML console redirects rather than 404s, since /ui is printed in
    welcome emails and on server startup.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_engineer_token, issue_founder_token, verify_engineer_token
from manthana.server.console_api import API
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore
from manthana.server.ui import COOKIE

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECRET = "x" * 40

#: Every write, as (method, path, body). Kept as data so the founder-only and
#: forged-org checks run against all of them rather than a chosen few.
WRITES = [
    ("POST", "/query", {"org_id": "org-b", "query": "what happened?"}),
    ("POST", "/mine", {"org_id": "org-b"}),
    ("POST", "/invites", {"org_id": "org-b"}),
    ("POST", "/invites/revoke", {"org_id": "org-b", "code": "x"}),
    ("POST", "/engineer-token", {"org_id": "org-b", "actor": "e@b.com"}),
    ("POST", "/members/promote", {"org_id": "org-b", "identity_id": "google:b"}),
]


def _comp(cid: str, actor: str) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=cid, actor=actor, surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent=f"intent {cid}",
        approach="a", outcome=Outcome.success, est_cost_usd=0.5, tier_used="opus",
        released=True,
    )


def _make(**kw):
    config = ServerConfig(jwt_secret=_SECRET, admin_token="adm", **kw)
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])),
        follow_redirects=False,
    )
    return client, config, store


def _seed(store: ServerStore) -> None:
    for org, actor in (("org-a", "alice@a.com"), ("org-b", "bob@b.com")):
        store.create_org(org, org.upper())
        store.create_team(f"{org}-core", org, "core")
        store.upsert_actor(actor, org, f"{org}-core")
        store.upsert_identity(f"google:{org}", email=actor, org_id=org, role="engineer")
        for i in range(3):
            store.ingest_compaction(_comp(f"{org}-c{i}", actor), org_id=org, team_id=f"{org}-core")


def _as_founder(client, org_id: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, issue_founder_token(_SECRET, org_id=org_id))


def _as_engineer(client, org_id: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, issue_engineer_token(_SECRET, org_id=org_id, actor="e@a.com"))


def _call(client, method: str, path: str, body: dict):
    if method == "POST":
        return client.post(f"{API}{path}", json=body)
    return client.get(f"{API}{path}", params=body)


# ── the quota contract ─────────────────────────────────────────────────────
def test_exhausted_budget_returns_numbers_not_prose() -> None:
    """The client renders spend-against-cap as a state. Putting the figures in a
    sentence would make it parse them back out of wording that can change."""
    client, _, store = _make(llm_monthly_cap_usd=1.0)
    _seed(store)
    store.set_org_quota("org-a", 0.01)
    from manthana.server.metering import month_key

    store.add_llm_usage("org-a", month_key(), input_tokens=1, output_tokens=1, est_cost_usd=5.0)
    _as_founder(client, "org-a")

    resp = client.post(f"{API}/query", json={"org_id": "org-a", "query": "what happened?"})

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "quota_exceeded"
    assert detail["cap_usd"] == 0.01
    assert detail["spent_usd"] == 5.0
    assert detail["org_id"] == "org-a"
    assert isinstance(detail["message"], str) and detail["message"]


def test_mining_refuses_up_front_when_the_budget_is_gone() -> None:
    """Pre-checked so an exhausted org gets its 429 on THIS click, rather than a
    silent no-op in a background task nobody is watching."""
    client, _, store = _make()
    _seed(store)
    store.set_org_quota("org-a", 0.01)
    from manthana.server.metering import month_key

    store.add_llm_usage("org-a", month_key(), input_tokens=1, output_tokens=1, est_cost_usd=9.0)
    _as_founder(client, "org-a")

    resp = client.post(f"{API}/mine", json={"org_id": "org-a"})

    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "quota_exceeded"


# ── who may write ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path,body", WRITES)
def test_engineers_cannot_write(method: str, path: str, body: dict) -> None:
    client, _, store = _make()
    _seed(store)
    _as_engineer(client, "org-a")
    assert _call(client, method, path, {**body, "org_id": "org-a"}).status_code == 403


@pytest.mark.parametrize("method,path,body", WRITES)
def test_anonymous_callers_cannot_write(method: str, path: str, body: dict) -> None:
    client, _, store = _make()
    _seed(store)
    assert _call(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITES)
def test_writes_are_forced_to_the_callers_own_org(method: str, path: str, body: dict) -> None:
    """Every body here names org-b; the caller owns org-a. Nothing may land on
    org-b — whether it succeeds or fails, it must have been about org-a."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    resp = _call(client, method, path, body)

    if resp.status_code < 300:
        assert resp.json()["org_id"] == "org-a"
    # And in every case, org-b is untouched.
    assert [i.role for i in store.list_identities("org-b")] == ["engineer"]
    assert not [i for i in store.list_invites("org-b")]


# ── invites ────────────────────────────────────────────────────────────────
def test_invite_points_at_the_orgs_real_team_not_the_literal_core() -> None:
    """TeamRow.id is a GLOBAL primary key, so self-serve orgs get `{org}-core`. The
    HTML console hardcoded "core" here and would mint invites pointing at whichever
    org happened to own that row."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    code = client.post(f"{API}/invites", json={"org_id": "org-a"}).json()["code"]

    row = store.get_invite(code)
    assert row is not None
    assert row.team_id == "org-a-core"
    assert row.org_id == "org-a"


def test_open_and_bound_invites_differ_in_the_way_that_matters() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    shared = client.post(f"{API}/invites", json={"org_id": "org-a"}).json()
    bound = client.post(f"{API}/invites", json={"org_id": "org-a", "actor": "new@a.com"}).json()

    assert shared["single_use"] is False and shared["actor"] is None
    assert bound["single_use"] is True and bound["actor"] == "new@a.com"
    assert store.get_invite(shared["code"]).uses_left > 1  # type: ignore[union-attr]
    assert store.get_invite(bound["code"]).uses_left == 1  # type: ignore[union-attr]
    assert shared["setup_line"].startswith("manthana setup mia_")


def test_revoking_is_scoped_to_the_callers_org() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-b")
    victim = client.post(f"{API}/invites", json={"org_id": "org-b"}).json()["code"]
    _as_founder(client, "org-a")

    client.post(f"{API}/invites/revoke", json={"org_id": "org-b", "code": victim})

    still = store.get_invite(victim)
    assert still is not None and still.uses_left > 0  # untouched


# ── tokens and promotion ───────────────────────────────────────────────────
def test_engineer_token_is_scoped_and_names_its_holder() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.post(
        f"{API}/engineer-token", json={"org_id": "org-b", "actor": "new@a.com"}
    ).json()

    claims = verify_engineer_token(_SECRET, body["token"])
    assert claims.org_id == "org-a"  # forced to the caller's, not org-b
    assert claims.actor == "new@a.com"


def test_engineer_token_requires_an_actor() -> None:
    """The token names a person so their wiki edits are attributable; an anonymous
    one would defeat the point."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")
    assert client.post(
        f"{API}/engineer-token", json={"org_id": "org-a", "actor": "  "}
    ).status_code == 422


def test_promotion_works_and_stays_inside_the_org() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    ok = client.post(
        f"{API}/members/promote", json={"org_id": "org-a", "identity_id": "google:org-a"}
    )
    cross = client.post(
        f"{API}/members/promote", json={"org_id": "org-a", "identity_id": "google:org-b"}
    )

    assert ok.status_code == 200
    assert store.get_identity("google:org-a").role == "founder"  # type: ignore[union-attr]
    assert cross.status_code == 404
    assert store.get_identity("google:org-b").role == "engineer"  # type: ignore[union-attr]


# ── mining ─────────────────────────────────────────────────────────────────
def test_mining_starts_a_run() -> None:
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.post(f"{API}/mine", json={"org_id": "org-a"}).json()

    assert body == {"org_id": "org-a", "started": True, "already_running": False}


def test_a_run_in_flight_blocks_a_second_one() -> None:
    """A second click would double the model spend for the same corpus.

    Asserted against the registry rather than through two HTTP calls, because
    TestClient drains background tasks before returning — so by the time a second
    request arrives the first run has already finished and is legitimately not
    running. An HTTP-level test here would pass whether or not the guard existed.
    """
    from manthana.server.mining import MineRun, MineRunRegistry

    reg = MineRunRegistry()
    assert reg.is_running("org-a") is False

    reg.start("org-a", MineRun(org_id="org-a"))

    assert reg.is_running("org-a") is True
    assert reg.is_running("org-b") is False  # per-org, not global


def test_no_run_yet_is_a_state_not_an_error() -> None:
    """Run state is in-process and deliberately not persisted, so a restart means
    'no run yet' — which the client must be able to render calmly."""
    client, _, store = _make()
    _seed(store)
    _as_founder(client, "org-a")

    body = client.get(f"{API}/mine-status", params={"org_id": "org-a"}).json()

    assert body["run"] is None
    assert body["pending_proposals"] == 0


# ── retiring the HTML console ──────────────────────────────────────────────
def test_retired_console_redirects_rather_than_404s() -> None:
    """/ui is printed in welcome emails, in the docs, and by the server on startup.
    A follower of one of those should land on the new page."""
    client, _, store = _make(retire_html_console=True)
    _seed(store)
    client.cookies.set(COOKIE, "adm")

    for old, new in (
        ("/ui", "/console"),
        ("/ui/sessions?org_id=org-a", "/console/sessions?org=org-a"),
        ("/ui/topics?org_id=org-a", "/console/topics?org=org-a"),
        ("/ui/router?org_id=org-a", "/console/cost?org=org-a"),
        ("/ui/digest?org_id=org-a", "/console/digest?org=org-a"),
        ("/ui/mine-status?org_id=org-a", "/console/mining?org=org-a"),
        ("/ui/session?org_id=org-a&compaction_id=org-a-c0", "/console/sessions/org-a-c0?org=org-a"),
    ):
        resp = client.get(old)
        assert resp.status_code == 303, old
        assert resp.headers["location"] == new, old


def test_the_html_console_is_still_there_when_not_retired() -> None:
    """The flag is what makes this revertable: off, the previous surface returns
    intact rather than having been deleted."""
    client, _, store = _make(retire_html_console=False)
    _seed(store)
    client.cookies.set(COOKIE, "adm")

    resp = client.get("/ui")

    assert resp.status_code == 200
    assert "Manthana" in resp.text


def test_sign_in_still_works_when_the_console_is_retired() -> None:
    """The retired routes are registered first and win for the paths they claim —
    everything else mount_ui provides has to keep working."""
    client, _, store = _make(retire_html_console=True)
    _seed(store)

    assert client.get("/ui/login").headers["location"] == "/login"
    assert client.post("/ui/api/wiki/login", json={"token": "adm"}).status_code == 200
