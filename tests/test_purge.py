"""Purge machinery — junk identification, dry-run safety, and cascade deletion.

Covers the server admin endpoint and the local agent CLI. The load-bearing
property is that a purge is narrow and reversible-until-confirmed: dry run by
default, admin-gated, audited either way, and refusing an unfiltered request.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import ScriptedProvider
from manthana.server.purge import PurgeSelector, is_self_generated, purge
from manthana.server.storage import InMemoryObjectStore

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}

# The literal opening of the compaction prompt template (enrich/prompt.py).
_PROMPT_HEAD = (
    "You are Manthana's compactor. Summarize ONE engineering session into a structured\n"
    "digest. Read the turns (a JSON array of {seq, role, text, tool}) and return ONLY a\n"
    "single JSON object"
)
# Real task_intent strings observed on production for self-generated sessions.
_PROD_A = "Summarize one engineering session into a structured JSON digest (the Manthana co"
_PROD_B = "No engineering task was undertaken; the session contains only the Manthana compa"


def _comp(
    cid: str,
    *,
    intent: str = "fix the webhook retry",
    approach: str = "traced api/webhook.py",
    source: str = "full",
    native: str | None = None,
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="e@x.com",
        surface=Surface.claude_code,
        project="demo",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=intent,
        approach=approach,
        outcome=Outcome.success,
        released=True,
        source=source,  # type: ignore[arg-type]
        native_summary=native,
    )


def _make():
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, ScriptedProvider([])))
    store.create_org("o1", "Org")
    return client, store, obj


# ── the junk predicate ────────────────────────────────────────────────────
def test_identifies_the_prompt_template_verbatim() -> None:
    # A pending digest's task_intent is the first user turn — for a compaction
    # call, that IS the prompt.
    assert is_self_generated(_comp("c", intent=_PROMPT_HEAD[:200], approach=""))


def test_identifies_the_observed_production_phrasings() -> None:
    assert is_self_generated(_comp("c", intent=_PROD_A, approach=""))
    assert is_self_generated(_comp("c", intent=_PROD_B, approach=""))


def test_identifies_junk_carried_in_native_summary() -> None:
    assert is_self_generated(_comp("c", intent="x", approach="", native=_PROMPT_HEAD))


def test_curly_apostrophe_and_wrapping_do_not_defeat_the_marker() -> None:
    # A model paraphrase may come back with U+2019 and different line wrapping.
    assert is_self_generated(
        _comp("c", intent="You are Manthana’s   compactor.\nSummarize...", approach="")
    )


def test_real_work_about_manthana_is_not_flagged() -> None:
    # THE false-positive case that matters: an engineer working ON Manthana.
    # These legitimately mention the compactor and must survive.
    for intent in (
        "Fix the Manthana compactor prompt",
        "Debug why manthana's compaction is recursing into itself",
        "Add native_summary to the Manthana compaction schema",
        "Refactor manthana compactor tests",
        "Investigate Manthana compaction cost per session",
    ):
        assert not is_self_generated(_comp("c", intent=intent, approach="")), intent


def test_ordinary_work_is_not_flagged() -> None:
    assert not is_self_generated(_comp("c"))
    assert not is_self_generated(_comp("c", intent="summarize the quarterly metrics"))


# ── dry run ───────────────────────────────────────────────────────────────
def test_dry_run_deletes_nothing_and_reports_what_would_go() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("real"), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "self_generated": True}, headers=ADMIN
    )

    body = resp.json()
    assert body["dry_run"] is True
    assert body["matched"] == 1 and body["deleted"] == 0
    assert body["sample"][0]["id"] == "junk"
    # Nothing actually removed.
    assert store.get_compaction("junk", "o1") is not None
    assert store.count_compactions("o1") == 2


def test_confirm_is_required_to_delete() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")

    client.post("/v1/admin/purge", json={"org_id": "o1", "self_generated": True}, headers=ADMIN)
    assert store.get_compaction("junk", "o1") is not None

    resp = client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "self_generated": True, "confirm": True},
        headers=ADMIN,
    )
    assert resp.json()["deleted"] == 1
    assert store.get_compaction("junk", "o1") is None


def test_unfiltered_purge_is_refused() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("real"), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "confirm": True}, headers=ADMIN
    )

    assert resp.status_code == 422
    assert store.count_compactions("o1") == 1  # untouched


# ── cascade: rows + blobs + vectors together ──────────────────────────────
def test_purge_removes_rows_blobs_and_vectors_together() -> None:
    client, store, obj = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")
    key = "o1/t1/junk.jsonl"
    obj.put(key, b'{"seq":0}\n')
    store.record_raw("junk", "o1", key)
    store.upsert_vector("o1", "junk", dim=3, text_hash="h", vec=[0.1, 0.2, 0.3])
    assert obj.get(key) is not None and store.get_vectors("o1", ["junk"], dim=3)

    resp = client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "self_generated": True, "confirm": True},
        headers=ADMIN,
    )

    body = resp.json()
    assert body["deleted"] == 1 and body["blobs_deleted"] == 1 and body["vectors_deleted"] == 1
    assert store.get_compaction("junk", "o1") is None  # row gone
    assert obj.get(key) is None  # blob gone
    assert store.get_vectors("o1", ["junk"], dim=3) == {}  # vector gone
    assert store.get_raw_key("junk", "o1") is None  # raw record gone


def test_blob_failure_aborts_the_whole_purge() -> None:
    # If a blob can't be removed we must NOT drop the rows that point at it —
    # that would orphan the blob permanently. Rows survive so a retry can finish.
    _, store, obj = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")
    key = "o1/t1/junk.jsonl"
    obj.put(key, b'{"seq":0}\n')
    store.record_raw("junk", "o1", key)

    class _RefusingStore(InMemoryObjectStore):
        def delete(self, key: str) -> bool:
            return False

    refusing = _RefusingStore()
    refusing.put(key, b'{"seq":0}\n')

    report = purge(
        store, refusing, org_id="o1",
        selector=PurgeSelector(self_generated=True), confirm=True,
    )

    assert report.deleted == 0 and report.error is not None
    assert store.get_compaction("junk", "o1") is not None  # row survived


# ── selectors ─────────────────────────────────────────────────────────────
def test_source_selector_narrows() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("p1", source="pending"), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("f1", source="full"), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "source": "pending", "confirm": True},
        headers=ADMIN,
    )

    assert resp.json()["deleted"] == 1
    assert store.get_compaction("p1", "o1") is None
    assert store.get_compaction("f1", "o1") is not None


def test_selectors_combine_as_and_never_widening() -> None:
    client, store, _ = _make()
    store.ingest_compaction(
        _comp("junk_full", intent=_PROD_A, source="full"), org_id="o1", team_id="t1"
    )
    store.ingest_compaction(
        _comp("junk_pending", intent=_PROD_A, source="pending"), org_id="o1", team_id="t1"
    )

    resp = client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "self_generated": True, "source": "pending", "confirm": True},
        headers=ADMIN,
    )

    assert resp.json()["deleted"] == 1
    assert store.get_compaction("junk_full", "o1") is not None  # narrowed out


def test_bad_source_value_is_rejected() -> None:
    client, *_ = _make()
    resp = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "source": "bogus"}, headers=ADMIN
    )
    assert resp.status_code == 422


def test_purge_is_org_scoped() -> None:
    client, store, _ = _make()
    store.create_org("o2", "Other")
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o2", team_id="t1")

    client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "self_generated": True, "confirm": True},
        headers=ADMIN,
    )

    assert store.get_compaction("junk", "o1") is None
    assert store.get_compaction("junk", "o2") is not None  # other tenant untouched


# ── auth + audit ──────────────────────────────────────────────────────────
def test_purge_is_admin_gated() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "self_generated": True, "confirm": True}
    )

    assert resp.status_code == 401
    assert store.get_compaction("junk", "o1") is not None
    assert client.get("/v1/admin/purge-audit?org_id=o1").status_code == 401


def test_purges_are_audited_including_dry_runs() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_comp("junk", intent=_PROD_A), org_id="o1", team_id="t1")

    client.post("/v1/admin/purge", json={"org_id": "o1", "self_generated": True}, headers=ADMIN)
    client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "self_generated": True, "confirm": True},
        headers=ADMIN,
    )

    entries = client.get("/v1/admin/purge-audit?org_id=o1", headers=ADMIN).json()["entries"]
    assert len(entries) == 2
    modes = {(e["dry_run"], e["deleted"]) for e in entries}
    assert modes == {(True, 0), (False, 1)}
    assert all(e["selector"]["self_generated"] for e in entries)


# ── agent-side local purge ────────────────────────────────────────────────
def test_agent_local_purge_dry_run_then_confirm() -> None:
    from manthana.agent.cli import app as cli_app
    from manthana.agent.store import Store
    from typer.testing import CliRunner

    store = Store.open_memory()
    store.upsert_compaction(_comp("junk", intent=_PROD_A))
    store.upsert_compaction(_comp("real"))
    store.upsert_vector("junk", dim=3, text_hash="h", vec=[0.1, 0.2, 0.3])

    # The predicate the CLI uses picks exactly the junk.
    from manthana.agent.purge import matches

    doomed = [c for c in store.list_compactions() if matches(c, self_generated=True)]
    assert [c.id for c in doomed] == ["junk"]

    # Dry run deletes nothing.
    assert len(store.list_compactions()) == 2

    removed, vectors = store.delete_compactions([c.id for c in doomed])
    assert removed == 1 and vectors == 1
    assert {c.id for c in store.list_compactions()} == {"real"}
    assert store.get_vectors(["junk"], dim=3) == {}

    # The CLI itself refuses an unfiltered purge.
    result = CliRunner().invoke(cli_app, ["purge"])
    assert result.exit_code == 1
    assert "refusing an unfiltered purge" in result.stdout


def test_agent_and_server_markers_stay_in_sync() -> None:
    # They are duplicated for the Apache-2.0 / AGPL package boundary; if one side
    # is edited without the other, local and server purges would disagree.
    from manthana.agent.purge import SELF_GENERATED_MARKERS as AGENT_MARKERS
    from manthana.server.purge import SELF_GENERATED_MARKERS as SERVER_MARKERS

    assert AGENT_MARKERS == SERVER_MARKERS
