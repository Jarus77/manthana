"""Server configuration (from ``MANTHANA_SERVER_*`` env vars).

Dev defaults run on SQLite + an in-memory object store with insecure secrets;
production MUST override JWT secret, admin token, DB URL, and object store.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import os
from dataclasses import dataclass

K_ANON_FLOOR_DEFAULT = 4


@dataclass
class ServerConfig:
    db_url: str = "sqlite:///./manthana-server.db"
    jwt_secret: str = "dev-insecure-jwt-secret-change-me-in-production"  # noqa: S105 - dev only
    admin_token: str = "dev-admin-token"  # noqa: S105 - dev default; override in prod
    k_anon_floor: int = K_ANON_FLOOR_DEFAULT
    object_store: str = "memory"  # "memory" | "s3"
    s3_bucket: str | None = None

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
        )


__all__ = ["ServerConfig", "K_ANON_FLOOR_DEFAULT"]
