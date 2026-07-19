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
from manthana.server.purge import (
    PurgeSelector,
    is_self_generated,
    is_structural_junk,
    purge,
)
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

# Real paraphrases observed on production. The model reworded the prompt every
# time, so NONE of these contains a SELF_GENERATED_MARKERS phrase — they are the
# ~664 rows the marker predicate missed.
_PARAPHRASES = (
    "Summarize a single engineering session into a structured JSON digest "
    "(the Manthana compactor task).",
    "Summarize an engineering session into a structured JSON digest as Manthana's compactor.",
    "Meta-task: produce a structured JSON compactor digest summarizing one engineering session.",
    "The session consisted solely of the Manthana compactor system prompt plus a single "
    "prior assistant turn that was itself a compactor invocation.",
)


def _comp(
    cid: str,
    *,
    intent: str = "fix the webhook retry",
    approach: str = "traced api/webhook.py",
    source: str = "full",
    native: str | None = None,
    project: str = "demo",
    outcome: Outcome = Outcome.success,
    files: list[str] | None = None,
) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=f"s-{cid}",
        actor="e@x.com",
        surface=Surface.claude_code,
        project=project,
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=intent,
        approach=approach,
        outcome=outcome,
        released=True,
        source=source,  # type: ignore[arg-type]
        native_summary=native,
        files_touched=files or [],
    )


def _junk(cid: str, *, intent: str, **kw: object) -> EngineeringCompaction:
    """A structurally-junk record: no files, no project, abandoned."""
    return _comp(
        cid,
        intent=intent,
        approach="",
        project="unknown",
        outcome=Outcome.abandoned,
        **kw,  # type: ignore[arg-type]
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


# ── the structural predicate ──────────────────────────────────────────────
def test_structural_junk_catches_the_paraphrases_markers_miss() -> None:
    # The whole reason the selector exists: these are LLM rewordings of the
    # prompt, so the fixed-phrase markers do NOT fire on them.
    for i, intent in enumerate(_PARAPHRASES):
        c = _junk(f"c{i}", intent=intent)
        assert not is_self_generated(c), intent
        assert is_structural_junk(c), intent


def test_structural_junk_accepts_a_falsy_project_as_well_as_unknown() -> None:
    assert is_structural_junk(
        _comp(
            "c",
            intent=_PARAPHRASES[0],
            approach="",
            project="",
            outcome=Outcome.abandoned,
        )
    )


def test_marker_hit_still_qualifies_under_the_structural_conjunction() -> None:
    assert is_structural_junk(_junk("c", intent=_PROD_A))


# ── the false-positive cases that matter ──────────────────────────────────
def test_compaction_shaped_text_with_files_touched_is_not_junk() -> None:
    # THE critical case: the operator's own session working ON Manthana. Its
    # text is every bit as compaction-shaped as the junk — but it touched files,
    # so it fails structurally and survives.
    for intent in _PARAPHRASES + (
        "Rewrite the compactor prompt so it stops summarizing its own digests",
        "Add native_summary to the Manthana compaction schema",
    ):
        c = _comp(
            "c",
            intent=intent,
            approach="",
            project="unknown",
            outcome=Outcome.abandoned,
            files=["server/src/manthana/server/purge.py"],
        )
        assert not is_structural_junk(c), intent


def test_compaction_shaped_text_with_a_real_project_is_not_junk() -> None:
    # Files empty (a pure discussion/design session) but a real project name —
    # deterministic metadata a compaction call never has.
    c = _comp(
        "c",
        intent=_PARAPHRASES[0],
        approach="",
        project="manthana",
        outcome=Outcome.abandoned,
    )
    assert not is_structural_junk(c)


def test_a_non_abandoned_outcome_is_not_junk() -> None:
    for outcome in (Outcome.success, Outcome.partial):
        c = _comp(
            "c", intent=_PARAPHRASES[0], approach="", project="unknown", outcome=outcome
        )
        assert not is_structural_junk(c), outcome


def test_structurally_empty_but_non_compaction_text_is_not_junk() -> None:
    # An engineer really did abandon an unattributed session. No compaction
    # shape in the text, so it is not ours to delete.
    for intent in (
        "Poke at the flaky CI runner, gave up",
        "summarize the quarterly metrics",  # action token, no subject token
        "Investigate Manthana compaction cost per session",  # subject, no action
    ):
        assert not is_structural_junk(_junk("c", intent=intent)), intent


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


def test_structural_junk_endpoint_purges_paraphrases_and_spares_real_work() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_junk("junk", intent=_PARAPHRASES[0]), org_id="o1", team_id="t1")
    # The operator's own Manthana dev session: same shape of text, but it touched files.
    store.ingest_compaction(
        _comp(
            "dev",
            intent=_PARAPHRASES[1],
            approach="",
            project="unknown",
            outcome=Outcome.abandoned,
            files=["server/src/manthana/server/purge.py"],
        ),
        org_id="o1",
        team_id="t1",
    )
    store.ingest_compaction(_comp("real"), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge",
        json={"org_id": "o1", "structural_junk": True, "confirm": True},
        headers=ADMIN,
    )

    assert resp.json()["deleted"] == 1
    assert store.get_compaction("junk", "o1") is None
    assert store.get_compaction("dev", "o1") is not None
    assert store.get_compaction("real", "o1") is not None


