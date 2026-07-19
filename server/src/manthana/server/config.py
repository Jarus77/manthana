"""Server configuration (from ``MANTHANA_SERVER_*`` env vars).

Dev defaults run on SQLite + an in-memory object store with insecure secrets;
production MUST override JWT secret, admin token, DB URL, and object store.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

K_ANON_FLOOR_DEFAULT = 4

# Insecure placeholders so `ServerConfig()` is constructible in a REPL/dev, but
# rejected at startup (see __post_init__) — a real deploy must override them.
_DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me-in-production"  # noqa: S105 - placeholder
_DEV_ADMIN_TOKEN = "dev-admin-token"  # noqa: S105 - placeholder


@dataclass
class ServerConfig:
    db_url: str = "sqlite:///./manthana-server.db"
    jwt_secret: str = _DEV_JWT_SECRET
    admin_token: str = _DEV_ADMIN_TOKEN
    # Optional: enables the audited per-individual manager view. None = disabled
    # (the founder console stays k-anon-only). Distinct from admin_token.
    manager_token: str | None = None
    k_anon_floor: int = K_ANON_FLOOR_DEFAULT
    object_store: str = "memory"  # "memory" | "s3"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # set for MinIO / non-AWS S3
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    # Founder-narrative provider (arch §9): dev/tests use the deterministic mock;
    # the org sets llm_provider="anthropic" + ANTHROPIC_API_KEY for a real model.
    llm_provider: str = "mock"  # "mock" | "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 1024
    # Default monthly server-side LLM budget per org (USD, hosted multi-tenant);
    # 0 disables enforcement (self-hosted default behavior — usage still recorded).
    # Per-org overrides live in the org_quota table (PUT /v1/admin/orgs/{id}/quota).
    llm_monthly_cap_usd: float = 0.0
    # Hard ceiling on an uploaded raw transcript (bytes) — bounds memory on the
    # privileged manager drill path. Default 25 MB.
    max_raw_bytes: int = 25_000_000
    # Mark console cookies Secure (HTTPS-only). Off by default so local/dev HTTP
    # logins keep working; MUST be enabled on any public TLS deployment.
    cookie_secure: bool = False
    # Whole-request Content-Length ceiling (bytes). Slightly above max_raw_bytes
    # so the raw endpoint's own cap stays the binding limit on its path.
    max_request_bytes: int = 30_000_000
    # Founder MCP gateway (spec: manthana-founder-mcp). OFF by default: the transport
    # mount has lifespan/session-manager wiring that must be verified with a live MCP
    # client before enabling, so it never risks the main app until proven.
    enable_founder_mcp: bool = False
    # Default privacy posture for orgs with no explicit override (org_privacy table).
    # "open" = consenting org: the founder sees named, per-individual results
    # (founder==manager). "k_anon" = de-identified, floor-gated aggregates.
    privacy_mode: str = "k_anon"
    # Comma-separated Host allowlist for the MCP endpoint's DNS-rebinding check;
    # must include the public domain behind the ALB. "*" disables the check.
    mcp_allowed_hosts: str = "localhost,127.0.0.1,testserver"

    def __post_init__(self) -> None:
        # An empty admin token or JWT secret is an auth bypass: hmac.compare_digest
        # ("", "") is True, so an empty cookie/header would authenticate. Reject it
        # (the dev defaults above are non-empty; only an explicit "" override trips this).
        if not self.admin_token:
            raise ValueError("admin_token must not be empty (set MANTHANA_SERVER_ADMIN_TOKEN)")
        if not self.jwt_secret:
            raise ValueError("jwt_secret must not be empty (set MANTHANA_SERVER_JWT_SECRET)")
        # Fail closed on the shipped placeholders so a deploy can't silently run
        # with publicly-known secrets (anyone could mint admin/team tokens).
        if self.admin_token == _DEV_ADMIN_TOKEN or self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError(
                "refusing to run with the insecure dev defaults — set "
                "MANTHANA_SERVER_ADMIN_TOKEN and MANTHANA_SERVER_JWT_SECRET "
                "(copy .env.example to .env)"
            )
        # An empty manager_token would auth-bypass (compare_digest("","")=True); a
        # None disables the manager view entirely, which is the safe default.
        if self.manager_token is not None and not self.manager_token:
            raise ValueError("manager_token must be non-empty when set (or leave it unset/None)")
        if self.privacy_mode not in ("open", "k_anon"):
            raise ValueError(f"privacy_mode must be 'open' or 'k_anon', got {self.privacy_mode!r}")
        if self.llm_provider not in ("mock", "anthropic"):
            raise ValueError(
                f"llm_provider must be 'mock' or 'anthropic', got {self.llm_provider!r}"
            )
        # A non-positive k-anon floor would silently disable the privacy floor; a
        # non-positive/absurd max_tokens is a config typo (0 → empty narrative,
        # huge → runaway cost). Note: llm_model is intentionally NOT whitelisted —
        # hardcoding model IDs would reject valid future models.
        if self.k_anon_floor < 1:
            raise ValueError(f"k_anon_floor must be >= 1, got {self.k_anon_floor}")
        if not 1 <= self.llm_max_tokens <= 100_000:
            raise ValueError(f"llm_max_tokens must be 1..100000, got {self.llm_max_tokens}")
        if self.llm_monthly_cap_usd < 0:
            raise ValueError(
                f"llm_monthly_cap_usd must be >= 0 (0 = unlimited), got {self.llm_monthly_cap_usd}"
            )
        if self.max_raw_bytes < 1:
            raise ValueError(f"max_raw_bytes must be >= 1, got {self.max_raw_bytes}")
        if self.max_request_bytes < 1:
            raise ValueError(f"max_request_bytes must be >= 1, got {self.max_request_bytes}")

    @classmethod
    def from_env(cls) -> ServerConfig:
        env = os.environ.get
        return cls(
            db_url=env("MANTHANA_SERVER_DB_URL", cls.db_url),
            jwt_secret=env("MANTHANA_SERVER_JWT_SECRET", cls.jwt_secret),
            admin_token=env("MANTHANA_SERVER_ADMIN_TOKEN", cls.admin_token),
            manager_token=env("MANTHANA_SERVER_MANAGER_TOKEN", None),
            k_anon_floor=int(env("MANTHANA_SERVER_K_ANON", str(cls.k_anon_floor))),
            object_store=env("MANTHANA_SERVER_OBJECT_STORE", cls.object_store),
            s3_bucket=env("MANTHANA_SERVER_S3_BUCKET", None),
            s3_endpoint_url=env("MANTHANA_SERVER_S3_ENDPOINT_URL", None),
            s3_access_key=env("MANTHANA_SERVER_S3_ACCESS_KEY", None),
            s3_secret_key=env("MANTHANA_SERVER_S3_SECRET_KEY", None),
            llm_provider=env("MANTHANA_SERVER_LLM", cls.llm_provider),
            llm_model=env("MANTHANA_SERVER_LLM_MODEL", cls.llm_model),
            llm_max_tokens=int(env("MANTHANA_SERVER_LLM_MAX_TOKENS", str(cls.llm_max_tokens))),
            llm_monthly_cap_usd=float(
                env("MANTHANA_SERVER_LLM_MONTHLY_CAP_USD", str(cls.llm_monthly_cap_usd))
            ),
            max_raw_bytes=int(env("MANTHANA_SERVER_MAX_RAW_BYTES", str(cls.max_raw_bytes))),
            cookie_secure=env("MANTHANA_SERVER_COOKIE_SECURE", "") in ("1", "true", "yes"),
            max_request_bytes=int(
                env("MANTHANA_SERVER_MAX_REQUEST_BYTES", str(cls.max_request_bytes))
            ),
            enable_founder_mcp=(
                env("MANTHANA_SERVER_ENABLE_FOUNDER_MCP", "") in ("1", "true", "yes")
            ),
            privacy_mode=env("MANTHANA_SERVER_PRIVACY_MODE", cls.privacy_mode),
            mcp_allowed_hosts=env("MANTHANA_SERVER_MCP_ALLOWED_HOSTS", cls.mcp_allowed_hosts),
        )


def persisted_secrets(data_dir: Path) -> tuple[str, str]:
    """Load ``(jwt_secret, admin_token)`` from ``<data_dir>/server-secrets.toml``, generating
    and persisting them (chmod 0600) on first run. Stable across restarts — regenerating would
    invalidate every already-issued agent token (they're signed with ``jwt_secret``). Used by
    ``manthana-server quickstart`` for a zero-config secure boot."""
    path = data_dir / "server-secrets.toml"
    if path.exists():
        data = tomllib.loads(path.read_text())
        sec = data.get("secrets", {})
        jwt, admin = sec.get("jwt_secret"), sec.get("admin_token")
        if jwt and admin:
            return str(jwt), str(admin)
    data_dir.mkdir(parents=True, exist_ok=True)
    jwt, admin = secrets.token_hex(32), secrets.token_hex(24)
    path.write_text(f'[secrets]\njwt_secret = "{jwt}"\nadmin_token = "{admin}"\n')
    try:
        path.chmod(0o600)
    except OSError:  # best-effort on filesystems without POSIX perms
        pass
    return jwt, admin


__all__ = ["ServerConfig", "K_ANON_FLOOR_DEFAULT", "persisted_secrets"]
