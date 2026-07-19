"""Engineer console logins — the team can teach the wiki.

Three roles now share one cookie login. The boundary under test: an engineer gets
the WIKI (read + teach, attributed to them by name) and is kept out of the
founder's oversight surfaces (cost, mining, audit, digests, session browsing).
Scope separation is also load-bearing — an agent's SYNC token must never work as
a browser login, and vice versa.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import (
    EngineeringCompaction,
    KnowledgeNote,
    NoteEntities,
    NoteKind,
    NoteSource,
    NoteStatus,
    Outcome,
    Surface,
)
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import (
    AuthError,
    issue_engineer_token,
    issue_founder_token,
    issue_team_token,
    verify_engineer_token,
    verify_team_token,
)
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore

_NOW = datetime.now(UTC)
ENG = "priya@acme.test"


def _comp(cid: str = "c1", *, actor: str = ENG) -> EngineeringCompaction:
    at = _NOW - timedelta(days=1)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project="bench",
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent="run the BIRD benchmark",
        approach="swept temperature",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def _note(nid: str = "kn-1", *, org_id: str = "o1", title: str = "Pin torch 2.4") -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id=org_id,
        kind=NoteKind.decision,
        title=title,
        body="2.5 breaks the eval harness.",
        scope="project:bench",
        entities=NoteEntities(projects=["bench"]),
        actors=[ENG],
        evidence=["c1"],
        source=NoteSource.ai,
        status=NoteStatus.candidate,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
    )


def _make(response: str = "{}") -> tuple[TestClient, ServerStore, ServerConfig]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), MockProvider(response)),
        follow_redirects=False,
    )
    return client, store, config


def _seed(store: ServerStore) -> None:
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.ingest_compaction(_comp(), org_id="o1", team_id="t1")
    store.upsert_note(_note())


def _login(client: TestClient, token: str) -> None:
    client.post("/ui/login", data={"token": token})


def _engineer(config: ServerConfig, actor: str = ENG, org: str = "o1") -> str:
    return issue_engineer_token(config.jwt_secret, org_id=org, actor=actor)


# ── token scopes are mutually exclusive ──────────────────────────────────
def test_engineer_token_carries_identity() -> None:
    token = issue_engineer_token("s" * 40, org_id="o1", actor=ENG)
    claims = verify_engineer_token("s" * 40, token)
    assert claims.org_id == "o1" and claims.actor == ENG


def test_engineer_token_requires_an_actor() -> None:
    # An anonymous "engineer" would make teaching unattributable.
    with pytest.raises(ValueError):
        issue_engineer_token("s" * 40, org_id="o1", actor="")


def test_scopes_do_not_cross() -> None:
    secret = "s" * 40
    agent = issue_team_token(secret, org_id="o1", team_id="t1", actor=ENG)
    engineer = issue_engineer_token(secret, org_id="o1", actor=ENG)
    founder = issue_founder_token(secret, org_id="o1")
    # A laptop sync credential must not double as a browser login...
    with pytest.raises(AuthError):
        verify_engineer_token(secret, agent)
    # ...and a browser login must not be usable to sync data as an agent.
    with pytest.raises(AuthError):
        verify_team_token(secret, engineer)
    with pytest.raises(AuthError):
        verify_engineer_token(secret, founder)


def test_agent_token_is_not_a_console_login() -> None:
    client, store, config = _make()
    _seed(store)
    agent = issue_team_token(config.jwt_secret, org_id="o1", team_id="t1", actor=ENG)
    resp = client.post("/ui/login", data={"token": agent})
    assert resp.status_code == 401
    assert client.get("/ui/home").status_code == 303  # still signed out


# ── engineers get the wiki ───────────────────────────────────────────────
def test_engineer_can_read_the_wiki() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    for url in ("/ui/home", "/ui/page/project/bench", f"/ui/page/person/{ENG}", "/ui/note/kn-1"):
        resp = client.get(url)
        assert resp.status_code == 200, url
    assert "Pin torch 2.4" in client.get("/ui/home").text


def test_engineer_sees_who_they_are_signed_in_as() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    assert ENG in client.get("/ui/home").text


# ── engineers can TEACH, attributed by name ──────────────────────────────
def test_engineer_edit_is_authoritative_and_attributed() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "Pin torch 2.6",
              "body": "2.6 fixed it — I hit this last week."},
    )
    assert resp.status_code == 303
    live = store.query_notes("o1")[0]
    assert live.source == NoteSource.human
    assert live.author == ENG  # the colleague's own name, not "founder"/"admin"
    assert live.title == "Pin torch 2.6" and live.version == 2


def test_engineer_can_confirm_and_add_notes() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    client.post("/ui/note/confirm", data={"org_id": "o1", "note_id": "kn-1"})
    confirmed = store.get_note("kn-1", "o1")
    assert confirmed is not None and confirmed.confirmed_by == ENG

    client.post(
        "/ui/note/new",
        data={"org_id": "o1", "kind": "gotcha", "title": "Eval cache goes stale",
              "body": "Delete .cache/preds before a rerun."},
    )
    added = [n for n in store.query_notes("o1") if n.title == "Eval cache goes stale"]
    assert len(added) == 1 and added[0].author == ENG


def test_two_engineers_teaching_is_attributable_per_person() -> None:
    # The point of team teaching: history shows WHO corrected what.
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config, ENG))
    client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "v2", "body": "priya's fix"},
    )
    v2 = store.query_notes("o1")[0]
    client.post("/ui/logout")
    _login(client, _engineer(config, "dev@acme.test"))
    client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": v2.id, "title": "v3", "body": "dev's correction"},
    )
    chain = store.note_history(store.query_notes("o1")[0].id, "o1")
    assert [n.author for n in chain] == ["dev@acme.test", ENG, None]


# ── engineers do NOT get the founder's oversight surfaces ────────────────
def test_engineer_is_redirected_away_from_oversight_pages() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    for url in (
        "/ui",
        "/ui/sessions?org_id=o1",
        "/ui/router?org_id=o1",
        "/ui/digest?org_id=o1",
        "/ui/topics?org_id=o1",
        "/ui/mine-status?org_id=o1",
    ):
        resp = client.get(url)
        assert resp.status_code == 303, url
        assert resp.headers["location"] == "/ui/home", url
        assert "BIRD" not in resp.text  # no oversight data leaks in the redirect


def test_engineer_cannot_start_org_skill_mining() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post("/ui/mine", data={"org_id": "o1"})
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/home"
    assert store.list_queue("o1") == []  # nothing was mined


def test_engineer_cannot_mint_tokens_for_the_team() -> None:
    # Minting is a founder act; an engineer escalating themselves would defeat
    # the whole role split.
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post("/ui/engineer-token", data={"org_id": "o1", "actor": "x@acme.test"})
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/home"


# ── tenant isolation still holds for the new role ────────────────────────
def test_engineer_is_locked_to_their_own_org() -> None:
    client, store, config = _make()
    _seed(store)
    store.create_org("o2", "Other")
    store.upsert_note(_note("kn-9", org_id="o2", title="Other org's secret"))
    _login(client, _engineer(config, ENG, org="o1"))
    home = client.get("/ui/home?org_id=o2")
    assert home.status_code == 200 and "Other org's secret" not in home.text
    assert client.get("/ui/note/kn-9?org_id=o2").status_code == 404
    # And they cannot teach into the other org either.
    resp = client.post(
        "/ui/note/edit",
        data={"org_id": "o2", "note_id": "kn-9", "title": "hijacked", "body": "x"},
    )
    assert resp.status_code == 404
    untouched = store.get_note("kn-9", "o2")
    assert untouched is not None and untouched.title == "Other org's secret"


# ── founders onboard their own team ──────────────────────────────────────
def test_founder_can_mint_an_engineer_login_from_the_console() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, issue_founder_token(config.jwt_secret, org_id="o1"))
    page = client.get("/ui")
    assert "Team access to the wiki" in page.text

    resp = client.post("/ui/engineer-token", data={"org_id": "o1", "actor": ENG})
    assert resp.status_code == 200
    assert "/ui/login" in resp.text  # the link to send them
    assert "Shown once" in resp.text  # honest about not storing it

    # The minted token must actually work as a wiki login.
    token = resp.text.split("<pre>")[1].split("</pre>")[0].strip()
    fresh = TestClient(
        create_app(config, store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    _login(fresh, token)
    assert fresh.get("/ui/home").status_code == 200


def test_founder_minting_is_scoped_to_their_org() -> None:
    client, store, config = _make()
    _seed(store)
    store.create_org("o2", "Other")
    _login(client, issue_founder_token(config.jwt_secret, org_id="o1"))
    resp = client.post("/ui/engineer-token", data={"org_id": "o2", "actor": "spy@acme.test"})
    assert resp.status_code == 200
    token = resp.text.split("<pre>")[1].split("</pre>")[0].strip()
    # Forced back to the founder's own org, not the one they asked for.
    assert verify_engineer_token(config.jwt_secret, token).org_id == "o1"


def test_api_engineer_token_endpoint_rejects_other_orgs() -> None:
    client, store, config = _make()
    _seed(store)
    founder = issue_founder_token(config.jwt_secret, org_id="o1")
    ok = client.post(
        "/v1/engineer-tokens",
        json={"org_id": "o1", "actor": ENG},
        headers={"authorization": f"Bearer {founder}"},
    )
    assert ok.status_code == 200 and ok.json()["actor"] == ENG

    denied = client.post(
        "/v1/engineer-tokens",
        json={"org_id": "o2", "actor": ENG},
        headers={"authorization": f"Bearer {founder}"},
    )
    assert denied.status_code == 403

    blank = client.post(
        "/v1/engineer-tokens",
        json={"org_id": "o1", "actor": "  "},
        headers={"x-admin-token": "adm"},
    )
    assert blank.status_code == 422
