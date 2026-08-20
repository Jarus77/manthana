#!/usr/bin/env bash
# Run the whole product locally against a seeded throwaway wiki, to look at it.
#
# Exists because a preview started from an agent turn gets reaped the moment the
# turn ends. Started from your own shell, it stays up until you Ctrl-C it.
#
# Seeds its own SQLite database on first run — one project with a real article,
# plus a few others — so the pages have something to render. Nothing here touches
# the production server or the repo's own manthana-server.db.
#
#   ./scripts/preview_wiki.sh          then open http://127.0.0.1:3010
#   sign in with the token: devadmin
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${TMPDIR:-/tmp}/manthana-preview.db"
API_PORT=8010
WEB_PORT=3010

# The admin token is a throwaway for a local database with fake data. It is not a
# credential in any meaningful sense, which is why it can live in a script.
export MANTHANA_SERVER_DB_URL="sqlite:///${DB}"
export MANTHANA_SERVER_ADMIN_TOKEN=devadmin
export MANTHANA_SERVER_JWT_SECRET=dddddddddddddddddddddddddddddddddddddddd
export MANTHANA_SERVER_K_ANON=1

cd "$ROOT"

if [ ! -f "$DB" ]; then
  echo "seeding $DB …"
  uv run --package manthana-server python - <<'PY'
import os
from datetime import UTC, datetime, timedelta
from manthana.schemas import (EngineeringCompaction, KnowledgeNote, NoteEntities,
                              NoteKind, NoteSource, NoteStatus, Outcome, Surface)
from manthana.server import ServerStore

NOW = datetime.now(UTC)
store = ServerStore.open(os.environ["MANTHANA_SERVER_DB_URL"])
store.create_org("actioneer", "Actioneer")
store.create_team("actioneer-core", "actioneer", "Core")

def comp(cid, project, intent, actor, days):
    at = NOW - timedelta(days=days)
    return EngineeringCompaction(
        id=cid, session_id=f"s-{cid}", actor=actor, surface=Surface.claude_code,
        project=project, started_at=at, ended_at=at, duration_seconds=3600.0,
        task_intent=intent, approach="Wired the SDK, ran the eval, recorded the numbers.",
        outcome=Outcome.success, released=True, source="full",
        files_touched=["src/analyze.py", "src/schema.py"], est_cost_usd=5.38,
    )

rows = [
    ("c1", "pilot-video-ey-inprime",
     "Evaluate Gemini 2.5 Pro's capability to perform detailed video analysis for three "
     "financial underwriting tasks", "navyansh@actioneer.com", 0),
    ("c2", "voice", "Monitor and validate live REST-STT phone call performance",
     "jarus@actioneer.com", 1),
    ("c3", "bird-sql", "Sweep the ranker over the dev split", "navyansh@actioneer.com", 2),
    ("c4", "baby-sentinel", "Wire the eval harness to the new scorer", "suraj@actioneer.com", 3),
    ("c5", "hdfc-pilot", "Reconcile the statement parser against the golden set",
     "jarus@actioneer.com", 4),
    ("c6", "Downloads", "Triage the inbox script", "navyansh@actioneer.com", 40),
]
for cid, project, intent, actor, days in rows:
    store.upsert_actor(actor, org_id="actioneer", team_id="actioneer-core",
                       display_name=actor.split("@")[0])
    store.ingest_compaction(comp(cid, project, intent, actor, days),
                            org_id="actioneer", team_id="actioneer-core")

store.upsert_note(KnowledgeNote(
    id="kn-ov-1", org_id="actioneer", kind=NoteKind.project_overview,
    title="pilot-video-ey-inprime",
    body=("## What this is\n\nA pilot project evaluating Gemini 2.5 Pro's capability to "
          "perform detailed video analysis for three financial underwriting tasks (business "
          "premises, residential premises, post-disbursement asset verification) using real "
          "video content.\n\n## Current state\n\n"
          "- Integrated the google-genai SDK with Vertex AI authentication.\n"
          "- Tested end-to-end analysis on a 5:26 convenience-store video.\n"
          "- The model returned structured JSON with inventory valuations.\n\n"
          "## Open questions / next steps\n\n"
          "- Does fidelity hold on lower-resolution field footage?\n"),
    scope="project:pilot-video-ey-inprime",
    entities=NoteEntities(projects=["pilot-video-ey-inprime"]),
    evidence=["c1"], actors=["navyansh@actioneer.com"], source=NoteSource.ai,
    status=NoteStatus.candidate, confidence=0.5, version=1,
    change_summary="article created", created_at=NOW, updated_at=NOW,
))
print("seeded")
PY
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

uv run --package manthana-server uvicorn manthana.server.app:build_default_app \
  --factory --host 127.0.0.1 --port "$API_PORT" >/dev/null 2>&1 &

( cd web && MANTHANA_API_ORIGIN="http://127.0.0.1:${API_PORT}" \
    npx next dev -p "$WEB_PORT" >/dev/null 2>&1 ) &

until curl -sf "http://127.0.0.1:${API_PORT}/healthz" >/dev/null 2>&1 \
   && curl -sf "http://127.0.0.1:${WEB_PORT}/login"   >/dev/null 2>&1; do sleep 2; done

cat <<EOF

  ready → http://127.0.0.1:${WEB_PORT}
  token → devadmin

  /                    marketing, as a Wikipedia portal
  /console/cost        the charts
  /console/team        forms and copy blocks
  /projects/pilot-video-ey-inprime    an article with an infobox

  Ctrl-C to stop.
EOF

wait
