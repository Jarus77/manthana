# Operating the server

Day-to-day work once the team is on. If you also *deploy* the server, read
[Self-hosting](../self-hosting/index.md) as well.

## The weekly ten minutes

```bash
manthana-server doctor                                          # is it healthy?
manthana-server usage acme --server-url https://manthana.acme.com   # what did it spend?
manthana-server digest acme                                     # what happened?
```

`doctor` checks secrets, database, object store, the LLM provider and key, the
k-anon floor, and the budget default — and on a Postgres deployment it flags the
two misconfigurations that silently lose things: an in-memory object store (every
raw transcript vanishes on restart) and non-secure cookies behind TLS (logins
won't stick). It exits non-zero on a critical failure, so it works in a cron job.

## Adding someone later

```bash
manthana-server enroll acme platform --emails newhire.txt \
  --server-url https://manthana.acme.com
manthana-server invites acme      # confirm they redeemed it
```

Then mint them a wiki login (console → **Mint engineer token**, or
`POST /v1/engineer-tokens`).

## The enrichment backlog

Enrichment is the pass everything else depends on, so this is the number to
watch.

```bash
curl -H "X-Admin-Token: $ADMIN" \
  "https://manthana.acme.com/v1/admin/enrichment?org_id=acme"
```

You get `enabled`, the model, the `pending` count, and a list of **stuck**
digests with their state, attempt count, and reason.

A digest with neither a native summary nor an uploaded raw transcript **waits**
rather than burning a model call. After `ENRICH_MAX_ATTEMPTS` (5) or
`ENRICH_MAX_AGE_DAYS` (7) it is abandoned and never retried — a digest whose
input is never coming must not retry forever.

**Two levers:**

```bash
# Run one bounded pass right now instead of waiting for the 5-minute interval
curl -X POST -H "X-Admin-Token: $ADMIN" \
  "https://manthana.acme.com/v1/admin/enrichment/run?org_id=acme"

# Un-abandon digests whose raw HAS since arrived
curl -X POST -H "X-Admin-Token: $ADMIN" \
  "https://manthana.acme.com/v1/admin/enrichment/retry?org_id=acme&limit=200"
```

`retry` exists for a specific situation: digests stranded when their raw upload
was merely *late*. By default it only resets digests that could actually enrich
now — ones with an uploaded raw or a native summary — so the queue isn't refilled
with the same nothing that abandoned them. `include_without_input=true` resets
the rest; `limit` caps at 500. It is bounded on purpose: un-abandoning re-enters
the metered queue, and a big backlog should drain in deliberate slices against
the org's cap.

Consolidation has the same pair at `/v1/admin/consolidation` and
`/v1/admin/consolidation/run`.

## Audits

```bash
curl -H "X-Admin-Token: $ADMIN" "https://manthana.acme.com/v1/admin/audit?org_id=acme"
```

Every founder query and every raw drill-down: who asked what, whether it was
answered or withheld, how many citations, and whether it was an individual
(named) lookup. Founders can read their own org's trail at
`GET /v1/founder/audit?org_id=…`, and the console shows it as a panel.

If your team asks "can I see what you looked at?", the answer should be yes.
Show them the panel.

## Purging data

Deletion is admin-only, always audited, dry-run by default, and refuses an
unfiltered request. That combination is deliberate: wiping an org's history
should take more than one command and should leave a record.

```bash
# Dry run — returns the count plus a sample of what WOULD be deleted
curl -X POST -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' \
  https://manthana.acme.com/v1/admin/purge \
  -d '{"org_id":"acme","contains":"legacy-billing"}'

# Commit
curl -X POST -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' \
  https://manthana.acme.com/v1/admin/purge \
  -d '{"org_id":"acme","contains":"legacy-billing","confirm":true}'
```

Filters: `source` (`pending` | `full` | `claude_summary`), `contains`,
`self_generated`, `structural_junk`. A confirmed purge deletes the rows, their
raw blobs, and their cached embedding vectors together. History, dry runs
included: `GET /v1/admin/purge-audit?org_id=acme`.

An engineer purging their **own local** store uses `manthana purge`, which is a
different command with the same safety posture.

## Backups

| Deployment | What to back up |
|---|---|
| Pilot (`serve`, SQLite) | the whole `~/.manthana-server` directory — DB *and* `server-secrets.toml` |
| Docker Compose | the `pgdata` and `miniodata` volumes |
| Postgres + S3 | your normal database backups, plus the raw-transcript bucket |

**Back up the secrets file too.** `jwt_secret` signs every token you've issued;
losing it invalidates every engineer's agent and every founder login at once.

If you ever restore onto a *fresh* database, engineers' laptops will still think
they've already pushed everything. Have them run `manthana resync --confirm`
followed by `manthana sync` — see
[the engineer guide](../engineers/daily.md#recovering-after-a-server-reset).

## Rotating the admin token

Set a new `MANTHANA_SERVER_ADMIN_TOKEN` and restart. Founder and engineer tokens
are unaffected — they're signed with the JWT secret, not the admin token. Rotate
the **JWT secret** only when you intend to invalidate every issued token.

## Upgrades

See [Self-hosting → upgrading](../self-hosting/operations.md#upgrading). The short
version: agent and server versions move in lockstep, and engineers upgrade by
re-running the installer.

## Next

→ [Reading the wiki & digest](reading-the-wiki.md)
→ [Troubleshooting](../troubleshooting.md)
