"""Bounded org skill mining: window/cap enforcement, coverage honesty, cached vectors.

The founder's "Mine org skills" click used to load every released compaction,
re-embed all of it, cluster it in-process and make one model call per cluster, all
inside the request handler — which returned 504 instead of an answer. These tests
pin the three properties that fix is built on: the work is bounded, the bound is
REPORTED rather than silently applied, and repeat runs reuse the stored vectors
instead of re-embedding.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from manthana.schemas import EngineeringCompaction, Outcome, Surface
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import MockProvider
from manthana.server.mining import DONE, QUOTA, run_mining, scope
from manthana.server.storage import InMemoryObjectStore
from manthana.skills.embed import HashingEmbedder

ADMIN = {"X-Admin-Token": "adm"}
_INTENT = "fix flaky pytest timeout in CI"


class CountingEmbedder:
    """HashingEmbedder that records how many texts it was asked to embed."""

    def __init__(self) -> None:
        self._inner = HashingEmbedder()
        self.dim = self._inner.dim
        self.embedded = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return self._inner.embed(texts)


def _comp(cid: str, actor: str, started: datetime, intent: str = _INTENT) -> EngineeringCompaction:
    return EngineeringCompaction(
        id=cid,
        session_id=cid,
        actor=actor,
        surface=Surface.claude_code,
        project="demo",
        started_at=started,
        ended_at=started,
        duration_seconds=1.0,
        task_intent=intent,
        approach="raised the wait budget and de-flaked the fixture",
        outcome=Outcome.success,
        released=True,
    )


def _store(*, recent: int = 0, old: int = 0, org: str = "o1") -> ServerStore:
    """A store with ``recent`` in-window and ``old`` far-out-of-window compactions."""
    store = ServerStore.open("sqlite://")
    store.create_org(org, "O")
    now = datetime.now(UTC)
    for i in range(recent):
        store.ingest_compaction(
            _comp(f"r{i}", f"e{i}@x.com", now - timedelta(days=1)), org_id=org, team_id="t1"
        )
    for i in range(old):
        store.ingest_compaction(
            _comp(f"o{i}", f"e{i}@x.com", now - timedelta(days=900)), org_id=org, team_id="t1"
        )
    return store


def _config(**kw: object) -> ServerConfig:
    return ServerConfig(jwt_secret="x" * 40, admin_token="adm", **kw)  # type: ignore[arg-type]


# ── the work is bounded ───────────────────────────────────────────────────
def test_scope_excludes_compactions_older_than_the_window() -> None:
    store = _store(recent=4, old=6)
    comps, since, matched = scope(store, _config(mine_window_days=90), "o1")
    assert matched == 4  # the 6 old ones are outside the window entirely
    assert len(comps) == 4
    assert since  # the window start is reported, not implicit
    assert all(c.id.startswith("r") for c in comps)


def test_scope_caps_rows_and_still_reports_the_true_total() -> None:
    store = _store(recent=10)
    comps, _since, matched = scope(store, _config(mine_window_days=90, mine_max_items=3), "o1")
    assert len(comps) == 3  # capped
    assert matched == 10  # but the honest total is preserved, not overwritten by the cap


def test_run_reports_a_cap_it_hit_instead_of_implying_full_coverage() -> None:
    store = _store(recent=10)
    run = run_mining(
        store, _config(mine_window_days=90, mine_max_items=3), "o1", provider=MockProvider("{}")
    )
    assert run.state == DONE
    assert run.capped is True
    note = run.coverage_note()
    assert "3 most recent of 10" in note  # says exactly what it left out
    assert "not included" in note


def test_run_within_bounds_claims_full_coverage() -> None:
    store = _store(recent=4)
    run = run_mining(store, _config(mine_window_days=90, mine_max_items=100), "o1",
                     provider=MockProvider("{}"))
    assert run.capped is False
    assert "mined all 4" in run.coverage_note()


def test_empty_window_is_a_clean_done_not_a_failure() -> None:
    store = _store(old=5)  # everything is outside the window
    run = run_mining(store, _config(mine_window_days=90), "o1", provider=MockProvider("{}"))
    assert run.state == DONE
    assert run.scanned == 0 and run.queued == 0
    assert "no released sessions" in run.detail


# ── cached vectors: a repeat run must not re-embed ────────────────────────
def test_second_run_reuses_cached_vectors_instead_of_re_embedding() -> None:
    store = _store(recent=6)
    config = _config(mine_window_days=90)
    embedder = CountingEmbedder()

    run_mining(store, config, "o1", provider=MockProvider("{}"), embedder=embedder)
    first = embedder.embedded
    assert first == 6  # cold cache: everything embedded once

    run_mining(store, config, "o1", provider=MockProvider("{}"), embedder=embedder)
    assert embedder.embedded == first  # warm cache: nothing re-embedded


def test_only_new_compactions_are_embedded_on_a_later_run() -> None:
    store = _store(recent=4)
    config = _config(mine_window_days=90)
    embedder = CountingEmbedder()
    run_mining(store, config, "o1", provider=MockProvider("{}"), embedder=embedder)
    assert embedder.embedded == 4

    store.ingest_compaction(
        _comp("new1", "e9@x.com", datetime.now(UTC)), org_id="o1", team_id="t1"
    )
    run_mining(store, config, "o1", provider=MockProvider("{}"), embedder=embedder)
    assert embedder.embedded == 5  # only the new row


# ── quota still respected ─────────────────────────────────────────────────
def test_exhausted_quota_is_recorded_as_the_run_outcome() -> None:
    store = _store(recent=4)
    store.set_org_quota("o1", 1.0)
    store.add_llm_usage(
        "o1", datetime.now(UTC).strftime("%Y-%m"),
        input_tokens=0, output_tokens=0, est_cost_usd=5.0,
    )
    run = run_mining(store, _config(mine_window_days=90), "o1", provider=MockProvider("{}"))
    assert run.state == QUOTA
    assert run.queued == 0  # nothing was spent or queued past the cap


def test_mine_endpoint_returns_429_when_quota_is_spent() -> None:
    store = _store(recent=4)
    store.set_org_quota("o1", 1.0)
    store.add_llm_usage(
        "o1", datetime.now(UTC).strftime("%Y-%m"),
        input_tokens=0, output_tokens=0, est_cost_usd=5.0,
    )
    client = TestClient(
        create_app(_config(mine_window_days=90), store, InMemoryObjectStore(), MockProvider("{}"))
    )
    resp = client.post("/v1/admin/mine-skills", json={"org_id": "o1"}, headers=ADMIN)
    assert resp.status_code == 429


# ── the admin API reports its coverage too ────────────────────────────────
def test_mine_endpoint_reports_coverage_alongside_proposals() -> None:
    store = _store(recent=10)
    client = TestClient(
        create_app(
            _config(mine_window_days=90, mine_max_items=4),
            store, InMemoryObjectStore(), MockProvider("{}"),
        )
    )
    body = client.post("/v1/admin/mine-skills", json={"org_id": "o1"}, headers=ADMIN).json()
    cov = body["coverage"]
    assert cov["capped"] is True
    assert cov["scanned"] == 4 and cov["matched"] == 10
    assert cov["window_days"] == 90
    assert "4 most recent of 10" in cov["coverage"]


# ── the console: prompt response + a status page that states the scope ────
def test_console_mine_redirects_to_status_rather_than_blocking() -> None:
    store = _store(recent=4)
    client = TestClient(
        create_app(_config(mine_window_days=90), store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    client.post("/ui/login", data={"token": "adm"})
    resp = client.post("/ui/mine", data={"org_id": "o1"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/mine-status?org_id=o1"


def test_status_page_states_what_the_run_covered() -> None:
    store = _store(recent=10)
    client = TestClient(
        create_app(
            _config(mine_window_days=90, mine_max_items=3),
            store, InMemoryObjectStore(), MockProvider("{}"),
        ),
        follow_redirects=False,
    )
    client.post("/ui/login", data={"token": "adm"})
    client.post("/ui/mine", data={"org_id": "o1"})
    page = client.get("/ui/mine-status?org_id=o1")
    assert page.status_code == 200
    assert "3 most recent of 10" in page.text  # the cap is stated, not silent
    assert "90 days" in page.text
    assert "nothing is published until you approve" in page.text


def test_mine_status_requires_auth() -> None:
    store = _store(recent=4)
    client = TestClient(
        create_app(_config(), store, InMemoryObjectStore(), MockProvider("{}")),
        follow_redirects=False,
    )
    resp = client.get("/ui/mine-status?org_id=o1")
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/login"
    assert "o1" not in resp.text
