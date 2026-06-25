"""Founder weekly digest — composed from the founder-query pipeline.

Runs a small set of canned founder queries over a fixed window (one week by default)
and assembles them into a digest. Each section is a real ``run_query`` result, so the
k-anonymity floor and citation-grounding are enforced exactly as in the console — a
section that comes back ``insufficient_data`` is OMITTED (never re-compose a suppressed
cohort into the digest).

Pull-based: a `GET /v1/admin/digest` endpoint + a `manthana-server digest` CLI return
the digest; an external scheduler (e.g. a k8s CronJob) drives the weekly cadence. No
outbound email/SMTP.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from manthana.skills.embed import Embedder

from .config import ServerConfig
from .founder import run_query
from .llm import LLMProvider
from .store import ServerStore

# (title, query) — composed via run_query; the window is forced, so the queries are
# phrased without temporal terms.
_SECTIONS: list[tuple[str, str]] = [
    ("Shipped", "what did the team ship successfully?"),
    ("In progress", "what is the team actively working on?"),
    ("Friction", "what kept failing or blocking the team?"),
]


@dataclass
class DigestSection:
    title: str
    query: str
    narrative: str
    citations: list[str]
    rollup: dict[str, Any] | None


@dataclass
class WeeklyDigest:
    org_id: str
    since: str
    until: str
    sections: list[DigestSection] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)  # section titles withheld by k-anon

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "since": self.since,
            "until": self.until,
            "sections": [
                {
                    "title": s.title,
                    "query": s.query,
                    "narrative": s.narrative,
                    "citations": s.citations,
                    "rollup": s.rollup,
                }
                for s in self.sections
            ],
            "omitted": self.omitted,
        }


def default_window(now: datetime | None = None) -> tuple[str, str]:
    """The last 7 days as (since, until) ISO dates."""
    today = (now or datetime.now(UTC)).date()
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


def build_weekly_digest(
    store: ServerStore,
    config: ServerConfig,
    *,
    org_id: str,
    provider: LLMProvider,
    since: str | None = None,
    until: str | None = None,
    embedder: Embedder | None = None,
) -> WeeklyDigest:
    """Assemble the digest for ``org_id`` over [since, until] (default: last 7 days).
    Founder-aggregate only (never ``allow_individual``); k-anon-insufficient sections are
    omitted, not leaked."""
    if since is None or until is None:
        since, until = default_window()
    digest = WeeklyDigest(org_id=org_id, since=since, until=until)
    for title, query in _SECTIONS:
        res = run_query(
            store, config, org_id=org_id, query=query, provider=provider,
            embedder=embedder, since=since, until=until,
        )
        if res.insufficient_data or not res.citations:
            digest.omitted.append(title)
            continue
        digest.sections.append(
            DigestSection(
                title=title, query=query, narrative=res.narrative, citations=res.citations,
                rollup=res.rollup.__dict__ if res.rollup else None,
            )
        )
    return digest


__all__ = ["WeeklyDigest", "DigestSection", "build_weekly_digest", "default_window"]
