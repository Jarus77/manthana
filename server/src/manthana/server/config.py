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

# What a customer org gets provisioned with on the HOSTED server (`onboard-org`).
# Deliberately not the ServerConfig default: a self-hoster paying their own model
# bill should never be capped by us (llm_monthly_cap_usd stays 0 = unlimited), but
# a hosted tenant spends OUR money, so it gets an explicit per-org override.
#
# It is $100 rather than something tighter because the failure mode of a low cap is
# invisible and awful: enrichment stops, every session stays `pending`, and the wiki
# quietly fills with unsummarised work that looks like a bug rather than a bill. The
# cap exists to stop a runaway, not to ration normal use.
HOSTED_MONTHLY_CAP_USD = 100.0

#: Every provider the server can drive, and for the OpenAI-compatible ones the
#: default endpoint plus the env var their key conventionally lives in. Keeping
#: this as data rather than a chain of ifs means adding the next such service is
#: one line, and the config validator and the factory can never disagree about
#: what is supported.
LLM_PROVIDERS = ("mock", "anthropic", "claude_cli", "openai", "openrouter")

OPENAI_COMPATIBLE: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}

# Insecure placeholders so `ServerConfig()` is constructible in a REPL/dev, but
# rejected at startup (see __post_init__) — a real deploy must override them.
_DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me-in-production"  # noqa: S105 - placeholder
_DEV_ADMIN_TOKEN = "dev-admin-token"  # noqa: S105 - placeholder


