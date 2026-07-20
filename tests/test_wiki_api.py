"""Wiki JSON API (``/ui/api/wiki/*``) — the surface the browser client reads.

Covers the three things that make it a shared context rather than a founder
dashboard: engineers may browse org-wide session digests, raw transcripts stay
unreachable, and home sections follow the data instead of a hardcoded taxonomy.
Plus the usual invariants — auth, tenant scoping, CSRF shape on writes, and that
the teaching verbs behave identically to the HTML wiki's.

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
from manthana.server.auth import issue_engineer_token, issue_founder_token
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore

API = "/ui/api/wiki"
_NOW = datetime.now(UTC)
ENG = "suraj@x.com"


def _comp(
    cid: str,
    *,
    actor: str = ENG,
    project: str = "bench",
    intent: str = "run the BIRD benchmark",
    days_ago: int = 1,
    files: list[str] | None = None,
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
        files_touched=files or [],
    )


def _note(
    nid: str = "kn-1",
    *,
    org_id: str = "o1",
    kind: NoteKind = NoteKind.decision,
    title: str = "Pin torch 2.4",
    project: str = "bench",
    actors: list[str] | None = None,
    status: NoteStatus = NoteStatus.candidate,
    source: NoteSource = NoteSource.ai,
    days_ago: int = 1,
    **kw: object,
) -> KnowledgeNote:
    at = _NOW - timedelta(days=days_ago)
    return KnowledgeNote(
        id=nid,
        org_id=org_id,
        kind=kind,
        title=title,
        body="2.5 breaks the eval harness.",
        scope=f"project:{project}",
        entities=NoteEntities(projects=[project]),
        actors=actors if actors is not None else [ENG],
        evidence=["c1"],
        status=status,
        source=source,
        created_at=at,
        updated_at=at,
        **kw,  # type: ignore[arg-type]
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
    store.upsert_actor(ENG, org_id="o1", team_id="t1", display_name="Suraj")
    store.ingest_compaction(_comp("c1", files=["src/core.py"]), org_id="o1", team_id="t1")
    store.ingest_compaction(
        _comp("c2", actor="mira@x.com", project="search", intent="ship bm25", days_ago=2),
        org_id="o1", team_id="t1",
    )
    store.upsert_note(_note("kn-1"))


def _login(client: TestClient, token: str = "adm") -> None:
    resp = client.post(f"{API}/login", json={"token": token})
    assert resp.status_code == 200, resp.text


def _engineer(config: ServerConfig, org: str = "o1", actor: str = ENG) -> str:
    return issue_engineer_token(config.jwt_secret, org_id=org, actor=actor)


# ── auth ─────────────────────────────────────────────────────────────────
def test_every_read_route_requires_a_session() -> None:
    client, store, _ = _make()
    _seed(store)
    for url in (
        f"{API}/me",
        f"{API}/home",
        f"{API}/people",
        f"{API}/people/{ENG}",
        f"{API}/projects",
        f"{API}/projects/bench",
        f"{API}/sessions",
        f"{API}/sessions/c1",
        f"{API}/notes",
        f"{API}/notes/kn-1",
        f"{API}/notes/kn-1/history",
    ):
        resp = client.get(url)
        assert resp.status_code == 401, url
        assert "Pin torch" not in resp.text  # nothing leaks to an anonymous caller


def test_bad_token_is_rejected_and_sets_no_cookie() -> None:
    client, store, _ = _make()
    _seed(store)
    resp = client.post(f"{API}/login", json={"token": "wrong"})
    assert resp.status_code == 401
    assert "manthana_admin" not in resp.cookies


def test_login_issues_a_cookie_scoped_to_the_ui_path() -> None:
    client, store, config = _make()
    _seed(store)
    resp = client.post(f"{API}/login", json={"token": _engineer(config)})
    assert resp.status_code == 200
    assert resp.json()["role"] == "engineer"
    assert resp.json()["actor"] == ENG
    # path=/ui is what lets one cookie serve both the HTML console and this API.
    assert "Path=/ui" in resp.headers["set-cookie"]


def test_founder_cannot_read_another_org_by_asking_for_it() -> None:
    client, store, config = _make()
    _seed(store)
    store.create_org("o2", "Other")
    store.create_team("t2", "o2", "Team")
    store.ingest_compaction(_comp("c9", project="secretproject"), org_id="o2", team_id="t2")
    store.upsert_note(_note("kn-9", org_id="o2", title="Other org's secret"))

    _login(client, issue_founder_token(config.jwt_secret, org_id="o1"))
    home = client.get(f"{API}/home?org_id=o2")
    assert home.status_code == 200
    assert "secretproject" not in home.text and "Other org's secret" not in home.text
    assert home.json()["org_id"] == "o1"
    assert client.get(f"{API}/notes/kn-9?org_id=o2").status_code == 404
    assert client.get(f"{API}/sessions/c9?org_id=o2").status_code == 404


# ── the product decision: engineers see org-wide digests ─────────────────
def test_engineer_browses_every_colleagues_session_digest() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.get(f"{API}/sessions")
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["items"]}
    assert ids == {"c1", "c2"}  # including mira's, which the console hid from them
    actors = {s["actor"] for s in resp.json()["items"]}
    assert actors == {ENG, "mira@x.com"}


def test_session_detail_carries_the_digest_but_never_raw_turns() -> None:
    client, store, config = _make()
    _seed(store)
    store.record_raw("c1", org_id="o1", object_key="o1/t1/c1.jsonl")
    _login(client, _engineer(config))
    resp = client.get(f"{API}/sessions/c1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session"]["task_intent"] == "run the BIRD benchmark"
    assert payload["session"]["approach"] == "swept temperature"
    # The tier-2 drill-down stays the audited founder endpoint; nothing here
    # exposes the transcript or even its object key.
    assert "turns" not in payload and "object_key" not in resp.text
    assert "jsonl" not in resp.text


def test_session_detail_exposes_the_released_digest_verbatim() -> None:
    # The verbatim page reads these. native_summary is redacted on the way off
    # the laptop (it is not in the redactor's KEEP set), so it is exactly as
    # shareable as `approach` — unlike the raw transcript, which is not here.
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    payload = client.get(f"{API}/sessions/c1").json()
    assert "native_summary" in payload
    assert "source" in payload
    assert payload["session"]["approach"] == "swept temperature"


def test_sessions_filter_by_actor_and_project() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    by_actor = client.get(f"{API}/sessions?actor=mira@x.com").json()["items"]
    assert [s["id"] for s in by_actor] == ["c2"]
    by_project = client.get(f"{API}/sessions?project=bench").json()["items"]
    assert [s["id"] for s in by_project] == ["c1"]


def test_sessions_paginate_with_a_cursor() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    first = client.get(f"{API}/sessions?limit=1").json()
    assert len(first["items"]) == 1
    assert first["next_cursor"] is not None
    second = client.get(f"{API}/sessions?limit=1&until={first['next_cursor']}").json()
    assert second["items"][0]["id"] != first["items"][0]["id"]


def test_a_short_page_offers_no_cursor() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    assert client.get(f"{API}/sessions?limit=50").json()["next_cursor"] is None


# ── home: a discovery feed, not a fixed taxonomy ─────────────────────────
def test_home_streams_recent_sessions_for_discovery() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    feed = client.get(f"{API}/home").json()
    stream = {s["id"]: s for s in feed["stream"]}
    assert set(stream) == {"c1", "c2"}
    # Each stream item must be self-describing enough to browse without a click.
    assert stream["c2"]["task_intent"] == "ship bm25"
    assert stream["c2"]["actor"] == "mira@x.com"
    assert stream["c2"]["project"] == "search"


def test_home_sections_follow_the_data_not_a_hardcoded_list() -> None:
    client, store, config = _make()
    _seed(store)  # a decision note only
    _login(client, _engineer(config))
    kinds = [s["kind"] for s in client.get(f"{API}/home").json()["sections"]]
    assert kinds == ["decision"]
    # benchmark is not a privileged section — it appears only once one exists.
    store.upsert_note(_note("kn-2", kind=NoteKind.benchmark, title="BIRD 64%", value="64%"))
    kinds = [s["kind"] for s in client.get(f"{API}/home").json()["sections"]]
    assert kinds == ["decision", "benchmark"]


def test_benchmark_notes_keep_their_delta_as_a_rendering_hint() -> None:
    client, store, config = _make()
    _seed(store)
    old = _note("kn-old", kind=NoteKind.benchmark, title="BIRD", value="61%")
    store.upsert_note(old)
    new = _note("kn-new", kind=NoteKind.benchmark, title="BIRD", value="64%")
    store.supersede_note("kn-old", new, "o1")
    _login(client, _engineer(config))
    benchmarks = client.get(f"{API}/home").json()["benchmarks"]
    assert benchmarks["kn-new"]["previous_value"] == "61%"


def test_home_counts_unreviewed_ai_notes() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    assert client.get(f"{API}/home").json()["unreviewed"] == 1


# ── people, projects, and the connections between them ───────────────────
def test_people_index_separates_active_from_quiet() -> None:
    client, store, config = _make()
    _seed(store)
    store.upsert_actor("quiet@x.com", org_id="o1", team_id="t1", display_name="Quiet")
    _login(client, _engineer(config))
    payload = client.get(f"{API}/people").json()
    assert {a["actor"] for a in payload["active"]} == {ENG, "mira@x.com"}
    assert [q["actor"] for q in payload["quiet"]] == ["quiet@x.com"]


def test_person_page_links_to_collaborators_with_a_reason() -> None:
    client, store, config = _make()
    _seed(store)
    # Give the two of them a shared project so an edge exists at all.
    store.ingest_compaction(
        _comp("c3", actor="mira@x.com", project="bench"), org_id="o1", team_id="t1"
    )
    _login(client, _engineer(config))
    payload = client.get(f"{API}/people/{ENG}").json()
    edges = payload["connections"]
    assert [e["actor"] for e in edges] == ["mira@x.com"]
    assert edges[0]["via_projects"] == ["bench"]
    assert payload["sections"][0]["kind"] == "decision"  # their work's knowledge


def test_edges_serialize_the_fields_the_client_renders_from() -> None:
    # Regression guard: dataclass @property values are dropped by asdict(), so
    # anything the client reads must be a real field. A missing key here renders
    # as "undefined" in the panel rather than failing loudly.
    client, store, config = _make()
    _seed(store)
    store.ingest_compaction(
        _comp("c3", actor="mira@x.com", project="bench"), org_id="o1", team_id="t1"
    )
    _login(client, _engineer(config))
    edge = client.get(f"{API}/people/{ENG}").json()["connections"][0]
    assert set(edge) >= {
        "actor", "weight", "shared_projects", "shared_notes", "shared_files",
        "via_projects", "via_notes", "via_files",
    }


def test_benchmark_delta_exposes_previous_value_as_a_field() -> None:
    client, store, config = _make()
    _seed(store)
    store.upsert_note(_note("kn-b", kind=NoteKind.benchmark, title="BIRD", value="61%"))
    store.supersede_note(
        "kn-b", _note("kn-b2", kind=NoteKind.benchmark, title="BIRD", value="64%"), "o1"
    )
    _login(client, _engineer(config))
    delta = client.get(f"{API}/home").json()["benchmarks"]["kn-b2"]
    assert "previous_value" in delta and "note" in delta


def test_project_page_links_to_sibling_projects() -> None:
    client, store, config = _make()
    _seed(store)
    store.ingest_compaction(
        _comp("c3", actor=ENG, project="search"), org_id="o1", team_id="t1"
    )
    _login(client, _engineer(config))
    payload = client.get(f"{API}/projects/bench").json()
    assert [n["project"] for n in payload["neighbors"]] == ["search"]
    assert payload["neighbors"][0]["via_actors"] == [ENG]


def test_projects_index_lists_active_and_dormant_projects() -> None:
    client, store, config = _make()
    _seed(store)
    store.ingest_compaction(
        _comp("c3", project="legacy", days_ago=400), org_id="o1", team_id="t1"
    )
    _login(client, _engineer(config))
    payload = client.get(f"{API}/projects").json()
    assert {p["project"] for p in payload["active"]} == {"bench", "search"}
    assert payload["quiet"] == ["legacy"]  # old, but still reachable


# ── knowledge browse: all-time, not just this week ───────────────────────
def test_notes_browse_reaches_knowledge_older_than_the_feed_window() -> None:
    client, store, config = _make()
    _seed(store)
    store.upsert_note(_note("kn-old", title="Ancient convention", kind=NoteKind.convention,
                            days_ago=300))
    _login(client, _engineer(config))
    # The home feed is a week wide, so the old note is absent there…
    home_titles = {
        n["title"] for s in client.get(f"{API}/home").json()["sections"] for n in s["notes"]
    }
    assert "Ancient convention" not in home_titles
    # …but browsing by kind finds it.
    browsed = client.get(f"{API}/notes?kind=convention").json()["items"]
    assert [n["title"] for n in browsed] == ["Ancient convention"]


def test_notes_browse_paginates_with_an_updated_at_cursor() -> None:
    client, store, config = _make()
    _seed(store)
    store.upsert_note(_note("kn-2", title="Second", days_ago=5))
    _login(client, _engineer(config))
    first = client.get(f"{API}/notes?limit=1").json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get(f"{API}/notes?limit=1&until={first['next_cursor']}").json()
    assert second["items"][0]["id"] != first["items"][0]["id"]


def test_unknown_note_kind_is_a_422_not_an_empty_list() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    assert client.get(f"{API}/notes?kind=nonsense").status_code == 422


def test_note_detail_resolves_evidence_to_real_sessions() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    payload = client.get(f"{API}/notes/kn-1").json()
    assert payload["note"]["title"] == "Pin torch 2.4"
    assert [c["id"] for c in payload["evidence"]] == ["c1"]


# ── teaching verbs ───────────────────────────────────────────────────────
def test_engineer_edit_creates_a_human_version_attributed_to_them() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post(
        f"{API}/notes/kn-1/edit", json={"title": "Pin torch 2.4.1", "body": "2.5 still breaks."}
    )
    assert resp.status_code == 200
    note = resp.json()["note"]
    assert note["source"] == "human"
    assert note["author"] == ENG  # the colleague who corrected it, not "engineer"
    assert note["supersedes"] == "kn-1"


def test_confirm_vouches_without_creating_a_version() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    note = client.post(f"{API}/notes/kn-1/confirm", json={}).json()["note"]
    assert note["confirmed_by"] == ENG
    assert note["id"] == "kn-1"  # same note, new standing
    assert len(store.note_history("kn-1", "o1")) == 1


def test_create_adds_knowledge_that_never_came_from_a_session() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post(
        f"{API}/notes",
        json={"kind": "gotcha", "title": "Staging shares prod redis", "body": "Flush carefully.",
              "project": "bench"},
    )
    assert resp.status_code == 200
    assert resp.json()["note"]["source"] == "human"
    assert client.get(f"{API}/notes?kind=gotcha").json()["items"][0]["title"] == (
        "Staging shares prod redis"
    )


def test_revert_restores_earlier_text_as_a_new_version() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    edited = client.post(
        f"{API}/notes/kn-1/edit", json={"title": "Wrong", "body": "Bad edit."}
    ).json()["note"]
    resp = client.post(f"{API}/notes/{edited['id']}/revert", json={"to_version_id": "kn-1"})
    assert resp.status_code == 200
    reverted = resp.json()["note"]
    assert reverted["title"] == "Pin torch 2.4"
    history = client.get(f"{API}/notes/{reverted['id']}/history").json()["versions"]
    assert len(history) == 3  # original, bad edit, restoration — nothing erased


def test_writes_validate_their_input() -> None:
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    assert client.post(
        f"{API}/notes", json={"kind": "nonsense", "title": "t", "body": "b"}
    ).status_code == 422
    assert client.post(
        f"{API}/notes", json={"kind": "gotcha", "title": "  ", "body": "b"}
    ).status_code == 422
    assert client.post(
        f"{API}/notes/kn-missing/edit", json={"title": "t", "body": "b"}
    ).status_code == 404


def test_form_encoded_writes_are_refused_as_csrf_shaped() -> None:
    # samesite=lax already blocks cross-site cookie forms; requiring a JSON
    # content type closes the remaining simple-request shape.
    client, store, config = _make()
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post(f"{API}/notes/kn-1/confirm", data={"org_id": "o1"})
    assert resp.status_code == 415
    note = store.get_note("kn-1", "o1")
    assert note is not None
    assert note.confirmed_by is None  # nothing was written


def test_writes_require_a_session() -> None:
    client, store, _ = _make()
    _seed(store)
    assert client.post(f"{API}/notes/kn-1/confirm", json={}).status_code == 401


# ── ask ──────────────────────────────────────────────────────────────────
def test_ask_returns_a_narrative_with_resolved_citations() -> None:
    client, store, config = _make(response="Pinned torch [kn-1] after a session [c1].")
    _seed(store)
    _login(client, _engineer(config))
    resp = client.post(f"{API}/ask", json={"query": "why torch 2.4?"})
    assert resp.status_code == 200
    payload = resp.json()
    assert "Pinned torch" in payload["narrative"]
    assert [n["id"] for n in payload["notes"]] == ["kn-1"]
    assert [s["id"] for s in payload["sessions"]] == ["c1"]
    # Citations come back as objects, not bare ids — the client renders cards
    # without an extra fetch per citation.
    for cited in payload["notes"]:
        assert "title" in cited and "body" in cited
    for cited in payload["sessions"]:
        assert "task_intent" in cited


def test_ask_is_audited_as_an_individual_query() -> None:
    client, store, config = _make(response="an answer [kn-1]")
    _seed(store)
    _login(client, _engineer(config))
    client.post(f"{API}/ask", json={"query": "what shipped this week?"})
    audit = store.list_founder_audit("o1")
    assert any(q.query == "what shipped this week?" for q in audit)
