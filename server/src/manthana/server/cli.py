"""Manthana org-server CLI (``manthana-server``).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import os
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from manthana.schemas import encode_invite

from .auth import issue_team_token
from .config import (
    HOSTED_MONTHLY_CAP_USD,
    K_ANON_FLOOR_DEFAULT,
    ServerConfig,
    persisted_secrets,
)
from .store import ServerStore

app = typer.Typer(help="Manthana org server.", no_args_is_help=True, add_completion=False)

_DEFAULT_DATA_DIR = Path.home() / ".manthana-server"


def _resolve_config(data: str = "") -> ServerConfig:
    """Config for the admin CLI. Prod (env secrets set) → ``from_env``. Otherwise the
    zero-infra quickstart path: persisted secrets + a data-dir SQLite DB, so `quickstart`,
    `enroll`, and `invites` all share one server + DB with no env wiring."""
    data_dir = Path(data).expanduser() if data else _DEFAULT_DATA_DIR
    env = os.environ.get
    if env("MANTHANA_SERVER_JWT_SECRET") and env("MANTHANA_SERVER_ADMIN_TOKEN"):
        return ServerConfig.from_env()
    jwt, admin = persisted_secrets(data_dir)
    db_url = env("MANTHANA_SERVER_DB_URL") or f"sqlite:///{data_dir / 'manthana-server.db'}"
    return ServerConfig(jwt_secret=jwt, admin_token=admin, db_url=db_url, object_store="memory")


def _tailscale_public_url(port: int) -> str | None:
    """This machine's tailnet HTTPS URL (from `tailscale status --json`), or None."""
    import json
    import subprocess

    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout
        dns = str(json.loads(out).get("Self", {}).get("DNSName", "")).rstrip(".")
        return f"https://{dns}" if dns else None
    except Exception:  # noqa: BLE001 - tailscale missing/erroring → no URL, caller falls back
        return None


def _run_server(
    *, host: str, port: int, public_url: str, k_anon: int | None, data: str, tailscale: bool
) -> None:
    """Serve the org server. Secrets from MANTHANA_SERVER_* env if set, else auto-generated +
    persisted (zero-config pilot). ``--tailscale`` fronts loopback with tailnet HTTPS."""
    import shutil
    import subprocess

    import uvicorn

    from .app import create_app
    from .llm import make_provider
    from .storage import make_object_store

    if tailscale:
        if not shutil.which("tailscale"):
            typer.echo("✗ tailscale not found — install it from https://tailscale.com/download")
            raise typer.Exit(code=1)
        subprocess.run(["tailscale", "serve", "--bg", f"http://127.0.0.1:{port}"], check=False)
        public_url = _tailscale_public_url(port) or public_url
        host = "127.0.0.1"  # Tailscale fronts loopback with HTTPS

    config = _resolve_config(data)
    if k_anon is not None:
        config = replace(config, k_anon_floor=k_anon)
    data_dir = Path(data).expanduser() if data else _DEFAULT_DATA_DIR
    application = create_app(
        config, ServerStore.open(config.db_url), make_object_store(config), make_provider(config)
    )
    url = public_url.rstrip("/") or f"http://127.0.0.1:{port}"
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    typer.echo(f"Manthana server (SQLite + in-memory unless env-set) → binding {host}:{port}")
    if not loopback and not url.startswith("https"):
        typer.echo("  ⚠ WARNING: binding to a NON-loopback address without HTTPS in front.")
        typer.echo("    Team tokens are bearer credentials — they would travel in PLAINTEXT.")
        typer.echo("    Put TLS ahead of it (Caddy or --tailscale) — see docs/deploy.md.")
    typer.echo(f"  data dir:    {data_dir}")
    typer.echo(f"  admin token: {config.admin_token}")
    typer.echo(f"  console:     {url}/ui   (sign in with the admin token)")
    if config.k_anon_floor < 4:
        typer.echo(
            f"  ⚠ k-anon {config.k_anon_floor} < 4 — cross-engineer features need >=4 contributors"
        )
    typer.echo(f"  next → manthana-server enroll acme platform --open --server-url {url}")
    uvicorn.run(application, host=host, port=port)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="bind address; 0.0.0.0 to serve other machines"),
    port: int = 8000,
    public_url: str = typer.Option("", help="the https URL engineers use (when behind TLS)"),
    k_anon: int = typer.Option(-1, "--k-anon", help="k-anonymity floor (default: env or 4)"),
    data: str = "",
    tailscale: bool = typer.Option(
        False, "--tailscale", help="expose over Tailscale (automatic HTTPS)"
    ),
) -> None:
    """Run the org server (zero-config for a pilot: auto-generates + persists secrets when the
    MANTHANA_SERVER_* env vars aren't set; honours them in production). --tailscale exposes it on
    your tailnet with automatic HTTPS. SQLite + in-memory by default — no Docker/Postgres."""
    _run_server(
        host=host, port=port, public_url=public_url,
        k_anon=(k_anon if k_anon >= 1 else None), data=data, tailscale=tailscale,
    )


