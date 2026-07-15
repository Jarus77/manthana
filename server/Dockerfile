# Manthana org server image (hosted / self-hosted).
# Build from the REPO ROOT (the server's deps are workspace path-packages):
#   docker build -f server/Dockerfile -t manthana-server .
# Or via docker-compose (build.context = repo root).
#
# Multi-stage: deps + the embedding model are built in a throwaway stage so the
# runtime image carries no uv/pip caches. The bge-large embedding model is baked
# in at BUILD time — a deploy must never depend on Hugging Face being up, and a
# fresh ECS task must serve queries immediately (no multi-minute cold download).
#
# SPDX-License-Identifier: AGPL-3.0-or-later
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    HF_HOME=/app/hf-cache

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . /app

# Workspace members from the committed lockfile + production extras: Postgres
# driver, S3, real LLM provider, and semantic embeddings (CPU-only torch — the
# CUDA build would add gigabytes for GPUs a Fargate task doesn't have).
RUN uv sync --all-packages --frozen \
 && uv pip install "psycopg[binary]>=3.2" "boto3>=1.35" "anthropic>=0.40" \
 && uv pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && uv pip install "sentence-transformers>=3.0"

# Bake the embedding model into the image (downloads into HF_HOME).
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-en-v1.5')"


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/hf-cache \
    HF_HUB_OFFLINE=1

WORKDIR /app
COPY --from=builder /app /app

# Run as a non-root user (the server needs no write access to the image).
RUN useradd --create-home --uid 10001 manthana && chown -R manthana:manthana /app
USER manthana

EXPOSE 8000
# build_default_app() wires config from MANTHANA_SERVER_* env vars and creates
# tables on startup (idempotent). --proxy-headers: behind the ALB, client IPs
# come from X-Forwarded-For — without it every request would appear to come
# from the LB and the per-IP rate limits would throttle all tenants together.
CMD ["uvicorn", "manthana.server.app:build_default_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
