"""Org wiki console (`/ui/home`, project/person/note pages, teaching routes).

In-memory SQLite + in-memory object store — no model access on the read paths
(they are zero-LLM by design). Verifies the auth gate, per-founder tenant
scoping, note-body escaping, and that person pages are first-class (no k-anon
gate on this path).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from manthana.server.auth import issue_founder_token
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore

_NOW = datetime.now(UTC)


def _comp(
    cid: str,
    *,
    actor: str = "suraj@x.com",
    project: str = "bench",
    intent: str = "run the BIRD benchmark",
    days_ago: int = 1,
) -> EngineeringCompaction:
    at = _NOW - timedelta(days=days_ago)
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor=actor,
        surface=Surface.claude_code,
        project=project,
        started_at=at,
        ended_at=at,
        duration_seconds=60.0,
        task_intent=intent,
        approach="swept temperature",
        outcome=Outcome.success,
        est_cost_usd=1.0,
        released=True,
        source="full",
    )


def _note(
    nid: str = "kn-1",
    *,
    org_id: str = "o1",
    kind: NoteKind = NoteKind.decision,
    title: str = "Pin torch 2.4",
    body: str = "2.5 breaks the eval harness.",
    project: str = "bench",
    actors: list[str] | None = None,
    status: NoteStatus = NoteStatus.candidate,
    source: NoteSource = NoteSource.ai,
    **kw: object,
) -> KnowledgeNote:
    return KnowledgeNote(
        id=nid,
        org_id=org_id,
        kind=kind,
        title=title,
        body=body,
        scope=f"project:{project}",
        entities=NoteEntities(projects=[project]),
        actors=actors if actors is not None else ["suraj@x.com"],
        evidence=["c1"],
        status=status,
        source=source,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
        **kw,  # type: ignore[arg-type]
    )


def _make(response: str = "{}") -> tuple[TestClient, ServerStore, ServerConfig]:
    """A console client. ``response`` is what the model returns for every call —
    the ask path parses it as a filter first (non-JSON degrades to 'match all')
    and then uses it as the narrative."""
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
    store.ingest_compaction(_comp("c1"), org_id="o1", team_id="t1")
    store.ingest_compaction(
        _comp("c2", actor="mira@x.com", project="search", intent="ship bm25"),
        org_id="o1", team_id="t1",
    )
    store.upsert_note(_note("kn-1"))


def _login(client: TestClient, token: str = "adm") -> None:
    client.post("/ui/login", data={"token": token})


# ── auth gate ────────────────────────────────────────────────────────────
def test_wiki_routes_require_auth() -> None:
    client, store, _ = _make()
    _seed(store)
    for url in (
        "/ui/home",
        "/ui/page/project/bench",
        "/ui/page/person/suraj@x.com",
        "/ui/note/kn-1",
        "/ui/note/kn-1/history",
    ):
        resp = client.get(url)
        assert resp.status_code == 303, url
        assert resp.headers["location"] == "/ui/login"
        assert "Pin torch" not in resp.text  # nothing leaks to an anonymous caller


def test_founder_is_scoped_to_own_org_on_every_wiki_route() -> None:
    # A founder token for o1 must not be able to read o2 by asking for it.
    client, store, config = _make()
    _seed(store)
    store.create_org("o2", "Other")
    store.create_team("t2", "o2", "Team")
    store.ingest_compaction(
        _comp("c9", project="secretproject"), org_id="o2", team_id="t2"
    )
    store.upsert_note(_note("kn-9", org_id="o2", title="Other org's secret"))

    _login(client, issue_founder_token(config.jwt_secret, org_id="o1"))
    home = client.get("/ui/home?org_id=o2")
    assert home.status_code == 200
    assert "secretproject" not in home.text and "Other org's secret" not in home.text
    assert "bench" in home.text  # their own org's data instead

    note = client.get("/ui/note/kn-9?org_id=o2")
    assert note.status_code == 404  # o2's note is invisible, not merely unlisted


# ── home feed ────────────────────────────────────────────────────────────
def test_home_shows_projects_people_and_notes() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.get("/ui/home?org_id=o1")
    assert resp.status_code == 200
    assert "bench" in resp.text and "search" in resp.text  # project lines
    assert "suraj" in resp.text and "mira" in resp.text  # who's active
    assert "Pin torch 2.4" in resp.text  # this week's decisions
    assert "unreviewed" in resp.text  # the candidate note's badge
    assert "ask anything" in resp.text  # the Q&A box


def test_home_defaults_to_first_org_for_admin() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.get("/ui/home")  # no org_id
    assert resp.status_code == 200 and "bench" in resp.text


# ── project / person / note pages ────────────────────────────────────────
def test_project_page_renders_notes_and_sessions() -> None:
    client, store, _ = _make()
    _seed(store)
    store.upsert_note(_note("kn-2", kind=NoteKind.gotcha, title="Stale cache"))
    _login(client)
    resp = client.get("/ui/page/project/bench?org_id=o1")
    assert resp.status_code == 200
    assert "Pin torch 2.4" in resp.text and "Stale cache" in resp.text
    assert "Decision" in resp.text and "Gotcha" in resp.text  # grouped sections
    assert "run the BIRD benchmark" in resp.text  # recent sessions


def test_person_page_is_first_class_with_single_contributor() -> None:
    # The founder's flagship question is person-shaped; one contributor is enough
    # (no k-anonymity floor applies on the wiki path).
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.get("/ui/page/person/suraj@x.com?org_id=o1")
    assert resp.status_code == 200
    assert "Currently working on" in resp.text
    assert "run the BIRD benchmark" in resp.text  # live activity
    assert "Pin torch 2.4" in resp.text  # notes whose evidence names them
    assert "ship bm25" not in resp.text  # not their work


def test_note_page_shows_evidence_and_history_link() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.get("/ui/note/kn-1?org_id=o1")
    assert resp.status_code == 200
    assert "Pin torch 2.4" in resp.text
    assert "run the BIRD benchmark" in resp.text  # evidence session resolved
    assert "/ui/note/kn-1/history" in resp.text
    assert client.get("/ui/note/kn-ghost?org_id=o1").status_code == 404


def test_note_history_lists_every_version() -> None:
    client, store, _ = _make()
    _seed(store)
    v2 = _note("kn-2", body="Actually 2.5 is fine now.").model_copy(
        update={"version": 2, "supersedes": "kn-1", "source": NoteSource.human,
                "author": "founder"}
    )
    store.supersede_note("kn-1", v2, "o1")
    _login(client)
    resp = client.get("/ui/note/kn-2/history?org_id=o1")
    assert resp.status_code == 200
    assert "2.5 breaks the eval harness." in resp.text  # v1 body preserved
    assert "Actually 2.5 is fine now." in resp.text  # v2 body
    assert "founder" in resp.text  # author attribution


def test_disputed_note_shows_conflicting_evidence() -> None:
    client, store, _ = _make()
    _seed(store)
    store.upsert_note(
        _note("kn-d", title="Disputed claim", status=NoteStatus.disputed, disputed_by=["c2"])
    )
    _login(client)
    resp = client.get("/ui/note/kn-d?org_id=o1")
    assert resp.status_code == 200
    assert "disputed" in resp.text and "Conflicting evidence" in resp.text
    assert "ship bm25" in resp.text  # the contradicting session, resolved


def test_human_note_dispute_keeps_body_canonical() -> None:
    client, store, _ = _make()
    _seed(store)
    store.upsert_note(
        _note("kn-h", title="Human claim", body="We ship on Fridays.",
              source=NoteSource.human, status=NoteStatus.disputed, disputed_by=["c2"])
    )
    _login(client)
    resp = client.get("/ui/note/kn-h?org_id=o1")
    assert "We ship on Fridays." in resp.text
    assert "stands until a human resolves it" in resp.text
    assert "human" in resp.text  # source badge


# ── ask (notes-first Q&A) ────────────────────────────────────────────────
def test_ui_ask_renders_answer_with_linked_citations() -> None:
    client, store, _ = _make("Pinned torch [kn-1] after a session [c1].")
    _seed(store)
    _login(client)
    resp = client.post("/ui/ask", data={"org_id": "o1", "query": "why torch 2.4?"})
    assert resp.status_code == 200
    assert "Pinned torch" in resp.text
    assert "/ui/note/kn-1" in resp.text  # note citation linked to its page
    assert "compaction_id=c1" in resp.text  # session citation linked to the digest
    assert "note(s)" in resp.text  # coverage line


def test_ui_ask_requires_auth() -> None:
    client, store, _ = _make()
    _seed(store)
    resp = client.post("/ui/ask", data={"org_id": "o1", "query": "hi"})
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/login"


def test_api_ask_is_org_scoped_and_audited() -> None:
    client2, store, config = _make("Answer [kn-1].")
    _seed(store)
    resp = client2.post(
        "/v1/founder/ask",
        json={"org_id": "o1", "query": "why torch 2.4?"},
        headers={"x-admin-token": "adm"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["note_citations"] == ["kn-1"]
    assert payload["insufficient_data"] is False
    assert store.list_founder_audit("o1")  # the ask is on the record

    # A founder token for another org is refused.
    other = issue_founder_token(config.jwt_secret, org_id="o2")
    denied = client2.post(
        "/v1/founder/ask",
        json={"org_id": "o1", "query": "x"},
        headers={"authorization": f"Bearer {other}"},
    )
    assert denied.status_code == 403


# ── teaching (write paths) ───────────────────────────────────────────────
def test_teaching_routes_require_auth_and_write_nothing() -> None:
    client, store, _ = _make()
    _seed(store)
    posts = {
        "/ui/note/edit": {"org_id": "o1", "note_id": "kn-1", "title": "t", "body": "b"},
        "/ui/note/new": {"org_id": "o1", "kind": "decision", "title": "t", "body": "b"},
        "/ui/note/confirm": {"org_id": "o1", "note_id": "kn-1"},
        "/ui/note/revert": {"org_id": "o1", "note_id": "kn-1", "to_version_id": "kn-1"},
    }
    for url, data in posts.items():
        resp = client.post(url, data=data)
        assert resp.status_code == 303 and resp.headers["location"] == "/ui/login", url
    note = store.get_note("kn-1", "o1")
    assert note is not None and note.source == NoteSource.ai  # untouched
    assert len(store.query_notes("o1")) == 1  # nothing created


def test_edit_creates_human_version_and_supersedes() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "Pin torch 2.6",
              "body": "2.5 was the problem, 2.6 is fine.",
              "back": "/ui/page/project/bench?org_id=o1"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/page/project/bench?org_id=o1"  # back where we were
    live = store.query_notes("o1")
    assert len(live) == 1
    assert live[0].source == NoteSource.human and live[0].author == "admin"
    assert live[0].title == "Pin torch 2.6" and live[0].version == 2
    old = store.get_note("kn-1", "o1")
    assert old is not None and old.status == NoteStatus.superseded


def test_edit_redirect_refuses_offsite_back_url() -> None:
    # `back` is caller-supplied; it must never become an open redirect.
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "t", "body": "b",
              "back": "https://evil.example/steal"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/")


def test_confirm_marks_established_without_new_version() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    resp = client.post("/ui/note/confirm", data={"org_id": "o1", "note_id": "kn-1"})
    assert resp.status_code == 303
    note = store.get_note("kn-1", "o1")
    assert note is not None
    assert note.status == NoteStatus.established and note.confirmed_by == "admin"
    assert note.version == 1  # endorsement, not an edit
    # And the page now shows the confirmed badge instead of a Confirm button.
    page = client.get("/ui/note/kn-1?org_id=o1")
    assert "confirmed" in page.text and "Confirm</button>" not in page.text


def test_new_note_is_created_and_validated() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    bad = client.post(
        "/ui/note/new",
        data={"org_id": "o1", "kind": "not-a-kind", "title": "t", "body": "b"},
    )
    assert bad.status_code == 422
    blank = client.post(
        "/ui/note/new", data={"org_id": "o1", "kind": "convention", "title": " ", "body": ""}
    )
    assert blank.status_code == 422
    assert len(store.query_notes("o1")) == 1  # neither wrote anything

    ok = client.post(
        "/ui/note/new",
        data={"org_id": "o1", "kind": "convention", "title": "Deploy Tuesdays",
              "body": "Never on Friday.", "project": "bench"},
    )
    assert ok.status_code == 303
    created = [n for n in store.query_notes("o1") if n.title == "Deploy Tuesdays"]
    assert len(created) == 1 and created[0].source == NoteSource.human


def test_revert_restores_text_as_new_version() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "Bad edit", "body": "wrong"},
    )
    bad = store.query_notes("o1")[0]
    resp = client.post(
        "/ui/note/revert",
        data={"org_id": "o1", "note_id": bad.id, "to_version_id": "kn-1"},
    )
    assert resp.status_code == 303
    restored = store.query_notes("o1")[0]
    assert restored.body == "2.5 breaks the eval harness."  # the original text
    assert restored.version == 3  # appended, not rewound
    assert len(store.note_history(restored.id, "o1")) == 3


def test_history_page_offers_revert_for_old_versions_only() -> None:
    client, store, _ = _make()
    _seed(store)
    _login(client)
    client.post(
        "/ui/note/edit",
        data={"org_id": "o1", "note_id": "kn-1", "title": "v2", "body": "second"},
    )
    current = store.query_notes("o1")[0]
    page = client.get(f"/ui/note/{current.id}/history?org_id=o1")
    assert page.status_code == 200
    assert page.text.count("Revert to this") == 1  # only the superseded version
    assert "current" in page.text


def test_founder_cannot_teach_another_org() -> None:
    client, store, config = _make()
    _seed(store)
    store.create_org("o2", "Other")
    store.upsert_note(_note("kn-9", org_id="o2", title="Other org's note"))
    _login(client, issue_founder_token(config.jwt_secret, org_id="o1"))
    resp = client.post(
        "/ui/note/edit",
        data={"org_id": "o2", "note_id": "kn-9", "title": "hijacked", "body": "x"},
    )
    # Scoped to their own org, where kn-9 does not exist.
    assert resp.status_code == 404
    untouched = store.get_note("kn-9", "o2")
    assert untouched is not None and untouched.title == "Other org's note"


# ── escaping ─────────────────────────────────────────────────────────────
def test_note_bodies_are_html_escaped() -> None:
    # Note text is written by a model AND by humans — neither may inject markup.
    client, store, _ = _make()
    _seed(store)
    store.upsert_note(
        _note("kn-x", title="<img src=x onerror=alert(1)>",
              body="<script>alert('xss')</script>")
    )
    _login(client)
    for url in ("/ui/note/kn-x?org_id=o1", "/ui/page/project/bench?org_id=o1",
                "/ui/note/kn-x/history?org_id=o1"):
        resp = client.get(url)
        # The property that matters: no attacker-controlled TAG can form. The
        # payload text may appear, but only with its angle brackets escaped.
        assert "<script>" not in resp.text, url
        assert "<img " not in resp.text, url
        assert "&lt;script&gt;" in resp.text, url
        assert "&lt;img src=x onerror=alert(1)&gt;" in resp.text, url