@app.command()
def create_org(org_id: str, name: str) -> None:
    """Create an org."""
    config = ServerConfig.from_env()
    ServerStore.open(config.db_url).create_org(org_id, name)
    typer.echo(f"created org {org_id}")


@app.command()
def create_team(team_id: str, org_id: str, name: str) -> None:
    """Create a team within an org."""
    config = ServerConfig.from_env()
    ServerStore.open(config.db_url).create_team(team_id, org_id, name)
    typer.echo(f"created team {team_id}")


@app.command()
def token(org_id: str, team_id: str, actor: str) -> None:
    """Mint a team-scoped agent token."""
    config = ServerConfig.from_env()
    typer.echo(issue_team_token(config.jwt_secret, org_id=org_id, team_id=team_id, actor=actor))


@app.command()
def onboard(org_id: str, org_name: str, team_id: str, team_name: str, actor: str) -> None:
    """Provision one engineer in a single step: ensure the org + team exist
    (idempotent) and mint their agent token. Hand the printed token to the
    employee's `manthana login`."""
    config = ServerConfig.from_env()
    store = ServerStore.open(config.db_url)
    store.create_org(org_id, org_name)
    store.create_team(team_id, org_id, team_name)
    tok = issue_team_token(config.jwt_secret, org_id=org_id, team_id=team_id, actor=actor)
    typer.echo(f"provisioned org={org_id} team={team_id} actor={actor}")
    typer.echo(tok)


@app.command()
def router_analysis(org_id: str) -> None:
    """Estimate cost savings from routing low-risk sessions to cheaper model tiers."""
    from .analyzer import analyze_counterfactual_costs

    config = ServerConfig.from_env()
    report = analyze_counterfactual_costs(ServerStore.open(config.db_url), org_id)
    skip = ""
    if report.skipped_no_tokens:
        skip = f" (skipped {report.skipped_no_tokens} pre-breakdown)"
    typer.echo(f"org={report.org_id}  priced={report.priced}/{report.sessions} sessions{skip}")
    typer.echo(
        f"current ~${report.current_usd:.2f} → projected ~${report.projected_usd:.2f}  "
        f"= save ${report.savings_usd:.2f} ({report.savings_pct:.1f}%)  "
        f"downgrades: {report.by_target or '—'}"
    )
    for r in report.rows[:10]:
        if r.savings_usd > 0:
            typer.echo(
                f"  {r.tier}→{r.target_tier}  save ${r.savings_usd:.2f}  [{r.project}] {r.id}"
            )


@app.command()
def digest(org_id: str, since: str = "", until: str = "") -> None:
    """Print the founder weekly digest for an org (last 7 days by default)."""
    from .digest import build_weekly_digest
    from .llm import make_provider

    config = ServerConfig.from_env()
    d = build_weekly_digest(
        ServerStore.open(config.db_url), config, org_id=org_id,
        provider=make_provider(config), since=since or None, until=until or None,
    )
    typer.echo(f"# Weekly digest — {d.org_id} ({d.since} → {d.until})")
    for s in d.sections:
        typer.echo(f"\n## {s.title}\n{s.narrative}\nsources: {', '.join(s.citations)}")
    if d.omitted:
        typer.echo(f"\n(omitted (k-anon / no data): {', '.join(d.omitted)})")
    if not d.sections:
        typer.echo("\n(no sections cleared the k-anonymity floor for this window)")


@app.command()
def quickstart(
    port: int = 8000,
    host: str = typer.Option("127.0.0.1", help="bind address; 0.0.0.0 to serve other machines"),
    public_url: str = typer.Option("", help="the https URL engineers use (when behind TLS)"),
    k_anon: int = K_ANON_FLOOR_DEFAULT,
    data: str = "",
    tailscale: bool = typer.Option(
        False, "--tailscale", help="expose over Tailscale (automatic HTTPS)"
    ),
) -> None:
    """Alias for `serve` (zero-infra pilot: auto-secrets, SQLite + in-memory)."""
    _run_server(
        host=host, port=port, public_url=public_url, k_anon=k_anon, data=data, tailscale=tailscale
    )


