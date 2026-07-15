"""Public-internet hardening: rate limits, request size cap, security headers.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.hardening import SlidingWindowLimiter
from manthana.server.llm import ScriptedProvider
from manthana.server.storage import InMemoryObjectStore


def _make(**config_kwargs):
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm", **config_kwargs)
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), ScriptedProvider([]))
    )
    return client, config, store


# ── limiter unit ───────────────────────────────────────────────────────────
def test_sliding_window_allows_then_blocks_then_recovers() -> None:
    lim = SlidingWindowLimiter(window=60.0)
    key = ("/v1/enroll", "1.2.3.4")
    assert all(lim.allow(key, 3, now=t) for t in (0.0, 1.0, 2.0))
    assert not lim.allow(key, 3, now=3.0)  # over the limit
    assert lim.allow(key, 3, now=61.0)  # oldest hit aged out
    # a different client IP has its own bucket
    assert lim.allow(("/v1/enroll", "5.6.7.8"), 3, now=3.0)


# ── enroll brute-force protection ──────────────────────────────────────────
def test_enroll_rate_limited_after_10_attempts() -> None:
    client, *_ = _make()
    statuses = [
        client.post("/v1/enroll", json={"code": f"guess-{i}"}).status_code
        for i in range(12)
    ]
    assert statuses[:10] == [400] * 10  # unknown code, but processed
    assert statuses[10] == statuses[11] == 429


def test_login_rate_limited() -> None:
    client, *_ = _make()
    statuses = [
        client.post("/ui/login", data={"token": "wrong"}).status_code for i in range(12)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_unlimited_routes_not_throttled() -> None:
    client, *_ = _make()
    assert all(client.get("/healthz").status_code == 200 for _ in range(40))


# ── request size cap ───────────────────────────────────────────────────────
def test_oversized_request_rejected_413() -> None:
    client, *_ = _make(max_request_bytes=1000)
    resp = client.post(
        "/v1/enroll",
        content=b'{"code": "' + b"x" * 2000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


# ── security headers ───────────────────────────────────────────────────────
def test_security_headers_present() -> None:
    client, *_ = _make()
    resp = client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in resp.headers  # dev/HTTP deploy


def test_hsts_present_on_tls_deploy() -> None:
    client, *_ = _make(cookie_secure=True)
    resp = client.get("/healthz")
    assert "max-age" in resp.headers["Strict-Transport-Security"]