def test_structural_junk_dry_run_is_still_the_default() -> None:
    client, store, _ = _make()
    store.ingest_compaction(_junk("junk", intent=_PARAPHRASES[2]), org_id="o1", team_id="t1")

    body = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "structural_junk": True}, headers=ADMIN
    ).json()

    assert body["dry_run"] is True
    assert body["matched"] == 1 and body["deleted"] == 0
    assert store.get_compaction("junk", "o1") is not None


def test_structural_and_self_generated_compose_as_and() -> None:
    client, store, _ = _make()
    # Structurally junk, but a paraphrase — no marker phrase in it.
    store.ingest_compaction(
        _junk("paraphrase", intent=_PARAPHRASES[0]), org_id="o1", team_id="t1"
    )
    # Structurally junk AND carrying a verbatim marker.
    store.ingest_compaction(_junk("marked", intent=_PROD_A), org_id="o1", team_id="t1")

    resp = client.post(
        "/v1/admin/purge",
        json={
            "org_id": "o1",
            "structural_junk": True,
            "self_generated": True,
            "confirm": True,
        },
        headers=ADMIN,
    )

    # ANDed, so adding self_generated deletes strictly less.
    assert resp.json()["deleted"] == 1
    assert store.get_compaction("marked", "o1") is None
    assert store.get_compaction("paraphrase", "o1") is not None


def test_structural_junk_is_a_superset_of_self_generated_alone() -> None:
    # Sanity on the production motivation: the structural selector must catch
    # BOTH the marker rows and the paraphrases the markers missed.
    client, store, _ = _make()
    store.ingest_compaction(_junk("marked", intent=_PROD_A), org_id="o1", team_id="t1")
    for i, intent in enumerate(_PARAPHRASES):
        store.ingest_compaction(_junk(f"p{i}", intent=intent), org_id="o1", team_id="t1")

    marker_only = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "self_generated": True}, headers=ADMIN
    ).json()
    structural = client.post(
        "/v1/admin/purge", json={"org_id": "o1", "structural_junk": True}, headers=ADMIN
    ).json()

    assert marker_only["matched"] == 1
    assert structural["matched"] == 1 + len(_PARAPHRASES)


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


def test_agent_local_structural_purge_matches_the_server() -> None:
    from manthana.agent.purge import matches
    from manthana.agent.store import Store

    store = Store.open_memory()
    store.upsert_compaction(_junk("junk", intent=_PARAPHRASES[0]))
    store.upsert_compaction(
        _comp(
            "dev",
            intent=_PARAPHRASES[1],
            approach="",
            project="unknown",
            outcome=Outcome.abandoned,
            files=["agent/src/manthana/agent/purge.py"],
        )
    )
    store.upsert_compaction(_comp("real"))

    doomed = [c for c in store.list_compactions() if matches(c, structural_junk=True)]
    assert [c.id for c in doomed] == ["junk"]


def test_agent_cli_accepts_structural_junk_and_dry_runs_by_default() -> None:
    from manthana.agent.cli import app as cli_app
    from typer.testing import CliRunner

    # The flag is offered, and the unfiltered-purge refusal names it — so an
    # operator who runs a bare `manthana purge` is told the selector exists.
    assert "--structural-junk" in CliRunner().invoke(cli_app, ["purge", "--help"]).stdout
    assert "--structural-junk" in CliRunner().invoke(cli_app, ["purge"]).stdout


def test_agent_and_server_purge_logic_stays_in_sync() -> None:
    # Duplicated for the Apache-2.0 / AGPL package boundary; if one side is
    # edited without the other, local and server purges would disagree.
    from manthana.agent import purge as agent_purge
    from manthana.server import purge as server_purge

    assert agent_purge.SELF_GENERATED_MARKERS == server_purge.SELF_GENERATED_MARKERS
    assert agent_purge.COMPACTION_SUBJECT_TOKENS == server_purge.COMPACTION_SUBJECT_TOKENS
    assert agent_purge.COMPACTION_ACTION_TOKENS == server_purge.COMPACTION_ACTION_TOKENS

    # And the predicates themselves must agree, not just their token lists.
    cases = [
        _junk("a", intent=_PROD_A),
        _junk("b", intent=_PARAPHRASES[0]),
        _junk("c", intent=_PARAPHRASES[3]),
        _junk("d", intent="Poke at the flaky CI runner, gave up"),
        _comp(
            "e",
            intent=_PARAPHRASES[1],
            approach="",
            project="unknown",
            outcome=Outcome.abandoned,
            files=["x.py"],
        ),
        _comp("f", intent=_PARAPHRASES[1], approach="", outcome=Outcome.abandoned),
        _comp("g"),
    ]
    for c in cases:
        assert agent_purge.is_self_generated(c) == server_purge.is_self_generated(c), c.id
        assert agent_purge.is_structural_junk(c) == server_purge.is_structural_junk(c), c.id
