"""Deploy-facing server bits: /readyz probe + S3/MinIO endpoint config.

Hermetic — no Docker, no boto3 (the S3 store is exercised with an injected fake
client), no network.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.llm import MockProvider
from manthana.server.storage import InMemoryObjectStore, S3ObjectStore, make_object_store


def _client() -> tuple[TestClient, ServerStore]:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    client = TestClient(create_app(config, store, InMemoryObjectStore(), MockProvider("{}")))
    return client, store


# ── readiness / liveness ────────────────────────────────────────────────────
def test_readyz_ok_when_db_reachable() -> None:
    client, _ = _client()
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_healthz_liveness() -> None:
    client, _ = _client()
    # Liveness is `status: ok`; the version fields ride along for the agent's update
    # check, so assert the contract rather than the exact shape of the body.
    assert client.get("/healthz").json()["status"] == "ok"


def test_store_ping_true_on_live_engine() -> None:
    _, store = _client()
    assert store.ping() is True


# ── S3 / MinIO endpoint config ──────────────────────────────────────────────
def test_config_reads_s3_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANTHANA_SERVER_OBJECT_STORE", "s3")
    monkeypatch.setenv("MANTHANA_SERVER_S3_BUCKET", "manthana-raw")
    monkeypatch.setenv("MANTHANA_SERVER_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("MANTHANA_SERVER_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("MANTHANA_SERVER_S3_SECRET_KEY", "sk")
    monkeypatch.setenv("MANTHANA_SERVER_ADMIN_TOKEN", "adm")  # non-default (rejection guard)
    monkeypatch.setenv("MANTHANA_SERVER_JWT_SECRET", "x" * 40)
    cfg = ServerConfig.from_env()
    assert cfg.object_store == "s3"
    assert cfg.s3_endpoint_url == "http://minio:9000"
    assert cfg.s3_access_key == "ak" and cfg.s3_secret_key == "sk"


class _FakeS3:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803 - boto3 kwargs
        self.store[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 - boto3 kwargs
        data = self.store[(Bucket, Key)]

        class _Body:
            def read(self) -> bytes:
                return data

        return {"Body": _Body()}


def test_s3_object_store_roundtrips_with_injected_client() -> None:
    s3 = S3ObjectStore("bucket", client=_FakeS3())
    s3.put("raw/k", b"payload")
    assert s3.get("raw/k") == b"payload"
    assert s3.get("missing") is None


def test_make_object_store_memory_by_default() -> None:
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    assert isinstance(make_object_store(cfg), InMemoryObjectStore)


def test_make_object_store_s3_requires_bucket() -> None:
    cfg = ServerConfig(jwt_secret="x" * 40, admin_token="adm", object_store="s3")
    with pytest.raises(ValueError):
        make_object_store(cfg)  # raises before any boto3 import


# ── founder-query audit log ─────────────────────────────────────────────────
def test_founder_queries_are_audited() -> None:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    store.create_org("o1", "Acme")
    client = TestClient(create_app(config, store, InMemoryObjectStore(), MockProvider("{}")))
    admin = {"X-Admin-Token": "adm"}

    client.post("/v1/founder/query", json={"org_id": "o1", "query": "what shipped?"}, headers=admin)
    client.post("/v1/founder/query", json={"org_id": "o1", "query": "any blockers?"}, headers=admin)

    resp = client.get("/v1/admin/audit", params={"org_id": "o1"}, headers=admin)
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 2
    assert {e["query"] for e in entries} == {"what shipped?", "any blockers?"}
    assert all(e["insufficient"] for e in entries)  # no data seeded -> withheld, still audited


def test_audit_requires_admin_token() -> None:
    config = ServerConfig(jwt_secret="x" * 40, admin_token="adm")
    store = ServerStore.open("sqlite://")
    client = TestClient(create_app(config, store, InMemoryObjectStore(), MockProvider("{}")))
    assert client.get("/v1/admin/audit", params={"org_id": "o1"}).status_code == 401


# ── published-image references stay consistent ───────────────────────────────
#
# Two independent drifts happened here, both silent and both only discoverable by
# trying to deploy: the compose files and the k8s manifest named an image owner
# that never published anything, and the k8s tag sat at 0.2.0 through four
# releases. Nothing failed — `docker compose pull` just 404s at the worst moment.
# These pin the invariants: one image path everywhere, and the pinned tags are the
# version this tree actually is.
_GHCR = re.compile(r"ghcr\.io/[\w.-]+/manthana-server")
_GHCR_TAGGED = re.compile(r"ghcr\.io/[\w.-]+/manthana-server:([\w.]+)")

_IMAGE_FILES = (
    "docker-compose.prod.yml",
    "deploy/k8s/deployment.yaml",
    "server/src/manthana/server/deploy_templates.py",
)


def _uncommented(text: str) -> str:
    """Drop comment lines before matching.

    Comments legitimately show alternative registries (the `MANTHANA_IMAGE=ghcr.io/
    your-org/…` fork example), and a doc example disagreeing with the real value is
    not the drift this guards against.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".github").is_dir():
            return parent
    raise AssertionError("repo root not found")


def test_every_deploy_file_names_the_same_published_image() -> None:
    root = _repo_root()
    found = {
        name: set(_GHCR.findall(_uncommented((root / name).read_text())))
        for name in _IMAGE_FILES
        if (root / name).exists()
    }
    assert found, "no deploy files found — did paths move?"
    paths = {p for names in found.values() for p in names}
    assert len(paths) == 1, f"deploy files disagree on the image path: {found}"

    # …and it must be what the publishing workflow pushes. The workflow derives the
    # owner from the repository, so we can only assert the repository segment here —
    # but that is the half that was never wrong. The owner is asserted by equality
    # across files above, which is what actually broke.
    workflow = (root / ".github/workflows/publish-image.yml").read_text()
    assert "manthana-server" in workflow
    assert next(iter(paths)).endswith("/manthana-server")


def test_pinned_image_tags_match_the_packaged_version() -> None:
    """The version is read from pyproject, NOT importlib.metadata.

    The installed distribution's metadata is a snapshot of the last `uv sync`, so
    during a version bump it still reports the OLD number and this test fails on
    correctly-bumped files — which is precisely when you least want a spurious
    failure. The file on disk is what "this tree is".
    """
    root = _repo_root()
    text = (root / "server/pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "server/pyproject.toml has no version"
    expected = match.group(1)

    for name in _IMAGE_FILES:
        path = root / name
        if not path.exists():
            continue
        for tag in _GHCR_TAGGED.findall(_uncommented(path.read_text())):
            assert tag == expected, f"{name} pins :{tag}, but this tree is {expected}"
