"""Retiring the legacy HTML wiki (`config.retire_html_wiki`).

Exactly one wiki renders at a time. With the flag OFF (the default) the
server-rendered wiki is untouched — `test_wiki_ui.py` covers it. With the flag ON
its pages become 303s into the Next.js client, and its form-POST endpoints become
410s rather than redirects that would quietly drop a submitted body.

The flag defaults OFF because the client is a SEPARATE deployable: enabling it
where nothing serves the client turns a working wiki into a 404. These tests pin
the default as much as the behaviour.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_engineer_token
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore

_NOW = datetime.now(UTC)
ENG = "suraj@x.com"


def _comp(cid: str = "c1") -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=ENG,
        surface=Surface.claude_code,
        project="bench",
        started_at=_NOW - timedelta(days=1),
        ended_at=_NOW - timedelta(days=1),
        duration_seconds=60.0,
        task_intent="run the BIRD benchmark",
        approach="swept temperature",
        outcome=Outcome.success,
        released=True,
        source="full",
    )


def _make(*, retired: bool) -> tuple[TestClient, ServerStore, ServerConfig]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", retire_html_wiki=retired)
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    store.ingest_compaction(_comp(), org_id="o1", team_id="t1")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    return client, store, config


def _login(client: TestClient, config: ServerConfig) -> None:
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor=ENG)
    client.post("/ui/login", data={"token": token})


# ── the default ──────────────────────────────────────────────────────────
def test_html_wiki_is_live_by_default() -> None:
    # The client is a separate deployable; a server that turned this on for
    # itself would replace a working wiki with a 404.
    assert ServerConfig(jwt_secret="x" * 40, admin_token="adm").retire_html_wiki is False


def test_default_still_renders_the_html_wiki() -> None:
    client, _store, config = _make(retired=False)
    _login(client, config)
    resp = client.get("/ui/home?org_id=o1")
    assert resp.status_code == 200
    assert "bench" in resp.text  # real rendered content, not a redirect


# ── retired: pages redirect ──────────────────────────────────────────────
def test_every_wiki_page_redirects_to_its_client_route() -> None:
    client, _store, config = _make(retired=True)
    _login(client, config)
    for url, target in (
        ("/ui/home", "/"),
        ("/ui/page/project/bench", "/projects/bench"),
        (f"/ui/page/person/{ENG}", f"/people/{ENG.replace('@', '%40')}"),
        ("/ui/note/kn-1", "/notes/kn-1"),
        ("/ui/note/kn-1/history", "/notes/kn-1/history"),
    ):
        resp = client.get(url)
        assert resp.status_code == 303, url
        assert resp.headers["location"] == target, url


def test_redirect_targets_are_same_origin_relative_paths() -> None:
    # The session cookie is httponly and path-scoped, so the client MUST be on
    # this origin; an absolute URL here would invite a deployment that cannot
    # authenticate at all.
    client, _store, config = _make(retired=True)
    _login(client, config)
    for url in ("/ui/home", "/ui/page/project/bench", "/ui/note/kn-1"):
        location = client.get(url).headers["location"]
        assert location.startswith("/"), location
        assert "://" not in location, location


def test_path_segments_are_escaped_into_the_redirect() -> None:
    # Unescaped, a project named "a?b" would emit Location: /projects/a?b — the
    # tail becomes a query string and the client looks up the wrong project. "#"
    # is worse: everything after it is a fragment the server never sees.
    client, _store, config = _make(retired=True)
    _login(client, config)
    for raw, escaped in (("a%3Fb", "a%3Fb"), ("a%23b", "a%23b"), ("a%20b", "a%20b")):
        resp = client.get(f"/ui/page/project/{raw}")
        assert resp.status_code == 303, raw
        assert resp.headers["location"] == f"/projects/{escaped}", raw


def test_old_links_redirect_even_when_signed_out() -> None:
    # A bookmark or Slack link should land on the client (which will send the
    # reader to its own login), not on the retired server's login form.
    client, _store, _config = _make(retired=True)
    assert client.get("/ui/home").headers["location"] == "/"


# ── retired: writes are gone, not silently swallowed ─────────────────────
def test_form_writes_report_gone_rather_than_dropping_the_body() -> None:
    # A 303 would turn these into a GET and discard the submitted note, which
    # reads to a user as a save that vanished.
    client, _store, config = _make(retired=True)
    _login(client, config)
    for url in (
        "/ui/ask",
        "/ui/note/edit",
        "/ui/note/new",
        "/ui/note/confirm",
        "/ui/note/revert",
    ):
        resp = client.post(url, data={"org_id": "o1"})
        assert resp.status_code == 410, url


# ── what must keep working ───────────────────────────────────────────────
def test_the_json_api_is_unaffected_by_retirement() -> None:
    client, _store, config = _make(retired=True)
    token = issue_engineer_token(config.jwt_secret, org_id="o1", actor=ENG)
    assert client.post("/ui/api/wiki/login", json={"token": token}).status_code == 200
    home = client.get("/ui/api/wiki/home")
    assert home.status_code == 200
    assert home.json()["stream"][0]["task_intent"] == "run the BIRD benchmark"


def test_founder_console_survives_retirement() -> None:
    client, _store, _config = _make(retired=True)
    client.post("/ui/login", data={"token": "adm"})
    console = client.get("/ui")
    assert console.status_code == 200
    assert "Manthana" in console.text


def test_engineers_are_still_bounced_off_oversight_pages() -> None:
    # The bounce target is /ui/home, which now 303s on to the client — the chain
    # must still start, or engineers would reach a founder-only page.
    client, _store, config = _make(retired=True)
    _login(client, config)
    resp = client.get("/ui/sessions?org_id=o1")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/home"
    assert client.get("/ui/home").headers["location"] == "/"