@app.command()
def enroll(
    org_id: str,
    team_id: str,
    server_url: str = typer.Option(..., "--server-url", help="public URL engineers redeem at"),
    emails: str = typer.Option("", help="file of engineer emails (one per line) → bound invites"),
    open_invite: bool = typer.Option(False, "--open", help="one shared multi-use team invite"),
    org_name: str = "",
    team_name: str = "",
    expires_days: int = 14,
    data: str = "",
) -> None:
    """Provision a team + emit `manthana setup <blob>` one-liners. `--open` = one shared invite
    to drop in Slack; `--emails <file>` = a single-use invite per engineer (identity bound)."""
    config = _resolve_config(data)
    store = ServerStore.open(config.db_url)
    store.create_org(org_id, org_name or org_id)
    store.create_team(team_id, org_id, team_name or team_id)
    exp = datetime.now(UTC) + timedelta(days=expires_days)
    if open_invite:
        code = secrets.token_urlsafe(8)
        store.create_invite(code, org_id=org_id, team_id=team_id, uses=10_000, expires_at=exp)
        typer.echo(f"open team invite (share in Slack; expires in {expires_days}d):")
        typer.echo(f"  manthana setup {encode_invite(server_url, code)}")
    elif emails:
        actors = [
            ln.strip()
            for ln in Path(emails).expanduser().read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        for actor in actors:
            store.upsert_actor(actor, org_id, team_id)
            code = secrets.token_urlsafe(8)
            store.create_invite(
                code, org_id=org_id, team_id=team_id, actor=actor, uses=1, expires_at=exp
            )
            typer.echo(f"{actor} → manthana setup {encode_invite(server_url, code)}")
        typer.echo(f"\nprovisioned {len(actors)} engineer(s); send each their line")
    else:
        typer.echo("pass --open (one shared invite) OR --emails <file> (one per engineer)")
        raise typer.Exit(code=1)


def _onboard_org_via_api(
    client,  # httpx.Client-compatible (base_url set); injectable for tests
    *,
    org_id: str,
    org_name: str,
    server_url: str,
    admin_token: str,
    teams: list[str],
    emails_path: str,
    open_invite: bool,
    quota_usd: float,
    expires_days: int,
) -> str:
    """Provision a customer org over the ADMIN HTTP API and return the
    paste-ready welcome block. HTTP (not direct-DB) on purpose: the hosted
    DB (RDS) is not reachable from the operator's laptop — the admin API is."""
    headers = {"X-Admin-Token": admin_token}

    def call(method: str, path: str, **kwargs):
        resp = client.request(method, path, headers=headers, **kwargs)
        if resp.status_code >= 300:
            raise RuntimeError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    call("POST", "/v1/admin/orgs", json={"org_id": org_id, "name": org_name})
    for t in teams:
        call("POST", "/v1/admin/teams", json={"team_id": t, "org_id": org_id, "name": t})

    expires_minutes = max(1, expires_days) * 24 * 60
    setup_lines: list[str] = []
    if open_invite:
        for t in teams:
            inv = call(
                "POST", "/v1/admin/invites",
                json={
                    "org_id": org_id, "team_id": t,
                    "expires_minutes": expires_minutes, "uses": 10_000,
                },
            )
            setup_lines.append(
                f"  [team {t}] manthana setup {encode_invite(server_url, inv['code'])}"
            )
    elif emails_path:
        actors = [
            ln.strip()
            for ln in Path(emails_path).expanduser().read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        team = teams[0]  # bound invites land on the first team
        for actor in actors:
            inv = call(
                "POST", "/v1/admin/invites",
                json={
                    "org_id": org_id, "team_id": team, "actor": actor,
                    "expires_minutes": expires_minutes, "uses": 1,
                },
            )
            setup_lines.append(
                f"  {actor} → manthana setup {encode_invite(server_url, inv['code'])}"
            )
    else:
        raise RuntimeError("pass --open (one shared invite per team) OR --emails <file>")

    founder = call("POST", "/v1/admin/founder-tokens", json={"org_id": org_id})
    if quota_usd >= 0:
        call("PUT", f"/v1/admin/orgs/{org_id}/quota", json={"monthly_cap_usd": quota_usd})
    quota_line = (
        f"${quota_usd:.2f}/month" if quota_usd >= 0 else "server default"
    )

    url = server_url.rstrip("/")
    return (
        f"══ Welcome to Manthana — {org_name} ══\n"
        f"Server: {url}\n"
        "\n"
        f"Engineers — each runs ONE command (invites expire in {expires_days}d):\n"
        + "\n".join(setup_lines)
        + "\n\n"
        f"Founder console: {url}/ui\n"
        f"  sign-in token (founder-only, keep private): {founder['token']}\n"
        "\n"
        f"AI budget: {quota_line}\n"
    )


@app.command()
def onboard_org(
    org_id: str,
    org_name: str,
    server_url: str = typer.Option(..., "--server-url", help="the hosted server's https URL"),
    admin_token: str = typer.Option(
        "", "--admin-token", envvar="MANTHANA_SERVER_ADMIN_TOKEN",
        help="operator admin token (or env MANTHANA_SERVER_ADMIN_TOKEN)",
    ),
    teams: str = typer.Option("core", "--teams", help="comma-separated team id(s)"),
    emails: str = typer.Option("", help="file of engineer emails (one per line) → bound invites"),
    open_invite: bool = typer.Option(False, "--open", help="one shared invite per team"),
    quota_usd: float = typer.Option(
        HOSTED_MONTHLY_CAP_USD,
        "--quota-usd",
        help="monthly AI budget for this org (USD); 0 = unlimited, -1 = server default",
    ),
    expires_days: int = 14,
) -> None:
    """Onboard a customer org onto the HOSTED server in one command: org + team(s) +
    engineer invites + an org-scoped founder console token + AI budget — then prints a
    paste-ready welcome block to email the startup. Works over the admin HTTP API, so
    it runs from anywhere the server URL is reachable (no DB access needed).

    An individual engineer is just an org of one: onboard-org jane jane --emails <file>."""
    import httpx

    if not admin_token:
        typer.echo("✗ no admin token (pass --admin-token or set MANTHANA_SERVER_ADMIN_TOKEN)")
        raise typer.Exit(code=1)
    with httpx.Client(base_url=server_url.rstrip("/"), timeout=30.0) as client:
        try:
            block = _onboard_org_via_api(
                client,
                org_id=org_id, org_name=org_name, server_url=server_url,
                admin_token=admin_token,
                teams=[t.strip() for t in teams.split(",") if t.strip()] or ["core"],
                emails_path=emails,
                open_invite=open_invite, quota_usd=quota_usd, expires_days=expires_days,
            )
        except RuntimeError as exc:
            typer.echo(f"✗ {exc}")
            raise typer.Exit(code=1) from exc
    typer.echo(block)


@app.command()
def usage(
    org_id: str,
    server_url: str = typer.Option(..., "--server-url"),
    admin_token: str = typer.Option("", "--admin-token", envvar="MANTHANA_SERVER_ADMIN_TOKEN"),
) -> None:
    """Show an org's month-by-month server-side AI usage and its budget cap."""
    import httpx

    with httpx.Client(base_url=server_url.rstrip("/"), timeout=30.0) as client:
        resp = client.get(
            "/v1/admin/usage", params={"org_id": org_id},
            headers={"X-Admin-Token": admin_token},
        )
        if resp.status_code >= 300:
            typer.echo(f"✗ {resp.status_code}: {resp.text[:300]}")
            raise typer.Exit(code=1)
        data = resp.json()
    cap = data["monthly_cap_usd"]
    src = "org override" if data["cap_is_override"] else "server default"
    typer.echo(f"org={org_id}  cap=" + (f"${cap:.2f}/mo ({src})" if cap > 0 else "unlimited"))
    for m in data["months"]:
        typer.echo(
            f"  {m['month']}  ${m['est_cost_usd']:.4f}  ({m['calls']} calls, "
            f"{m['input_tokens']:,} in / {m['output_tokens']:,} out tokens)"
        )
    if not data["months"]:
        typer.echo("  (no usage recorded)")


@app.command()
def set_quota(
    org_id: str,
    monthly_cap_usd: float = typer.Argument(..., help="USD per month; 0 = unlimited"),
    server_url: str = typer.Option(..., "--server-url"),
    admin_token: str = typer.Option("", "--admin-token", envvar="MANTHANA_SERVER_ADMIN_TOKEN"),
) -> None:
    """Set an org's monthly AI budget."""
    import httpx

    with httpx.Client(base_url=server_url.rstrip("/"), timeout=30.0) as client:
        resp = client.put(
            f"/v1/admin/orgs/{org_id}/quota",
            json={"monthly_cap_usd": monthly_cap_usd},
            headers={"X-Admin-Token": admin_token},
        )
        if resp.status_code >= 300:
            typer.echo(f"✗ {resp.status_code}: {resp.text[:300]}")
            raise typer.Exit(code=1)
    typer.echo(f"org={org_id} monthly cap → ${monthly_cap_usd:.2f}")


@app.command()
def invites(org_id: str, data: str = "") -> None:
    """List an org's onboarding invites and their state."""
    config = _resolve_config(data)
    rows = ServerStore.open(config.db_url).list_invites(org_id)
    if not rows:
        typer.echo("no invites")
        return
    for inv in rows:
        state = "redeemed" if inv.redeemed_at else f"{inv.uses_left} use(s) left"
        typer.echo(
            f"{inv.code}  team={inv.team_id}  who={inv.actor or '(open)'}  "
            f"{state}  expires={inv.expires_at}"
        )


@app.command()
def init(directory: str = typer.Argument(".", help="where to write the deploy files")) -> None:
    """Write the deploy templates (Caddyfile, docker-compose{,.tls}.yml, .env.example) into a
    directory — so you never have to clone the repo to stand up a Docker/TLS deployment."""
    from .deploy_templates import TEMPLATES

    target = Path(directory).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    for name, content in TEMPLATES.items():
        (target / name).write_text(content)
        typer.echo(f"  wrote {target / name}")
    typer.echo(
        "next: fill `.env` (generate secrets — see its header), then `docker compose up -d`; "
        "or skip Docker with `manthana-server serve --tailscale`."
    )


@app.command()
def doctor(data: str = "") -> None:
    """Health check for the org server — exits non-zero if a critical check fails."""
    import os as _os

    from .storage import make_object_store

    failed = False

    def check(ok: bool, label: str, detail: str = "", *, critical: bool = True) -> None:
        nonlocal failed
        mark = "✓" if ok else ("✗" if critical else "•")
        typer.echo(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
        if not ok and critical:
            failed = True

    typer.echo("Manthana server doctor")
    try:
        config = _resolve_config(data)  # constructs → validates secrets (refuses dev defaults)
    except Exception as exc:  # noqa: BLE001 - surface a bad config as a failed check, not a crash
        typer.echo(f"  ✗ config invalid: {exc}")
        raise typer.Exit(code=1) from exc
    check(True, "secrets configured", f"db={config.db_url}")

    store = ServerStore.open(config.db_url)
    check(store.ping(), "database reachable")
    try:
        make_object_store(config)
        os_ok = True
    except Exception:  # noqa: BLE001 - object-store init failure is a (non-fatal) health signal
        os_ok = False
    check(os_ok, "object store", config.object_store, critical=False)
    if config.llm_provider == "anthropic":
        check(
            bool(_os.environ.get("ANTHROPIC_API_KEY")),
            "LLM: anthropic key present", critical=False,
        )
    elif config.llm_provider == "claude_cli":
        # The failure mode this catches is nasty and silent: without the binary the
        # provider degrades to the mock, so every pass "succeeds" while writing
        # nothing. Say so here rather than leaving it to a server log nobody reads.
        import shutil as _shutil

        found = _shutil.which(config.claude_cli_binary)
        check(
            bool(found),
            f"LLM: claude_cli ({config.claude_cli_binary})",
            found or "not on PATH — every pass would silently degrade to the mock; "
            "this mode needs the CLI installed and logged in as the user running "
            "the server, so it cannot work inside the container images",
            critical=False,
        )
    else:
        check(True, "LLM: mock (set MANTHANA_SERVER_LLM=anthropic, or claude_cli to use "
                    "your own logged-in Claude CLI)", critical=False)
    check(config.k_anon_floor >= 4, f"k-anon floor = {config.k_anon_floor}",
          "cross-engineer features need >=4", critical=False)
    # Hosted-deploy sanity: a Postgres DB with the in-memory object store loses
    # every raw transcript on restart — almost certainly a misconfiguration.
    if config.db_url.startswith("postgres"):
        check(config.object_store == "s3", "object store persistent",
              f"db is Postgres but object store is '{config.object_store}' — "
              "raw transcripts would vanish on restart (set MANTHANA_SERVER_OBJECT_STORE=s3)",
              critical=False)
        check(config.cookie_secure, "secure cookies",
              "set MANTHANA_SERVER_COOKIE_SECURE=1 behind TLS", critical=False)
    cap = config.llm_monthly_cap_usd
    check(True, "AI budget default",
          f"${cap:.2f}/org/month" if cap > 0 else "unlimited (set "
          "MANTHANA_SERVER_LLM_MONTHLY_CAP_USD for hosted multi-tenant)", critical=False)

    orgs = store.list_orgs()
    actors = sum(len(store.list_actors(o.id)) for o in orgs)
    comps = sum(store.count_compactions(o.id) for o in orgs)
    invs = sum(len(store.list_invites(o.id)) for o in orgs)
    typer.echo(
        f"  • {len(orgs)} org(s) · {actors} actor(s) · {comps} released compaction(s) · "
        f"{invs} invite(s)"
    )
    if failed:
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
