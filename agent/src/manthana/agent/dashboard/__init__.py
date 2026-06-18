"""Local dashboard for the Manthana agent (FastAPI + HTMX).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
