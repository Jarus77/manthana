"""Agent → server sync: end-to-end (capture → release → sync → ingest).

Wires the local agent Store to the server's ASGI app via the FastAPI TestClient
(no network). Exercises the full trust path: only released, non-personal,
redacted compactions cross the boundary, and re-sync is idempotent.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from manthana.agent.store import Store
from manthana.agent.sync_client import SyncClient, SyncError
from manthana.schemas import EngineeringCompaction, Mode, Outcome, Role, Session, Surface, Turn
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import issue_team_token
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECRET = "AKIAIOSFODNN7EXAMPLE"


def _server() -> tuple[TestClient, ServerStore, InMemoryObjectStore, str]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "O")
    store.create_team("t1", "o1", "T")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, ScriptedProvider([])))
    token = issue_team_token("x" * 40, org_id="o1", team_id="t1", actor="eng@x.com")
    return client, store, obj, token


def _session(local: Store, sid: str, mode: Mode) -> None:
    local.upsert_session(
        Session(
            id=sid,
            actor="eng@x.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            mode=mode,
        )
    )


def _comp(
    local: Store,
    cid: str,
    sid: str,
    *,
    released: bool,
    intent: str = "did work",
    files: list[str] | None = None,
) -> None:
    local.upsert_compaction(
        EngineeringCompaction(
            id=cid,
            session_id=sid,
            actor="eng@x.com",
            surface=Surface.claude_code,
            project="demo",
            started_at=_T0,
            ended_at=_T0,
            duration_seconds=1.0,
            task_intent=intent,
            approach="a",
            outcome=Outcome.success,
            released=released,
            files_touched=files or [],
        )
    )


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Minimal HTTP double for SyncClient (configurable ingest count / raw status)."""

    def __init__(self, *, ingested: int | None = None, raw_status: int = 200) -> None:
        self.ingested = ingested
        self.raw_status = raw_status
        self.posts: list[str] = []

    def post(self, url: str, *, json: Any = None, headers: Any = None) -> _Resp:
        self.posts.append(url)
        if url == "/v1/compactions":
            count = self.ingested if self.ingested is not None else len(json["compactions"])
            return _Resp(200, {"ingested": count})
        if url.endswith("/raw"):
            return _Resp(self.raw_status, {"ok": self.raw_status == 200})
        return _Resp(404, {})


def test_sync_only_released_nonpersonal_and_redacts() -> None:
    server, sstore, _obj, token = _server()
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(
        local, "cw", "w", released=True,
        intent=f"deploy with key {_SECRET}", files=[f"/app/.env holds {_SECRET}"],
    )
    _session(local, "p", Mode.personal)
    _comp(local, "cp", "p", released=True)  # personal -> never syncs
    _session(local, "w2", Mode.work)
    _comp(local, "cu", "w2", released=False)  # unreleased -> never syncs

    client = SyncClient("", token, client=server)
    result = client.sync(local)
    assert result.pushed == 1

    on_server = sstore.query_compactions(org_id="o1")
    assert [c.id for c in on_server] == ["cw"]  # only the released, work compaction
    # redaction-on-release: the secret never reaches the server — in task_intent
    # OR in EngineeringCompaction subclass fields (files_touched).
    assert _SECRET not in on_server[0].task_intent
    assert "[REDACTED:aws_key]" in on_server[0].task_intent
    eng = on_server[0]
    assert isinstance(eng, EngineeringCompaction)
    assert _SECRET not in " ".join(eng.files_touched)
    assert "[REDACTED:aws_key]" in eng.files_touched[0]


def test_sync_is_idempotent() -> None:
    server, sstore, _obj, token = _server()
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(local, "cw", "w", released=True)
    client = SyncClient("", token, client=server)
    assert client.sync(local).pushed == 1
    second = client.sync(local)
    assert second.pushed == 0  # already synced
    assert second.skipped == 1
    assert len(sstore.query_compactions(org_id="o1")) == 1  # no duplicate


def test_sync_raw_release_uploads_redacted_transcript() -> None:
    server, _sstore, obj, token = _server()
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(local, "cw", "w", released=True)
    local.add_turns(
        [Turn(id="t0", session_id="w", actor="eng@x.com", seq=0, role=Role.user, content="hi")]
    )
    client = SyncClient("", token, client=server)
    result = client.sync(local, include_raw=True)
    assert result.pushed == 1
    assert result.raw_uploaded == 1
    assert obj.get("o1/t1/cw.jsonl") is not None


def test_sync_raises_and_does_not_mark_synced_on_ingest_mismatch() -> None:
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(local, "cw", "w", released=True)
    client = SyncClient("", "tok", client=_FakeClient(ingested=0))  # server accepted 0 of 1
    with pytest.raises(SyncError):
        client.sync(local)
    assert local.synced_ids() == set()  # nothing marked synced -> will retry


def test_raw_failure_marks_metadata_then_retries_raw() -> None:
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(local, "cw", "w", released=True)
    local.add_turns(
        [Turn(id="t0", session_id="w", actor="eng@x.com", seq=0, role=Role.user, content="hi")]
    )
    fake = _FakeClient(raw_status=500)
    client = SyncClient("", "tok", client=fake)

    first = client.sync(local, include_raw=True)
    assert first.pushed == 1
    assert first.raw_uploaded == 0  # raw failed
    assert "cw" in local.synced_ids()  # metadata synced anyway (not re-pushed)
    assert "cw" not in local.raw_synced_ids()

    fake.raw_status = 200  # raw now succeeds
    second = client.sync(local, include_raw=True)
    assert second.pushed == 0  # metadata already synced
    assert second.raw_uploaded == 1  # raw retried successfully
    assert "cw" in local.raw_synced_ids()


