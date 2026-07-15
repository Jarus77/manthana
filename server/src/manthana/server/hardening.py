"""Public-internet hardening: rate limits, size cap, security headers.

The hosted deployment exposes the server on a public HTTPS endpoint, so the
credential-accepting routes (invite redemption, console logins) and the
LLM-backed query routes get per-client-IP sliding-window rate limits, every
request gets a Content-Length ceiling (the raw endpoint's own 25 MB cap stays
the tighter bound on its path), and responses carry standard security headers.

In-process limiter by design: the pilot runs a single server task, so a shared
Redis would be pure overhead. Behind a load balancer the client IP is taken
from ``request.client`` — run uvicorn with ``--proxy-headers`` so that reflects
X-Forwarded-For (see server/Dockerfile), otherwise every request would share
the LB's address and rate-limit collectively.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .config import ServerConfig

_WINDOW_SECONDS = 60.0

# (method, exact path, max requests per window per client IP). Tight limits on
# the unauthenticated credential routes (an invite/token can be brute-forced),
# looser ones on the authenticated LLM-backed query routes (cost control).
RATE_LIMITS: dict[tuple[str, str], int] = {
    ("POST", "/v1/enroll"): 10,
    ("POST", "/ui/login"): 10,
    ("POST", "/ui/manager/login"): 10,
    ("POST", "/v1/founder/query"): 30,
    ("POST", "/v1/manager/query"): 30,
    ("POST", "/ui/query"): 30,
    ("POST", "/ui/manager/query"): 30,
}


class SlidingWindowLimiter:
    """Per-key sliding-window counter (thread-safe enough: FastAPI middleware
    runs on the event loop, so calls are serialized)."""

    def __init__(self, window: float = _WINDOW_SECONDS) -> None:
        self._window = window
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: tuple[str, str], limit: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def install_hardening(app: FastAPI, config: ServerConfig) -> None:
    limiter = SlidingWindowLimiter()

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > config.max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "request too large"})
        limit = RATE_LIMITS.get((request.method, request.url.path))
        if limit is not None:
            client_ip = request.client.host if request.client else "unknown"
            if not limiter.allow((request.url.path, client_ip), limit):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded — retry in a minute"},
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if config.cookie_secure:  # TLS deploy → also pin HTTPS in the browser
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


__all__ = ["install_hardening", "SlidingWindowLimiter", "RATE_LIMITS"]
