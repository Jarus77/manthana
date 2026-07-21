"""Agent → server sync transport.

Closes the loop: read sync-eligible compactions from the local store (personal
mode excluded + released-only + fail-closed, via ``eligible_for_sync``), redact
their free text, and POST them to the org server's ingestion API with the team
JWT. Optionally releases raw transcripts (redacted turns) for synced compactions.
Idempotent: already-synced compaction ids are tracked locally and skipped.

This is the ONLY component that moves data off the laptop, and it routes through
``eligible_for_sync`` — never bypass it.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import httpx

from .redaction import Redactor
from .store import Store
from .sync import eligible_for_sync

_log = logging.getLogger(__name__)


class RawPushResult(Enum):
    """Why a raw upload did or did not land.

    The distinction the old bare-bool hid: a 404 is PERMANENT — the server does
    not have this compaction as a released row, and no amount of retrying the
    RAW changes that (the digest would have to be re-pushed first, which sync
    won't do on its own). Retrying it forever re-redacts and re-uploads the whole
    transcript every tick for content the server always rejects.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"  # 4xx — permanent for this content; stop retrying
    RETRY = "retry"              # network / 5xx — transient; try again next run


class SyncError(RuntimeError):
    """Raised when the server rejects a sync request."""


class _HttpClient(Protocol):
    def post(self, url: str, *, json: Any = ..., headers: dict[str, str] = ...) -> Any: ...


@dataclass
class SyncResult:
    pushed: int
    skipped: int
    raw_uploaded: int


class SyncClient:
    """Pushes eligible, redacted compactions to the org server."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: _HttpClient | None = None,
        redactor: Redactor | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client: _HttpClient = client or httpx.Client(base_url=self.base_url, timeout=timeout)
        self._owns_client = client is None
        self.redactor = redactor or Redactor()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def push_compactions(self, compactions: list[Any]) -> int:
        payload = {"compactions": [c.model_dump(mode="json") for c in compactions]}
        resp = self._client.post("/v1/compactions", json=payload, headers=self._headers())
        if resp.status_code != 200:
            raise SyncError(f"ingest failed ({resp.status_code}): {resp.text[:200]}")
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:  # malformed 200 body (e.g. a proxy)
            raise SyncError(f"malformed ingest response: {exc}") from exc
        return int(data.get("ingested", 0))

    def push_raw(self, compaction_id: str, content: str) -> RawPushResult:
        try:
            resp = self._client.post(
                f"/v1/compactions/{compaction_id}/raw",
                json={"content": content},
                headers=self._headers(),
            )
        except (httpx.HTTPError, OSError):
            return RawPushResult.RETRY  # network blip — try again next run
        if resp.status_code == 200:
            return RawPushResult.OK
        if 400 <= resp.status_code < 500:
            # Permanent for THIS content. A 404 (the observed case) means the
            # digest is not on the server; 413/422 mean the transcript is too
            # large or malformed. Either way retrying the same bytes cannot
            # succeed. Self-heals if the session re-compacts: that clears the
            # whole sync-state row, so a changed transcript retries fresh.
            _log.warning(
                "raw upload rejected for %s (HTTP %d); will not retry until "
                "the session re-compacts or you `manthana resync`",
                compaction_id, resp.status_code,
            )
            return RawPushResult.UNAVAILABLE
        return RawPushResult.RETRY  # 5xx — server hiccup, retry

    def sync(
        self, store: Store, *, include_raw: bool = False, now: datetime | None = None
    ) -> SyncResult:
        now = now or datetime.now(UTC)
        sessions = {s.id: s for s in store.list_sessions(limit=1_000_000)}
        eligible = eligible_for_sync(store.list_compactions(limit=1_000_000), sessions)
        already = store.synced_ids()
        fresh = [c for c in eligible if c.id not in already]

        pushed = 0
        if fresh:
            ingested = self.push_compactions([self.redactor.redact_compaction(c) for c in fresh])
            if ingested != len(fresh):
                raise SyncError(f"server ingested {ingested} of {len(fresh)}; not marking synced")
            # Record metadata sync only after a verified push, BEFORE raw upload,
            # so a raw failure never forces re-pushing metadata.
            for compaction in fresh:
                store.mark_synced(compaction.id, now)
            pushed = len(fresh)

        raw_uploaded = 0
        if include_raw:
            # Skip both the already-uploaded AND the permanently-rejected: a 404
            # is not a transient failure to keep hammering, it is a compaction the
            # server does not have. A network failure stays a candidate and one
            # failure never aborts the rest.
            raw_skip = store.raw_synced_ids() | store.raw_unavailable_ids()
            for compaction in eligible:
                if compaction.id in raw_skip:
                    continue
                turns = store.get_turns(compaction.session_id)
                content = "\n".join(
                    json.dumps(self.redactor.redact_turn(t).model_dump(mode="json")) for t in turns
                )
                result = self.push_raw(compaction.id, content)
                if result is RawPushResult.OK:
                    store.mark_raw_synced(compaction.id, now)
                    raw_uploaded += 1
                elif result is RawPushResult.UNAVAILABLE:
                    store.mark_raw_unavailable(compaction.id, now)

        return SyncResult(
            pushed=pushed, skipped=len(eligible) - pushed, raw_uploaded=raw_uploaded
        )

    def close(self) -> None:
        if self._owns_client and hasattr(self._client, "close"):
            self._client.close()  # type: ignore[attr-defined]


__all__ = ["SyncClient", "SyncResult", "SyncError"]