def test_watch_sync_adapter_uploads_raw_by_default_path() -> None:
    """The watcher's sync adapter (`manthana watch` → _sync_pushed) carries the
    redacted raw transcript, not just the digest — release triggers raw upload
    (decisions-doc contract; the founder MCP drill/grep layer needs the raw)."""
    from manthana.agent.cli import _sync_pushed

    server, _sstore, obj, token = _server()
    local = Store.open_memory()
    _session(local, "w", Mode.work)
    _comp(local, "cw", "w", released=True)
    local.add_turns(
        [Turn(id="t0", session_id="w", actor="eng@x.com", seq=0, role=Role.user, content="hi")]
    )
    client = SyncClient("", token, client=server)
    sync_fn = _sync_pushed(client, include_raw=True)  # how `watch` wires it (default)
    assert sync_fn(local) == 1
    assert obj.get("o1/t1/cw.jsonl") is not None  # raw landed without a manual --raw sync
    # and the adapter without raw (opt-out path) does not upload raw
    local2 = Store.open_memory()
    _session(local2, "w2", Mode.work)
    _comp(local2, "cw2", "w2", released=True)
    sync_fn_noraw = _sync_pushed(client, include_raw=False)
    assert sync_fn_noraw(local2) == 1
    assert obj.get("o1/t1/cw2.jsonl") is None


# ── `manthana resync` (re-onboarding after a server-side wipe) ─────────────
def _resync_store(tmp_path: Any) -> Store:
    """A local store on disk at MANTHANA_DATA_HOME, seeded with one released work
    compaction, one personal-mode compaction, and one unreleased one."""
    local = Store.open()
    _session(local, "w", Mode.work)
    _session(local, "p", Mode.personal)
    _comp(local, "cw", "w", released=True)
    _comp(local, "cp", "p", released=True)  # released but PERSONAL — never syncable
    _comp(local, "cu", "w", released=False)  # work but not released
    return local


def test_resync_dry_run_reports_counts_and_changes_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from manthana.agent.cli import app as cli_app
    from typer.testing import CliRunner

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    local = _resync_store(tmp_path)
    local.mark_synced("cw", _T0)
    assert local.synced_ids() == {"cw"}

    result = CliRunner().invoke(cli_app, ["resync"])
    assert result.exit_code == 0
    assert "1 released, non-personal compaction(s) would be re-pushed" in result.stdout
    assert "1 local sync watermark(s) would be cleared" in result.stdout
    assert "2 compaction(s) stay local" in result.stdout  # personal + unreleased
    assert "DRY RUN" in result.stdout
    # nothing actually cleared
    assert Store.open().synced_ids() == {"cw"}


def test_resync_confirm_clears_sync_state_so_the_next_sync_re_pushes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from manthana.agent.cli import app as cli_app
    from typer.testing import CliRunner

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    local = _resync_store(tmp_path)
    local.mark_synced("cw", _T0)
    local.mark_raw_synced("cw", _T0)

    result = CliRunner().invoke(cli_app, ["resync", "--confirm"])
    assert result.exit_code == 0
    assert "cleared 1 watermark(s)" in result.stdout
    assert "re-upload 1 compaction(s) to the org server" in result.stdout

    reopened = Store.open()
    assert reopened.synced_ids() == set()  # digest watermark gone
    assert reopened.raw_synced_ids() == set()  # raw watermark gone too


def test_resync_does_not_make_personal_sessions_syncable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trust invariant: clearing sync state changes WHAT HAS BEEN SENT, never
    WHAT MAY BE SENT. A personal-mode session stays unsyncable after a resync, and
    an actual sync must still refuse to push it."""
    from manthana.agent.cli import app as cli_app
    from manthana.agent.sync import eligible_for_sync
    from typer.testing import CliRunner

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    local = _resync_store(tmp_path)
    local.mark_synced("cw", _T0)

    assert CliRunner().invoke(cli_app, ["resync", "--confirm"]).exit_code == 0

    reopened = Store.open()
    sessions = {s.id: s for s in reopened.list_sessions(limit=1000)}
    eligible = eligible_for_sync(reopened.list_compactions(), sessions)
    assert {c.id for c in eligible} == {"cw"}  # personal "cp" and unreleased "cu" excluded

    # And end-to-end through the real sync path: only the work compaction lands.
    server, sstore, _obj, token = _server()
    client = SyncClient("", token, client=server)
    result = client.sync(reopened)
    assert result.pushed == 1
    assert {c.id for c in sstore.query_compactions(org_id="o1")} == {"cw"}


def test_resync_with_no_watermarks_says_so(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from manthana.agent.cli import app as cli_app
    from typer.testing import CliRunner

    monkeypatch.setenv("MANTHANA_DATA_HOME", str(tmp_path))
    _resync_store(tmp_path)  # nothing marked synced
    result = CliRunner().invoke(cli_app, ["resync"])
    assert result.exit_code == 0
    assert "nothing to clear" in result.stdout


def test_resync_help_says_it_re_uploads_and_deletes_nothing_locally() -> None:
    from manthana.agent.cli import app as cli_app
    from typer.testing import CliRunner

    out = CliRunner().invoke(cli_app, ["resync", "--help"]).stdout
    assert "RE-UPLOADS" in out
    assert "deletes nothing" in out
    assert "--confirm" in out
