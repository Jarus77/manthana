"""Org onboarding — invite blob, persisted secrets, invite store + endpoints (phase P2).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from manthana.schemas import decode_invite, encode_invite
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import verify_team_token
from manthana.server.config import persisted_secrets
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 3, 1, tzinfo=UTC)


# ── invite blob (shared, Apache) ─────────────────────────────────────────────
def test_invite_blob_roundtrips_and_rejects_garbage() -> None:
    blob = encode_invite("https://manthana.acme.com/", "code123")
    assert blob.startswith("mia_")
    assert decode_invite(blob) == ("https://manthana.acme.com", "code123")  # trailing / stripped
    assert decode_invite("  " + blob + "  ") == ("https://manthana.acme.com", "code123")
    with pytest.raises(ValueError):
        decode_invite("not-a-real-invite")


# ── persisted secrets (quickstart) ───────────────────────────────────────────
def test_persisted_secrets_generate_then_reuse(tmp_path: Path) -> None:
    jwt1, admin1 = persisted_secrets(tmp_path)
    assert jwt1 and admin1 and jwt1 != admin1
    jwt2, admin2 = persisted_secrets(tmp_path)  # second call reuses the file
    assert (jwt2, admin2) == (jwt1, admin1)  # stable → existing agent tokens keep working
    assert (tmp_path / "server-secrets.toml").exists()


# ── invite store methods ─────────────────────────────────────────────────────
def _store() -> ServerStore:
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    store.create_team("t1", "o1", "Platform")
    return store


def test_bound_single_use_invite_redeems_once() -> None:
    store = _store()
    store.create_invite("c1", org_id="o1", team_id="t1", actor="bob@x.com", uses=1,
                        expires_at=_T0 + timedelta(days=14))
    row = store.redeem_invite("c1", now=_T0)
    assert row is not None and row.actor == "bob@x.com"
    assert store.redeem_invite("c1", now=_T0) is None  # single-use → gone
    assert store.get_invite("c1").redeemed_at is not None  # type: ignore[union-attr]


def test_open_multi_use_invite_decrements() -> None:
    store = _store()
    store.create_invite("open1", org_id="o1", team_id="t1", uses=3,
                        expires_at=_T0 + timedelta(days=14))
    assert store.redeem_invite("open1", now=_T0) is not None
    assert store.redeem_invite("open1", now=_T0) is not None
    assert store.get_invite("open1").uses_left == 1  # type: ignore[union-attr]


def test_expired_and_unknown_invites_rejected() -> None:
    store = _store()
    store.create_invite("old", org_id="o1", team_id="t1", uses=1, expires_at=_T0)
    assert store.redeem_invite("old", now=_T0 + timedelta(days=1)) is None  # expired
    assert store.redeem_invite("nope", now=_T0) is None  # unknown


# ── endpoints: admin mint + public redeem ────────────────────────────────────
def _client() -> tuple[TestClient, ServerStore]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = _store()
    client = TestClient(create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])))
    return client, store


def test_admin_invites_endpoint_is_gated_and_enroll_is_open() -> None:
    client, _ = _client()
    body = {"org_id": "o1", "team_id": "t1", "actor": "bob@x.com"}
    assert client.post("/v1/admin/invites", json=body).status_code == 401  # no admin token
    resp = client.post("/v1/admin/invites", json=body, headers={"X-Admin-Token": "adm"})
    assert resp.status_code == 200
    code = resp.json()["code"]

    # redeem (unauthenticated) → a valid team token for the bound actor
    r = client.post("/v1/enroll", json={"code": code})
    assert r.status_code == 200
    claims = verify_team_token("x" * 40, r.json()["token"])
    assert claims.actor == "bob@x.com" and claims.org_id == "o1" and claims.team_id == "t1"
    # single-use → second redeem fails
    assert client.post("/v1/enroll", json={"code": code}).status_code == 400


def test_open_invite_requires_actor_without_consuming() -> None:
    client, store = _client()
    resp = client.post(
        "/v1/admin/invites", json={"org_id": "o1", "team_id": "t1", "uses": 5},
        headers={"X-Admin-Token": "adm"},
    )
    code = resp.json()["code"]
    # open invite with no actor → 400, and NO use consumed
    assert client.post("/v1/enroll", json={"code": code}).status_code == 400
    assert store.get_invite(code).uses_left == 5  # type: ignore[union-attr]
    # with an actor → succeeds
    r = client.post("/v1/enroll", json={"code": code, "actor": "carol@x.com"})
    assert r.status_code == 200
    assert verify_team_token("x" * 40, r.json()["token"]).actor == "carol@x.com"


def test_enroll_unknown_code_400() -> None:
    client, _ = _client()
    assert client.post("/v1/enroll", json={"code": "bogus"}).status_code == 400


# ── engineer side: _verify_connection + setup orchestration (phase P3) ───────
def test_verify_connection_reports_reachable_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import manthana.agent.cli as cli
    from manthana.agent import sync_client

    class _R:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
    monkeypatch.setattr(sync_client.SyncClient, "close", lambda self: None)
    monkeypatch.setattr(sync_client.SyncClient, "push_compactions", lambda self, comps: 0)
    assert cli._verify_connection("http://x", "tok") == (True, True)  # token accepted

    def _reject(self: object, comps: object) -> int:
        raise sync_client.SyncError("rejected")

    monkeypatch.setattr(sync_client.SyncClient, "push_compactions", _reject)
    assert cli._verify_connection("http://x", "tok") == (True, False)  # token rejected

    def _boom(*a: object, **k: object) -> object:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _boom)
    assert cli._verify_connection("http://x", "tok") == (False, False)  # unreachable


def test_setup_redeems_invite_and_writes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    import platform

    import httpx
    import manthana.agent.cli as cli
    from manthana.agent.config import load_config

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self) -> dict[str, str]:
            return {"token": "TESTTOKEN", "actor": "bob@acme.com"}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(cli, "_verify_connection", lambda base, token: (True, True))
    monkeypatch.setattr(platform, "system", lambda: "Linux")  # skip macOS launchd install
    monkeypatch.setattr(cli, "ingest_all", lambda store: None)  # skip real ~/.claude capture

    cli.setup(invite=encode_invite("http://localhost:9999", "code1"), actor="bob@acme.com")

    cfg = load_config()
    assert cfg.server_url == "http://localhost:9999"
    assert cfg.team_token == "TESTTOKEN" and cfg.actor == "bob@acme.com"


# ── doctor + status helpers (phase P4) ───────────────────────────────────────
def test_count_pending_and_last_sync() -> None:
    from manthana.agent.store import Store
    from manthana.schemas import (
        EngineeringCompaction,
        Mode,
        Outcome,
        Session,
        Surface,
    )

    store = Store.open_memory()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    def sess(sid: str, mode: Mode = Mode.work) -> Session:
        return Session(
            id=sid, actor="e@x", surface=Surface.claude_code, project="p",
            started_at=t0, turn_count=1, mode=mode,
        )

    store.upsert_session(sess("s1"))
    store.upsert_session(sess("s2"))
    store.upsert_session(sess("p1", Mode.personal))
    store.upsert_compaction(EngineeringCompaction(
        id="comp-s1", session_id="s1", actor="e@x", surface=Surface.claude_code, project="p",
        started_at=t0, ended_at=t0, duration_seconds=1.0, task_intent="t", approach="a",
        outcome=Outcome.success))
    assert store.count_pending() == 1  # s2 pending (s1 compacted, p1 personal never counts)
    assert store.last_sync_at() is None
    store.mark_synced("comp-s1", datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    assert store.last_sync_at() == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def test_doctor_exits_nonzero_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import manthana.agent.cli as cli
    import typer

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("MANTHANA_SERVER_URL", raising=False)
    monkeypatch.delenv("MANTHANA_TEAM_TOKEN", raising=False)
    with pytest.raises(typer.Exit):
        cli.doctor()  # no config → critical check fails → non-zero exit


def test_doctor_passes_when_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import manthana.agent.cli as cli
    from manthana.agent.config import Config, save_config

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    save_config(Config(server_url="http://x:8000", team_token="tok", actor="bob@x"))
    monkeypatch.setattr(cli, "_verify_connection", lambda base, token: (True, True))

    class _R:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
    cli.doctor()  # all critical checks pass → does not raise
