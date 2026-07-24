"""Tenant deletion — removing an org completely, without touching anyone else.

The load-bearing properties, in order of how badly each would hurt:

  1. Deleting org A leaves org B *bit-identical*. This is the one that matters —
     a delete that reached across tenants would be the worst bug the product
     could have.
  2. Nothing of the deleted org survives, in any table, including tables added to
     the schema after this code was written.
  3. Dry run by default, and it really writes nothing.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
import typer.main
from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.cli import app as server_cli
from manthana.server.llm import ScriptedProvider
from manthana.server.purge import delete_org
from manthana.server.storage import InMemoryObjectStore
from manthana.server.store import _DELETE_ORG_KEEP
from manthana.server.tables import SERVER_TABLES

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN = {"X-Admin-Token": "adm"}


def _comp(cid: str, actor: str = "e@x.com") -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=cid,
        actor=actor,
        surface=Surface.claude_code,
        project="demo",
        started_at=_T0,
        ended_at=_T0,
        duration_seconds=1.0,
        task_intent=f"intent {cid}",
        approach="a",
        outcome=Outcome.success,
        est_cost_usd=0.5,
        tier_used="opus",
        released=True,
    )


def _make():
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    obj = InMemoryObjectStore()
    client = TestClient(create_app(config, store, obj, ScriptedProvider([])))
    return client, store, obj


def _data_footprint(store: ServerStore, org: str) -> dict[str, int]:
    """The org's footprint minus its audit trail.

    ``purge_audit`` is deliberately excluded from these comparisons: every delete
    and every dry run appends a row there, so a raw footprint comparison would be
    asserting that auditing does not happen. The audit is asserted on its own,
    where it is the subject rather than noise.
    """
    return {k: v for k, v in store.org_footprint(org).items() if k != "purge_audit"}


def _populate(store: ServerStore, obj: InMemoryObjectStore, org: str, *, n: int = 3) -> None:
    """Give an org a body across as many tables as a real tenant touches."""
    store.create_org(org, org.title())
    store.create_team(f"{org}-core", org, "core")
    store.upsert_actor(f"e@{org}.com", org, f"{org}-core")
    store.upsert_identity(
        f"google:{org}", email=f"e@{org}.com", org_id=org, role="founder"
    )
    store.claim_domain(f"{org}.com", org)
    store.create_invite(
        f"code-{org}", org_id=org, team_id=f"{org}-core", uses=5,
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    store.set_org_quota(org, 42.0)
    store.set_org_privacy(org, "open")
    for i in range(n):
        cid = f"{org}-c{i}"
        store.ingest_compaction(_comp(cid, f"e@{org}.com"), org_id=org, team_id=f"{org}-core")
        key = f"raw/{org}/{cid}.jsonl"
        obj.put(key, b'{"turns": []}')
        store.record_raw(cid, org_id=org, object_key=key)


# ── the property that matters most ─────────────────────────────────────────
def test_deleting_one_org_leaves_the_other_bit_identical() -> None:
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    _populate(store, obj, "beta")
    before = store.org_footprint("beta")

    delete_org(store, obj, org_id="alpha", confirm=True)

    assert store.org_footprint("beta") == before
    assert store.get_org("beta") is not None
    assert store.get_identity("google:beta") is not None
    assert store.get_org_by_domain("beta.com") == "beta"
    assert store.get_org_quota("beta") == 42.0
    assert store.count_compactions("beta") == 3


def test_nothing_of_the_deleted_org_survives() -> None:
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    assert store.org_footprint("alpha")  # precondition: there was something there

    delete_org(store, obj, org_id="alpha", confirm=True)

    # Everything is gone except the audit row recording that it went — which is
    # written after the sweep precisely so it survives it.
    assert _data_footprint(store, "alpha") == {}
    assert store.org_footprint("alpha") == {"purge_audit": 1}
    assert store.get_org("alpha") is None
    assert store.get_identity("google:alpha") is None
    # The domain claim must go too, or a later signup from that domain is offered
    # a join into an org that no longer exists.
    assert store.get_org_by_domain("alpha.com") is None
    assert store.get_invite("code-alpha") is None
    assert store.count_compactions("alpha") == 0


def test_raw_blobs_go_with_the_rows() -> None:
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    keys = store.raw_object_keys("alpha")
    assert keys and all(obj.get(k) is not None for k in keys)

    report = delete_org(store, obj, org_id="alpha", confirm=True)

    assert report.blobs_deleted == len(keys)
    assert all(obj.get(k) is None for k in keys)


def test_a_blob_failure_deletes_nothing_at_all() -> None:
    """Ordering contract, same as purge: blobs first, and a failure must leave the
    DB completely intact so the whole operation can simply be re-run. Committing
    the rows first would strip the only pointers to those blobs forever."""
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    before = _data_footprint(store, "alpha")

    class RefusingStore(InMemoryObjectStore):
        def delete(self, key: str) -> bool:
            return False

    report = delete_org(store, RefusingStore(), org_id="alpha", confirm=True)

    assert report.error and "nothing deleted" in report.error
    assert _data_footprint(store, "alpha") == before
    # The failed attempt is on the record, with its reason.
    audit = store.list_purge_audit("alpha")
    assert audit and audit[0].deleted == 0 and audit[0].data["error"] == report.error


# ── the guard that keeps this correct as the schema grows ──────────────────
#: Tables that carry no ``org_id`` column and so cannot be swept by matching on
#: one. Only the org row itself, which ``delete_org`` removes by primary key.
#: Anything landing here in future needs a deliberate decision, which is what the
#: test below forces.
_NOT_ORG_SCOPED = frozenset({"org"})


def test_every_table_is_swept_excused_or_known_unscoped() -> None:
    """The drift guard.

    A hand-written table list goes stale the moment someone adds a table, and the
    failure mode is a deleted tenant's data quietly surviving its own deletion.

    Note what this deliberately does NOT assert: that every table with an
    ``org_id`` is swept. ``org_scoped_tables()`` is *defined* as "has org_id,
    minus the excused set", so that comparison is true by construction and could
    never fail. The real gap is a new table that holds tenant data under some
    other key — it would have no ``org_id``, so the sweep would silently skip it
    and nothing else would notice. That is what this catches.
    """
    _, store, _ = _make()
    swept = {t.__tablename__ for t in store.org_scoped_tables()}
    accounted = swept | set(_DELETE_ORG_KEEP) | _NOT_ORG_SCOPED
    unaccounted = {str(t.__tablename__) for t in SERVER_TABLES} - accounted
    assert not unaccounted, (
        f"table(s) {sorted(unaccounted)} carry no org_id, so delete_org cannot "
        "sweep them. If they hold tenant data, delete_org needs to handle them "
        "explicitly; if not, add them to _NOT_ORG_SCOPED."
    )


def test_the_revocation_blocklist_survives_deletion() -> None:
    """Dropping a blocklist entry can only ever RE-ENABLE a credential, which is
    the wrong direction for a security control. The rows are hashes, not tenant
    data, so they cost nothing to keep."""
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    store.revoke_token("a-leaked-jwt", reason="test", revoked_by="admin", org_id="alpha")
    assert store.is_token_revoked("a-leaked-jwt")

    delete_org(store, obj, org_id="alpha", confirm=True)

    assert store.is_token_revoked("a-leaked-jwt")


# ── dry run ────────────────────────────────────────────────────────────────
def test_dry_run_counts_without_deleting() -> None:
    _, store, obj = _make()
    _populate(store, obj, "alpha")
    before = _data_footprint(store, "alpha")

    report = delete_org(store, obj, org_id="alpha")

    assert report.dry_run is True
    assert report.counts == before
    assert report.total() == sum(before.values())
    assert _data_footprint(store, "alpha") == before  # nothing moved
    assert store.raw_object_keys("alpha")  # blobs untouched too


def test_dry_run_is_audited() -> None:
    """Knowing someone previewed deleting a customer is itself of governance
    interest, and it ties a later confirmed delete back to its preview."""
    _, store, obj = _make()
    _populate(store, obj, "alpha")

    report = delete_org(store, obj, org_id="alpha")

    audit = store.list_purge_audit("alpha")
    assert report.audit_id is not None
    assert [a.id for a in audit] == [report.audit_id]
    assert audit[0].dry_run is True and audit[0].deleted == 0


def test_the_delete_audit_survives_the_delete_that_wrote_it() -> None:
    """purge_audit carries org_id, so it is swept by delete_org too — the audit row
    is therefore written AFTER the sweep, or the operation would erase its own
    record."""
    _, store, obj = _make()
    _populate(store, obj, "alpha")

    report = delete_org(store, obj, org_id="alpha", confirm=True)

    audit = store.list_purge_audit("alpha")
    assert [a.id for a in audit] == [report.audit_id]
    assert audit[0].dry_run is False and audit[0].deleted > 0


def test_unknown_org_is_a_404_not_a_silent_success() -> None:
    client, store, _ = _make()
    resp = client.post(
        "/v1/admin/delete-org", json={"org_id": "ghost", "confirm": True}, headers=ADMIN
    )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


# ── the HTTP surface ───────────────────────────────────────────────────────
def test_endpoint_requires_admin() -> None:
    client, store, obj = _make()
    _populate(store, obj, "alpha")

    resp = client.post("/v1/admin/delete-org", json={"org_id": "alpha", "confirm": True})

    assert resp.status_code == 401
    assert store.get_org("alpha") is not None


def test_endpoint_defaults_to_a_dry_run() -> None:
    client, store, obj = _make()
    _populate(store, obj, "alpha")

    resp = client.post("/v1/admin/delete-org", json={"org_id": "alpha"}, headers=ADMIN)

    body = resp.json()
    assert body["dry_run"] is True and body["total"] > 0
    assert store.get_org("alpha") is not None


def test_endpoint_deletes_on_confirm() -> None:
    client, store, obj = _make()
    _populate(store, obj, "alpha")
    _populate(store, obj, "beta")

    body = client.post(
        "/v1/admin/delete-org", json={"org_id": "alpha", "confirm": True}, headers=ADMIN
    ).json()

    assert body["dry_run"] is False and body["total"] > 0
    assert body["counts"]["org"] == 1
    assert store.get_org("alpha") is None
    assert store.get_org("beta") is not None


# ── the CLI contract ───────────────────────────────────────────────────────
def test_cli_requires_an_explicit_confirm_flag() -> None:
    """The safety is the flag, so assert it exists on the real Click command
    rather than trusting help text that rich re-wraps per terminal width."""
    group = typer.main.get_command(server_cli)
    cmd = group.commands["delete-org"]  # type: ignore[attr-defined]
    opts = {opt for param in cmd.params for opt in param.opts}
    assert "--confirm" in opts
    confirm = next(p for p in cmd.params if "--confirm" in p.opts)
    assert confirm.default is False  # dry run unless asked otherwise
