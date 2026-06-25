"""Manthana org-server CLI (``manthana-server``).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import typer

from .auth import issue_team_token
from .config import ServerConfig
from .store import ServerStore

app = typer.Typer(help="Manthana org server.", no_args_is_help=True, add_completion=False)


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
