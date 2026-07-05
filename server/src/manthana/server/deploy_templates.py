# ruff: noqa: E501 - embedded deploy-file content (compose/Caddy lines are intentionally long)
"""Deploy templates bundled with the ``manthana-server`` wheel.

Written out by ``manthana-server init`` so an admin never has to clone the repo to get the
Caddy / docker-compose / .env files. Pilot-oriented (SQLite + in-memory; add Postgres/MinIO
only when you graduate).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

_CADDYFILE = """\
# Automatic-HTTPS reverse proxy (Caddy) for a standalone (non-Docker) Manthana server.
# Caddy gets + auto-renews a free Let's Encrypt certificate for your domain and forwards to
# the server, so tokens never travel unencrypted.
#
# Setup: point <your-domain>'s DNS A record at this machine; open ports 80 + 443; then:
#   manthana-server serve --public-url https://<your-domain>   # server on 127.0.0.1:8000
#   caddy run --config ./Caddyfile
# Replace <your-domain> with your real domain (e.g. manthana.acme.com).

<your-domain> {
\treverse_proxy 127.0.0.1:8000
}
"""

_DOCKER_COMPOSE = """\
# Pilot Manthana server via Docker — SQLite + in-memory (no Postgres/MinIO). Fill .env first.
#   docker compose up -d
services:
  server:
    image: ghcr.io/suraj-gameramp/manthana-server:0.4.0
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      MANTHANA_SERVER_DB_URL: sqlite:////data/manthana-server.db   # persisted on the volume
    volumes:
      - manthana_data:/data

volumes:
  manthana_data:
"""

_DOCKER_COMPOSE_TLS = """\
# TLS overlay — Caddy (automatic HTTPS) in front of the pilot server.
#   MANTHANA_DOMAIN=manthana.acme.com docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
# Prereqs: your domain's DNS A record points here; ports 80 + 443 open.
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    command: caddy reverse-proxy --from ${MANTHANA_DOMAIN:?set MANTHANA_DOMAIN to your domain} --to server:8000
    volumes:
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - server

volumes:
  caddy_data:
  caddy_config:
"""

_ENV_EXAMPLE = """\
# Copy to .env and fill in. The server refuses to start with empty or dev-default secrets.
# Generate strong ones:
#   python3 -c "import secrets;print('MANTHANA_SERVER_JWT_SECRET='+secrets.token_hex(32));print('MANTHANA_SERVER_ADMIN_TOKEN='+secrets.token_hex(24))"
MANTHANA_SERVER_JWT_SECRET=
MANTHANA_SERVER_ADMIN_TOKEN=
MANTHANA_SERVER_K_ANON=4

# Optional: real, citation-grounded founder narratives (else a deterministic mock is used).
# MANTHANA_SERVER_LLM=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
"""

TEMPLATES: dict[str, str] = {
    "Caddyfile": _CADDYFILE,
    "docker-compose.yml": _DOCKER_COMPOSE,
    "docker-compose.tls.yml": _DOCKER_COMPOSE_TLS,
    ".env.example": _ENV_EXAMPLE,
}

__all__ = ["TEMPLATES"]