@dataclass
class ServerConfig:
    db_url: str = "sqlite:///./manthana-server.db"
    jwt_secret: str = _DEV_JWT_SECRET
    admin_token: str = _DEV_ADMIN_TOKEN
    k_anon_floor: int = K_ANON_FLOOR_DEFAULT
    object_store: str = "memory"  # "memory" | "s3"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # set for MinIO / non-AWS S3
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    # Founder-narrative provider (arch §9): dev/tests use the deterministic mock;
    # the org sets llm_provider="anthropic" + ANTHROPIC_API_KEY for a real model.
    # "claude_cli" is the bring-your-own-model path: the server shells out to a
    # Claude CLI logged in as the user running it, so a solo self-hoster spends
    # the subscription they already pay for instead of a second API key. Works on
    # a laptop; does NOT work in the container images (no binary, no logged-in
    # $HOME), which is why it is opt-in rather than an automatic fallback.
    # "openai" and "openrouter" both speak the OpenAI chat-completions API and
    # share one provider class; they differ only in default base URL and which
    # env var holds the key. "openrouter" is the widest option — it fronts
    # hundreds of models, Anthropic's included, behind a single key.
    llm_provider: str = "mock"  # mock | anthropic | claude_cli | openai | openrouter
    claude_cli_binary: str = "claude"
    # Override the endpoint. Setting this with llm_provider="openai" points the
    # whole pipeline at ANY OpenAI-compatible server — vLLM, Ollama, LM Studio —
    # which is how an org runs Manthana with no third party seeing their sessions.
    llm_base_url: str = ""
    # Explicit key, if you would rather not use the provider's conventional env
    # var (ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY).
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 1024
    # Default monthly server-side LLM budget per org (USD, hosted multi-tenant);
    # 0 disables enforcement (self-hosted default behavior — usage still recorded).
    # Per-org overrides live in the org_quota table (PUT /v1/admin/orgs/{id}/quota).
    llm_monthly_cap_usd: float = 0.0
    # Hard ceiling on an uploaded raw transcript (bytes) — bounds memory on the
    # privileged founder drill path. Default 25 MB.
    max_raw_bytes: int = 25_000_000
    # Mark console cookies Secure (HTTPS-only). Off by default so local/dev HTTP
    # logins keep working; MUST be enabled on any public TLS deployment.
    cookie_secure: bool = False
    # Public base URL of this deployment (no trailing slash), e.g.
    # "https://api.latentspaces.in". Used ONLY to print shareable links (the wiki
    # login link a founder sends a new engineer) — never for routing or auth, so
    # a wrong value produces a bad link, never a security hole. Defaults to
    # localhost so a self-hosted dev instance still prints something usable.
    public_url: str = "http://127.0.0.1:8000"
    # Whole-request Content-Length ceiling (bytes). Slightly above max_raw_bytes
    # so the raw endpoint's own cap stays the binding limit on its path.
    max_request_bytes: int = 30_000_000
    # Retire the legacy server-rendered wiki: when ON, every `/ui/...` wiki page
    # 303s to the equivalent route in the Next.js client (`web/`) instead of
    # rendering HTML. The founder console is unaffected.
    #
    # OFF by default, and the default is load-bearing rather than merely cautious:
    # the client is a SEPARATE deployable. Turning this on where the client is not
    # being served replaces a working wiki with a 404, because the redirect
    # targets (`/`, `/people/...`) belong to the client, not to this server. Only
    # enable it once something in front of this process routes non-`/ui` paths to
    # the client — see deploy/Caddyfile and the `web` service in docker-compose.
    retire_html_wiki: bool = False
    # Founder MCP gateway (spec: manthana-founder-mcp). OFF by default: the transport
    # mount has lifespan/session-manager wiring that must be verified with a live MCP
    # client before enabling, so it never risks the main app until proven.
    enable_founder_mcp: bool = False
    # Default privacy posture for orgs with no explicit override (org_privacy table).
    # "open" = consenting org: the founder sees named, per-individual results.
    # "k_anon" = de-identified, floor-gated aggregates.
    privacy_mode: str = "k_anon"
    # ── server-side digest enrichment ────────────────────────────────────
    # Agents emit deterministic ``source="pending"`` digests; the server fills the
    # qualitative fields on the operator's metered key. OFF by default (same posture
    # as enable_founder_mcp): enabling it starts a background loop that spends real
    # money, so it must be an explicit operator decision. The pass itself is directly
    # callable/testable regardless of this flag.
    enable_enrichment: bool = False
    # Cheap model for enrichment — this is bulk structured summarization, not
    # reasoning. Haiku 4.5 is $1/$5 per Mtok, which lands enrichment around a cent
    # or two per session. Separate from llm_model (the founder-narrative model) so
    # the narrative can stay on a stronger tier.
    enrich_model: str = "claude-haiku-4-5"
    enrich_max_tokens: int = 2048
    # Seconds between background passes.
    enrich_interval_seconds: int = 300
    # Per-org cap for one pass — a deliberate ceiling so a single org's backlog
    # cannot starve every other tenant.
    enrich_batch_per_org: int = 25
    # Whole-pass ceiling across all orgs.
    enrich_max_batch: int = 200
    # A pending digest whose raw never arrived must not retry forever: it is
    # abandoned after this many attempts, or once it is this old.
    enrich_max_attempts: int = 5
    enrich_max_age_days: int = 7
    # Comma-separated Host allowlist for the MCP endpoint's DNS-rebinding check;
    # must include the public domain behind the ALB. "*" disables the check.
    mcp_allowed_hosts: str = "localhost,127.0.0.1,testserver"
    # ── knowledge consolidation (compactions → org-wiki notes) ───────────
    # Turns enriched digests into typed KnowledgeNotes (decisions, conventions,
    # gotchas, benchmarks) via one cheap adjudication call per session. OFF by
    # default, same posture as enable_enrichment: it spends real money in a
    # background loop. The pass is directly callable/testable regardless.
    # ── project overviews ────────────────────────────────────────────────
    # Writes one `project_overview` note per project describing what the project
    # IS, because a project slug is only ever the git repo directory name and
    # "scribe is a project in the LSIITB organisation" is not a description.
    # OFF by default, same posture as enrichment and consolidation: it spends
    # real money in a background loop.
    enable_project_overview: bool = False
    overview_interval_seconds: int = 3600  # a description changes over weeks
    overview_max_per_pass: int = 10        # whole-pass bound across all orgs
    overview_session_limit: int = 40       # sessions fed to one call
    # A one-session project still deserves "what this is" — it is exactly the case
    # where a reader has least context. This was 3, which meant most projects showed
    # a bare "no article has been written yet" forever and looked broken.
    overview_min_sessions: int = 1
    overview_max_attempts: int = 3

    enable_consolidation: bool = False
    # Bulk adjudication, not reasoning — same tier logic as enrich_model.
    consolidate_model: str = "claude-haiku-4-5"
    consolidate_max_tokens: int = 2048
    consolidate_interval_seconds: int = 300
    # Per-org / whole-pass ceilings (mirror the enrichment bounds).
    consolidate_batch_per_org: int = 25
    consolidate_max_batch: int = 200
    # Candidate retrieval: how many live notes one adjudication sees (top-k by
    # cosine, plus entity-overlap hits), and how many notes the retrieval scans.
    consolidate_top_k: int = 8
    consolidate_note_scan: int = 500
    # A compaction whose adjudication keeps failing is abandoned, never retried.
    consolidate_max_attempts: int = 3
    # ── org skill mining bounds ──────────────────────────────────────────
    # Mining is O(n^2) in clustering plus one model call per cluster, so an
    # unbounded run over a large org held the request open until the gateway timed
    # out (504). These two bounds make the work finite; both are REPORTED to the
    # founder in the console — a run that hit a bound says so rather than silently
    # covering less than the founder assumes.
    mine_window_days: int = 90  # only compactions started within this window
    mine_max_items: int = 1000  # newest-first cap on what one run clusters

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
        if self.privacy_mode not in ("open", "k_anon"):
            raise ValueError(f"privacy_mode must be 'open' or 'k_anon', got {self.privacy_mode!r}")
        if self.llm_provider not in LLM_PROVIDERS:
            raise ValueError(
                f"llm_provider must be one of {', '.join(LLM_PROVIDERS)}, got "
                f"{self.llm_provider!r}"
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
        # An interval of 0 busy-loops the background task; the rest are counts
        # whose only sane floor is 1 (0 would silently disable the pass while
        # still reporting it enabled).
        for name in (
            "overview_interval_seconds",
            "overview_max_per_pass",
            "overview_session_limit",
            "overview_min_sessions",
            "overview_max_attempts",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1, got {getattr(self, name)}")
        if self.max_request_bytes < 1:
            raise ValueError(f"max_request_bytes must be >= 1, got {self.max_request_bytes}")
        # Enrichment bounds. A non-positive batch/attempt/age would either spin the
        # background loop hot or retry a never-arriving raw forever; a non-positive
        # interval would busy-loop. (enrich_model is deliberately NOT whitelisted,
        # same reasoning as llm_model.)
        if not 1 <= self.enrich_max_tokens <= 100_000:
            raise ValueError(
                f"enrich_max_tokens must be 1..100000, got {self.enrich_max_tokens}"
            )
        if self.enrich_interval_seconds < 1:
            raise ValueError(
                f"enrich_interval_seconds must be >= 1, got {self.enrich_interval_seconds}"
            )
        if self.enrich_batch_per_org < 1:
            raise ValueError(
                f"enrich_batch_per_org must be >= 1, got {self.enrich_batch_per_org}"
            )
        if self.enrich_max_batch < 1:
            raise ValueError(f"enrich_max_batch must be >= 1, got {self.enrich_max_batch}")
        # Non-positive mining bounds would mean "mine nothing" while the console
        # still reported a run — worse than a slow run.
        if self.mine_window_days < 1:
            raise ValueError(f"mine_window_days must be >= 1, got {self.mine_window_days}")
        if self.mine_max_items < 1:
            raise ValueError(f"mine_max_items must be >= 1, got {self.mine_max_items}")
        if self.enrich_max_attempts < 1:
            raise ValueError(
                f"enrich_max_attempts must be >= 1, got {self.enrich_max_attempts}"
            )
        if self.enrich_max_age_days < 1:
            raise ValueError(
                f"enrich_max_age_days must be >= 1, got {self.enrich_max_age_days}"
            )
        # Consolidation bounds — same rationale as the enrichment bounds above.
        if not 1 <= self.consolidate_max_tokens <= 100_000:
            raise ValueError(
                f"consolidate_max_tokens must be 1..100000, got {self.consolidate_max_tokens}"
            )
        if self.consolidate_interval_seconds < 1:
            raise ValueError(
                "consolidate_interval_seconds must be >= 1, "
                f"got {self.consolidate_interval_seconds}"
            )
        if self.consolidate_batch_per_org < 1:
            raise ValueError(
                f"consolidate_batch_per_org must be >= 1, got {self.consolidate_batch_per_org}"
            )
        if self.consolidate_max_batch < 1:
            raise ValueError(
                f"consolidate_max_batch must be >= 1, got {self.consolidate_max_batch}"
            )
        if self.consolidate_top_k < 1:
            raise ValueError(f"consolidate_top_k must be >= 1, got {self.consolidate_top_k}")
        if self.consolidate_note_scan < 1:
            raise ValueError(
                f"consolidate_note_scan must be >= 1, got {self.consolidate_note_scan}"
            )
        if self.consolidate_max_attempts < 1:
            raise ValueError(
                f"consolidate_max_attempts must be >= 1, got {self.consolidate_max_attempts}"
            )

    @classmethod
    def from_env(cls) -> ServerConfig:
        env = os.environ.get
        return cls(
            db_url=env("MANTHANA_SERVER_DB_URL", cls.db_url),
            jwt_secret=env("MANTHANA_SERVER_JWT_SECRET", cls.jwt_secret),
            admin_token=env("MANTHANA_SERVER_ADMIN_TOKEN", cls.admin_token),
            k_anon_floor=int(env("MANTHANA_SERVER_K_ANON", str(cls.k_anon_floor))),
            object_store=env("MANTHANA_SERVER_OBJECT_STORE", cls.object_store),
            s3_bucket=env("MANTHANA_SERVER_S3_BUCKET", None),
            s3_endpoint_url=env("MANTHANA_SERVER_S3_ENDPOINT_URL", None),
            s3_access_key=env("MANTHANA_SERVER_S3_ACCESS_KEY", None),
            s3_secret_key=env("MANTHANA_SERVER_S3_SECRET_KEY", None),
            llm_provider=env("MANTHANA_SERVER_LLM", cls.llm_provider),
            claude_cli_binary=env("MANTHANA_SERVER_CLAUDE_CLI", cls.claude_cli_binary),
            llm_base_url=env("MANTHANA_SERVER_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=env("MANTHANA_SERVER_LLM_API_KEY", cls.llm_api_key),
            llm_model=env("MANTHANA_SERVER_LLM_MODEL", cls.llm_model),
            llm_max_tokens=int(env("MANTHANA_SERVER_LLM_MAX_TOKENS", str(cls.llm_max_tokens))),
            llm_monthly_cap_usd=float(
                env("MANTHANA_SERVER_LLM_MONTHLY_CAP_USD", str(cls.llm_monthly_cap_usd))
            ),
            max_raw_bytes=int(env("MANTHANA_SERVER_MAX_RAW_BYTES", str(cls.max_raw_bytes))),
            cookie_secure=env("MANTHANA_SERVER_COOKIE_SECURE", "") in ("1", "true", "yes"),
            public_url=env("MANTHANA_SERVER_PUBLIC_URL", cls.public_url).rstrip("/"),
            max_request_bytes=int(
                env("MANTHANA_SERVER_MAX_REQUEST_BYTES", str(cls.max_request_bytes))
            ),
            retire_html_wiki=(
                env("MANTHANA_SERVER_RETIRE_HTML_WIKI", "") in ("1", "true", "yes")
            ),
            enable_founder_mcp=(
                env("MANTHANA_SERVER_ENABLE_FOUNDER_MCP", "") in ("1", "true", "yes")
            ),
            enable_enrichment=(
                env("MANTHANA_SERVER_ENABLE_ENRICHMENT", "") in ("1", "true", "yes")
            ),
            enrich_model=env("MANTHANA_SERVER_ENRICH_MODEL", cls.enrich_model),
            enrich_max_tokens=int(
                env("MANTHANA_SERVER_ENRICH_MAX_TOKENS", str(cls.enrich_max_tokens))
            ),
            enrich_interval_seconds=int(
                env("MANTHANA_SERVER_ENRICH_INTERVAL", str(cls.enrich_interval_seconds))
            ),
            enrich_batch_per_org=int(
                env("MANTHANA_SERVER_ENRICH_BATCH_PER_ORG", str(cls.enrich_batch_per_org))
            ),
            enrich_max_batch=int(
                env("MANTHANA_SERVER_ENRICH_MAX_BATCH", str(cls.enrich_max_batch))
            ),
            enrich_max_attempts=int(
                env("MANTHANA_SERVER_ENRICH_MAX_ATTEMPTS", str(cls.enrich_max_attempts))
            ),
            enrich_max_age_days=int(
                env("MANTHANA_SERVER_ENRICH_MAX_AGE_DAYS", str(cls.enrich_max_age_days))
            ),
            enable_project_overview=(
                env("MANTHANA_SERVER_ENABLE_PROJECT_OVERVIEW", "") in ("1", "true", "yes")
            ),
            overview_interval_seconds=int(
                env("MANTHANA_SERVER_OVERVIEW_INTERVAL", str(cls.overview_interval_seconds))
            ),
            overview_max_per_pass=int(
                env("MANTHANA_SERVER_OVERVIEW_MAX_PER_PASS", str(cls.overview_max_per_pass))
            ),
            overview_session_limit=int(
                env("MANTHANA_SERVER_OVERVIEW_SESSION_LIMIT", str(cls.overview_session_limit))
            ),
            overview_min_sessions=int(
                env("MANTHANA_SERVER_OVERVIEW_MIN_SESSIONS", str(cls.overview_min_sessions))
            ),
            overview_max_attempts=int(
                env("MANTHANA_SERVER_OVERVIEW_MAX_ATTEMPTS", str(cls.overview_max_attempts))
            ),
            enable_consolidation=(
                env("MANTHANA_SERVER_ENABLE_CONSOLIDATION", "") in ("1", "true", "yes")
            ),
            consolidate_model=env("MANTHANA_SERVER_CONSOLIDATE_MODEL", cls.consolidate_model),
            consolidate_max_tokens=int(
                env("MANTHANA_SERVER_CONSOLIDATE_MAX_TOKENS", str(cls.consolidate_max_tokens))
            ),
            consolidate_interval_seconds=int(
                env("MANTHANA_SERVER_CONSOLIDATE_INTERVAL", str(cls.consolidate_interval_seconds))
            ),
            consolidate_batch_per_org=int(
                env(
                    "MANTHANA_SERVER_CONSOLIDATE_BATCH_PER_ORG",
                    str(cls.consolidate_batch_per_org),
                )
            ),
            consolidate_max_batch=int(
                env("MANTHANA_SERVER_CONSOLIDATE_MAX_BATCH", str(cls.consolidate_max_batch))
            ),
            consolidate_top_k=int(
                env("MANTHANA_SERVER_CONSOLIDATE_TOP_K", str(cls.consolidate_top_k))
            ),
            consolidate_note_scan=int(
                env("MANTHANA_SERVER_CONSOLIDATE_NOTE_SCAN", str(cls.consolidate_note_scan))
            ),
            consolidate_max_attempts=int(
                env(
                    "MANTHANA_SERVER_CONSOLIDATE_MAX_ATTEMPTS",
                    str(cls.consolidate_max_attempts),
                )
            ),
            privacy_mode=env("MANTHANA_SERVER_PRIVACY_MODE", cls.privacy_mode),
            mcp_allowed_hosts=env("MANTHANA_SERVER_MCP_ALLOWED_HOSTS", cls.mcp_allowed_hosts),
            mine_window_days=int(
                env("MANTHANA_SERVER_MINE_WINDOW_DAYS", str(cls.mine_window_days))
            ),
            mine_max_items=int(env("MANTHANA_SERVER_MINE_MAX_ITEMS", str(cls.mine_max_items))),
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
