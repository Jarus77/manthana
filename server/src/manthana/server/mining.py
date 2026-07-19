"""Bounded org skill mining + its run status.

Mining used to be unbounded and synchronous: every released compaction was loaded
(``limit=100_000``), re-embedded from scratch on every run, clustered with an
O(n^2) pure-Python pass, and then synthesized with one blocking model call per
cluster — all inside the request handler. At ~1100 compactions the founder's
"Mine org skills" click held the connection past the gateway timeout and returned
504 with nothing to show for it.

Three changes make it honest and fast:

1. **Bounded scope.** A run covers released compactions from the last
   ``mine_window_days``, newest first, capped at ``mine_max_items``. Both bounds
   are recorded on the ``MineRun`` and rendered in the console, because the
   standing rule is that a truncated answer must SAY it is truncated.
2. **Cached vectors.** Embeddings come from the same DB-backed cache the founder
   query path uses (``vectors.ensure_vectors``), so a repeat run embeds only what
   is new instead of the entire corpus.
3. **Off the request thread.** The console starts a run in the background and
   redirects immediately to a status page; the caller never waits on the model
   calls. Quota is pre-checked before the run starts so an exhausted org still
   gets its 429 up front, and a quota exhaustion mid-run is recorded as the run's
   outcome rather than lost.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from manthana.skills import mine_org
from manthana.skills.embed import Embedder, default_embedder

from .config import ServerConfig
from .llm import LLMProvider
from .metering import QuotaExceededError, month_key
from .store import ServerStore
from .vectors import ensure_vectors

_log = logging.getLogger(__name__)

RUNNING = "running"
DONE = "done"
FAILED = "failed"
QUOTA = "quota"


@dataclass
class MineRun:
    """One org-mining run: what it covered and how it ended."""

    org_id: str
    state: str = RUNNING
    window_days: int = 0
    since: str = ""
    matched: int = 0  # released compactions inside the window
    scanned: int = 0  # how many were actually clustered (<= matched)
    max_items: int = 0
    queued: int = 0  # skill proposals enqueued for approval
    # Summary of what this run proposed (name/description/contributor_count/evidence).
    # Carried on the run so callers report THIS run's output rather than re-reading the
    # shared action queue, which also holds proposals from earlier runs.
    proposals: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str = ""

    @property
    def capped(self) -> bool:
        """True when the item cap, not the window, decided what was covered."""
        return self.scanned < self.matched

    def coverage_note(self) -> str:
        """Plain-language statement of exactly what this run looked at."""
        window = f"the last {self.window_days} days"
        if self.capped:
            return (
                f"mined the {self.scanned} most recent of {self.matched} released "
                f"sessions from {window} — older sessions in that window were not "
                "included in this run"
            )
        return f"mined all {self.scanned} released sessions from {window}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "state": self.state,
            "window_days": self.window_days,
            "since": self.since,
            "matched": self.matched,
            "scanned": self.scanned,
            "max_items": self.max_items,
            "capped": self.capped,
            "queued": self.queued,
            "proposals": self.proposals,
            "coverage": self.coverage_note(),
            "detail": self.detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class MineRunRegistry:
    """Last run per org, in process memory.

    Deliberately not persisted: this is progress feedback for a console click, not
    an audit record — the durable artifacts of a run are the queued proposals in
    the action queue. A restart simply means the console shows "no run yet".
    """

    def __init__(self) -> None:
        self._runs: dict[str, MineRun] = {}
        self._lock = threading.Lock()

    def get(self, org_id: str) -> MineRun | None:
        with self._lock:
            return self._runs.get(org_id)

    def start(self, org_id: str, run: MineRun) -> None:
        with self._lock:
            self._runs[org_id] = run

    def is_running(self, org_id: str) -> bool:
        run = self.get(org_id)
        return run is not None and run.state == RUNNING


def check_quota(store: ServerStore, config: ServerConfig, org_id: str) -> None:
    """Raise ``QuotaExceededError`` if the org's monthly AI budget is already spent.

    Mirrors ``MeteredProvider.complete``'s pre-call check so a background run's quota
    failure still reaches the founder as a 429 on the click that started it, instead
    of disappearing into a background task.
    """
    override = store.get_org_quota(org_id)
    cap = override if override is not None else config.llm_monthly_cap_usd
    if cap > 0:
        spent = store.get_llm_usage(org_id, month_key()).est_cost_usd
        if spent >= cap:
            raise QuotaExceededError(org_id, cap, spent)


def scope(
    store: ServerStore, config: ServerConfig, org_id: str, *, now: datetime | None = None
) -> tuple[list[Any], str, int]:
    """The bounded input set for a run: ``(compactions, since_iso, matched_count)``.

    ``compactions`` is already truncated to ``mine_max_items`` (newest first) while
    ``matched_count`` is the untruncated size, so the caller can report the gap.
    """
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=config.mine_window_days)).date().isoformat()
    matched = store.query_compactions(org_id=org_id, since=since, limit=config.mine_max_items + 1)
    if len(matched) > config.mine_max_items:
        # One extra row was fetched purely to detect the cap. Get the real total with a
        # COUNT-shaped query (ids only, no row decode) so the console can say how much
        # was left out — loading every row just to count them is the cost we removed.
        total = store.count_compactions(org_id, since=since)
        return matched[: config.mine_max_items], since, total
    return matched, since, len(matched)


def run_mining(
    store: ServerStore,
    config: ServerConfig,
    org_id: str,
    *,
    provider: LLMProvider,
    embedder: Embedder | None = None,
    registry: MineRunRegistry | None = None,
    now: datetime | None = None,
) -> MineRun:
    """Mine one org within the configured bounds and enqueue the proposals.

    Never raises: every outcome (including quota exhaustion) is recorded on the
    returned ``MineRun``, because this runs detached from the request that started
    it and an exception there would be invisible to the founder.
    """
    run = MineRun(
        org_id=org_id, window_days=config.mine_window_days, max_items=config.mine_max_items
    )
    if registry is not None:
        registry.start(org_id, run)
    try:
        # Fail closed before doing any work: don't start a run the org cannot pay to
        # finish. Not merely a shortcut for the MeteredProvider's own pre-call check —
        # a run that clusters into zero proposals never calls the provider at all, and
        # would otherwise report "done" while the org is over budget.
        check_quota(store, config, org_id)
        compactions, since, matched = scope(store, config, org_id, now=now)
        run.since, run.matched, run.scanned = since, matched, len(compactions)
        if not compactions:
            run.state, run.detail = DONE, "no released sessions in the window"
            return run
        embedder = embedder or default_embedder()
        # Reuse the DB vector cache — the single biggest win. Without it every run
        # re-embeds the whole window, which is where the minutes went.
        vectors = ensure_vectors(store, org_id, compactions, embedder)
        proposals = mine_org(
            compactions,
            provider=provider,
            embedder=embedder,
            max_items=config.mine_max_items,
            vectors=vectors,
        )
        for proposal in proposals:
            summary = {
                "name": proposal.draft.name,
                "description": proposal.draft.description,
                "contributor_count": proposal.provenance.contributor_count,
                "evidence": proposal.provenance.evidence,
            }
            store.enqueue_action(
                action_id="auto_draft_org_skill",
                org_id=org_id,
                payload={**summary, "skill_md": proposal.skill_md},
            )
            run.proposals.append(summary)
        run.queued = len(proposals)
        run.state = DONE
        run.detail = f"{run.queued} skill proposal(s) queued for approval"
    except QuotaExceededError as exc:
        run.state, run.detail = QUOTA, str(exc)
    except Exception as exc:  # noqa: BLE001 - a background run must record, never crash
        _log.exception("org skill mining failed for %s", org_id)
        run.state, run.detail = FAILED, f"{type(exc).__name__}: {exc}"
    finally:
        run.finished_at = datetime.now(UTC).isoformat()
    return run


__all__ = [
    "MineRun",
    "MineRunRegistry",
    "check_quota",
    "run_mining",
    "scope",
    "RUNNING",
    "DONE",
    "FAILED",
    "QUOTA",
]
