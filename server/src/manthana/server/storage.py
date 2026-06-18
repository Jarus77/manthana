"""Object store abstraction for raw transcripts released on explicit approval.

Dev/tests use ``InMemoryObjectStore``; production uses S3-compatible storage
(MinIO self-hosted, or AWS S3 / GCS / R2) via ``S3ObjectStore`` (boto3 — an
optional dependency).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import ServerConfig


@runtime_checkable
class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store bytes under ``key``; return the key."""
        ...

    def get(self, key: str) -> bytes | None:
        """Fetch bytes for ``key`` (None if absent)."""
        ...


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> str:
        self._objects[key] = data
        return key

    def get(self, key: str) -> bytes | None:
        return self._objects.get(key)


class S3ObjectStore:
    """S3-compatible store (boto3). Bucket must already exist."""

    def __init__(self, bucket: str, client: object | None = None) -> None:
        self.bucket = bucket
        if client is None:
            import boto3  # type: ignore[import-untyped]

            client = boto3.client("s3")
        self._client = client

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)  # type: ignore[attr-defined]
        return key

    def get(self, key: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - missing key / client error
            return None
        return resp["Body"].read()


def make_object_store(config: ServerConfig) -> ObjectStore:
    if config.object_store == "s3":
        if not config.s3_bucket:
            raise ValueError("MANTHANA_SERVER_S3_BUCKET required for object_store=s3")
        return S3ObjectStore(config.s3_bucket)
    return InMemoryObjectStore()


__all__ = ["ObjectStore", "InMemoryObjectStore", "S3ObjectStore", "make_object_store"]
