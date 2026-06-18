"""Manthana org server.

Self-hosted by the organization: FastAPI ingestion API, multi-tenant
Org > Team > Actor (Project as a cross-cutting tag), k-anonymity floor, action
queue, raw-transcript release to an S3-compatible store, and the founder query
(structured-filter-first → SQL → grounded narrative with citations).

LICENSING: this package is the ONLY AGPL-3.0-licensed component of Manthana
(everything else is Apache-2.0). Keeping it a separate distribution
(``manthana-server``) preserves the dual-license boundary from the spec.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from .app import build_default_app, create_app
from .config import ServerConfig
from .store import ServerStore

__all__ = ["create_app", "build_default_app", "ServerConfig", "ServerStore"]
