"""Auto-compaction (settle window + resume re-compact) + auto-release + cost capture.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from manthana.agent.compact import compact_session, compact_settled
from manthana.agent.llm import MockProvider
from manthana.agent.release import auto_release
from manthana.agent.store import Store
from manthana.schemas import EngineeringCompaction, Mode, Outcome, Role, Session, Surface, Turn

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GOOD = json.dumps({"task_intent": "did the thing", "approach": "this way", "outcome": "success"})


def _session(sid: str, *, mode: Mode = Mode.work, ended: datetime = _T0) -> Session:
    return Session(
        id=sid, actor="e@x", surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=ended, turn_count=1, mode=mode, source_path=f"/x/{sid}.jsonl",
    )


def _seed_session(store: Store, sid: str, *, mode: Mode = Mode.work, ended: datetime = _T0) -> None:
    store.upsert_session(_session(sid, mode=mode, ended=ended))
    store.add_turns([Turn(id=f"{sid}-0", session_id=sid, actor="e@x", seq=0, role=Role.user,
                          content="hi")])


def _comp(cid: str, sid: str, *, ended: datetime = _T0, created: datetime | None = None,
          released: bool = False, hold: bool = False) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid, session_id=sid, actor="e@x", surface=Surface.claude_code, project="p",
        started_at=_T0, ended_at=ended, duration_seconds=1.0, task_intent="t", approach="a",
        outcome=Outcome.success, released=released, hold=hold, created_at=created,
    )


# ── compact_settled: settle window ───────────────────────────────────────────
def test_settle_window_gates_compaction() -> None:
    store = Store.open_memory()
    _seed_session(store, "s1")
    # not settled: file modified 100s ago, window 600s → skipped
    out = compact_settled(store, provider=MockProvider(_GOOD), now=1000.0,
                          settle_seconds=600.0, mtime_of=lambda _p: 900.0)
    assert out == [] and store.get_compaction("comp-s1") is None
    # settled: quiet 700s → compacted
    out = compact_settled(store, provider=MockProvider(_GOOD), now=1000.0,
                          settle_seconds=600.0, mtime_of=lambda _p: 300.0)
    assert len(out) == 1 and store.get_compaction("comp-s1") is not None


def test_personal_sessions_are_never_auto_compacted() -> None:
    store = Store.open_memory()
    _seed_session(store, "s1", mode=Mode.personal)
    out = compact_settled(store, provider=MockProvider(_GOOD), now=1000.0,
                          settle_seconds=600.0, mtime_of=lambda _p: 0.0)
    assert out == [] and store.get_compaction("comp-s1") is None


def test_fresh_digest_not_recompacted_but_stale_resume_is() -> None:
    store = Store.open_memory()
    _seed_session(store, "s1")
    built = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)  # digest built at noon
    store.upsert_compaction(_comp("comp-s1", "s1", created=built))
    now = built.timestamp() + 10_000  # well past the settle window
    # file last modified BEFORE the digest was built → up-to-date → skip
    assert compact_settled(
        store, provider=MockProvider(_GOOD), now=now, settle_seconds=600.0,
        mtime_of=lambda _p: built.timestamp() - 100,
    ) == []
    # file modified AFTER the digest (a resume) → stale → re-compact
    out = compact_settled(
        store, provider=MockProvider(_GOOD), now=now, settle_seconds=600.0,
        mtime_of=lambda _p: built.timestamp() + 100,
    )
    assert len(out) == 1  # stale digest re-compacted


def test_max_per_cycle_caps_backlog_burst() -> None:
    store = Store.open_memory()
    for i in range(5):
        _seed_session(store, f"s{i}")
    out = compact_settled(
        store, provider=MockProvider(_GOOD), now=1000.0, settle_seconds=0.0,
        mtime_of=lambda _p: 0.0, max_per_cycle=2,
    )
    assert len(out) == 2  # only 2 of 5 settled sessions compacted this cycle


def test_recompaction_preserves_hold_released_and_forces_resync() -> None:
    store = Store.open_memory()
    _seed_session(store, "s1")
    assert compact_session(store, "s1", provider=MockProvider(_GOOD)) is not None
    # engineer holds + releases it, and it gets synced
    store.set_hold("comp-s1", hold=True)
    store.mark_released("comp-s1", released=True, released_at=datetime.now(UTC))
    store.mark_synced("comp-s1", datetime.now(UTC))
    assert "comp-s1" in store.synced_ids()
    # resume → re-compact: the trust flags must survive, and sync must be re-armed
    compact_session(store, "s1", provider=MockProvider(_GOOD))
    got = store.get_compaction("comp-s1")
    assert got is not None and got.hold is True and got.released is True
    assert "comp-s1" not in store.synced_ids()  # cleared → updated content re-syncs


# ── auto_release: window + opt-outs ──────────────────────────────────────────
def test_auto_release_after_window_only() -> None:
    store = Store.open_memory()
    _seed_session(store, "s1")
    now = _T0.timestamp() + 10_000
    # within window → not released
    store.upsert_compaction(_comp("comp-s1", "s1", created=datetime.fromtimestamp(now - 100, UTC)))
    assert auto_release(store, now=now, window_seconds=600.0) == 0
    got = store.get_compaction("comp-s1")
    assert got is not None and got.released is False
    # past window → released
    store.upsert_compaction(_comp("comp-s1", "s1", created=datetime.fromtimestamp(now - 700, UTC)))
    assert auto_release(store, now=now, window_seconds=600.0) == 1
    got = store.get_compaction("comp-s1")
    assert got is not None and got.released is True


def test_auto_release_skips_held_and_personal() -> None:
    store = Store.open_memory()
    _seed_session(store, "work1")
    _seed_session(store, "pers1", mode=Mode.personal)
    old = datetime.fromtimestamp(_T0.timestamp(), UTC)
    store.upsert_compaction(_comp("comp-work1", "work1", created=old, hold=True))  # held
    store.upsert_compaction(_comp("comp-pers1", "pers1", created=old))  # personal session
    n = auto_release(store, now=_T0.timestamp() + 10_000, window_seconds=600.0)
    assert n == 0
    work, pers = store.get_compaction("comp-work1"), store.get_compaction("comp-pers1")
    assert work is not None and work.released is False  # held
    assert pers is not None and pers.released is False  # personal never


# ── provider cost capture ────────────────────────────────────────────────────
def test_claude_cli_captures_cost_and_usage(monkeypatch) -> None:
    from manthana.agent.llm import provider as prov

    class _Proc:
        returncode = 0
        stdout = json.dumps(
            {"result": "ok", "total_cost_usd": 0.0123,
             "usage": {"input_tokens": 10, "output_tokens": 5}}
        )
        stderr = ""

    monkeypatch.setattr(prov.subprocess, "run", lambda *a, **k: _Proc())
    p = prov.ClaudeCLIProvider()
    assert p.complete("x") == "ok"
    assert p.last_cost_usd == 0.0123
    assert p.last_usage == {"input_tokens": 10, "output_tokens": 5}
