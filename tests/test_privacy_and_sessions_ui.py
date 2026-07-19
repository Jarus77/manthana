"""Per-org privacy mode (named founder view) + the Sessions browse/detail console.

Answers two pilot-founder complaints: "we can't see which engineer did what" and
"let us actually read the session digests in the UI".

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.founder import run_query
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}


def _comp(cid: str, actor: str, project: str = "checkout", intent: str = "fix webhook"):
    return EngineeringCompaction(
        id=cid, session_id=cid, actor=actor, surface=Surface.claude_code, project=project,
        started_at=_T0, ended_at=_T0, duration_seconds=1.0, task_intent=intent,
        approach="iterated", outcome=Outcome.success, est_cost_usd=0.4, tier_used="sonnet",
        files_touched=["/app/webhook.py"], released=True,
    )


def _make(privacy: str = "k_anon", provider: ScriptedProvider | None = None):
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", privacy_mode=privacy)
    store = ServerStore.open("sqlite://")
    store.create_org("acme", "Acme")
    for i in range(4):
        store.ingest_compaction(
            _comp(f"c{i}", f"eng{i}@acme.dev", intent=f"task number {i}"),
            org_id="acme", team_id="t1",
        )
    app = create_app(config, store, InMemoryObjectStore(), provider or ScriptedProvider([]))
    return TestClient(app), store, config


def _login(client: TestClient) -> None:
    client.post("/ui/login", data={"token": "adm"}, follow_redirects=False)


# ── privacy mode: names reach the model + the rollup ────────────────────────
def test_named_path_sends_engineer_to_model_and_rollups_by_engineer() -> None:
    _client, store, config = _make()
    provider = ScriptedProvider(["{}", "eng0 fixed the webhook [c0]."])
    res = run_query(
        store, config, org_id="acme", query="who did what?", provider=provider,
        allow_individual=True,
    )
    assert res.rollup is not None
    assert res.rollup.by_engineer  # per-engineer counts populated
    assert "eng0@acme.dev" in res.rollup.by_engineer
    # the engineer identity actually reached the narrative prompt
    assert "eng0@acme.dev" in provider.calls[-1]


def test_deidentified_path_never_sends_names() -> None:
    _client, store, config = _make()
    provider = ScriptedProvider(["{}", "The team shipped things [c0]."])
    res = run_query(
        store, config, org_id="acme", query="what shipped?", provider=provider,
        allow_individual=False,
    )
    assert res.rollup is not None
    assert res.rollup.by_engineer == {}  # no names in the aggregate
    assert "eng0@acme.dev" not in provider.calls[-1]  # nor in the prompt


def test_privacy_mode_endpoint_roundtrip_and_validation() -> None:
    client, store, _config = _make()
    ok = client.put("/v1/admin/orgs/acme/privacy", json={"mode": "open"}, headers=ADMIN)
    assert ok.is_success
    assert store.get_org_privacy("acme") == "open"
    bad = client.put("/v1/admin/orgs/acme/privacy", json={"mode": "nope"}, headers=ADMIN)
    assert bad.status_code == 422
    assert client.put("/v1/admin/orgs/acme/privacy", json={"mode": "open"}).status_code == 401


def test_org_override_beats_server_default_for_topics() -> None:
    client, store, _config = _make(privacy="k_anon")
    store.set_org_privacy("acme", "open")
    data = client.get("/v1/founder/topics", params={"org_id": "acme"}, headers=ADMIN).json()
    # named mode exposes contributors on each topic
    assert all("contributors" in t for t in data["topics"]) or not data["topics"]


# ── sessions browse UI ──────────────────────────────────────────────────────
def test_sessions_page_lists_digests_with_names_when_open() -> None:
    client, store, _config = _make(privacy="open")
    _login(client)
    page = client.get("/ui/sessions", params={"org_id": "acme"}).text
    assert "task number 0" in page  # intent is the readable label
    assert "eng0" in page  # engineer column present
    assert "engineer" in page


def test_sessions_page_hides_names_when_k_anon() -> None:
    client, _store, _config = _make(privacy="k_anon")
    _login(client)
    page = client.get("/ui/sessions", params={"org_id": "acme"}).text
    assert "task number 0" in page
    assert "eng0@acme.dev" not in page
    assert "de-identified" in page


def test_session_detail_shows_digest_not_raw() -> None:
    client, _store, _config = _make(privacy="open")
    _login(client)
    page = client.get("/ui/session", params={"org_id": "acme", "compaction_id": "c1"}).text
    assert "task number 1" in page
    assert "iterated" in page  # approach
    assert "/app/webhook.py" in page  # files touched
    missing = client.get("/ui/session", params={"org_id": "acme", "compaction_id": "nope"})
    assert missing.status_code == 404


def test_sessions_filter_by_project_and_requires_login() -> None:
    client, _store, _config = _make(privacy="open")
    assert client.get(
        "/ui/sessions", params={"org_id": "acme"}, follow_redirects=False
    ).status_code == 303  # unauthenticated → login
    _login(client)
    page = client.get("/ui/sessions", params={"org_id": "acme", "project": "nonexistent"}).text
    assert "no sessions yet" in page
