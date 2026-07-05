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
from .config import K_ANON_FLOOR_DEFAULT, ServerConfig, persisted_secrets
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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the API (config from MANTHANA_SERVER_* env vars)."""
    import uvicorn

    from .app import build_default_app

    uvicorn.run(build_default_app(), host=host, port=port)


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
def quickstart(port: int = 8000, k_anon: int = K_ANON_FLOOR_DEFAULT, data: str = "") -> None:
    """Zero-infra pilot server: SQLite + in-memory + auto-generated persisted secrets — no
    Docker/Postgres/MinIO. Prints the admin token + console URL, then serves."""
    import uvicorn

    from .app import create_app
    from .llm import make_provider
    from .storage import make_object_store

    config = replace(_resolve_config(data), k_anon_floor=k_anon)
    data_dir = Path(data).expanduser() if data else _DEFAULT_DATA_DIR
    application = create_app(
        config, ServerStore.open(config.db_url), make_object_store(config), make_provider(config)
    )
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Manthana server (zero-infra: SQLite + in-memory) → {url}")
    typer.echo(f"  data dir:    {data_dir}")
    typer.echo(f"  admin token: {config.admin_token}")
    typer.echo(f"  console:     {url}/ui   (sign in with the admin token)")
    if k_anon < 4:
        typer.echo(f"  ⚠ k-anon {k_anon} < 4 — cross-engineer features need >=4 contributors")
    typer.echo(f"  next → manthana-server enroll acme platform --open --server-url {url}")
    uvicorn.run(application, host="127.0.0.1", port=port)


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
