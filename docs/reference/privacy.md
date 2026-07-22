# Privacy & security model

Manthana reads people's work. If the guarantees are vague, the product is worse
than useless — engineers will quietly turn it off. So the guarantees are narrow,
specific, and enforced in code rather than in policy.

## The four hard invariants

**1. Personal-mode sessions never leave the laptop.**
`manthana mode <session-id> personal` (or the Personal toggle in the dashboard)
excludes a session from auto-release and from every sync path, permanently. There
is no admin override, no "resync anyway" flag, and no server-side setting that
can pull it. `manthana resync --confirm`, whose entire job is to re-upload
everything, still refuses to touch personal sessions.

**2. Nothing syncs until it is released.**
Sync only ever pushes compactions that are *released* AND *non-personal*. A
compaction auto-releases 10 minutes after it is built, which is an opt-out
window, not a silent upload: mark the session personal or hold the compaction
inside that window and it stays put. `manthana watch --no-auto-release` turns
auto-release off entirely.

**3. Free text is redacted before it leaves.**
Redaction runs on the laptop, on the way out, per turn. It strips AWS keys,
private key blocks, JWTs, GitHub tokens, `secret:`/`password:`/`api_key=` style
assignments, and a best-effort PII pass. Toggle it in `~/.manthana/manthana.toml`:

```toml
[redaction]
secrets = true
pii = true
```

Check what is active with `manthana config`.

**4. Raw transcripts stay behind an audited, founder-only drill-down.**
Released raw transcripts are uploaded (redacted) so the org can grep and cite
them, but they are not browsable. Reaching one goes through
`POST /v1/founder/drill`, which is org-scoped and writes an audit row every time
— including the ones that returned nothing. The founder cannot look without
leaving a trace, and that is the point.

## k-anonymity

Cross-engineer views — org rollups, topic clusters, skill mining, the weekly
digest — are gated on a **floor of distinct contributors**. Below the floor, a
section is withheld rather than shown thinly.

- Default floor: **4** (`MANTHANA_SERVER_K_ANON`).
- Below 4, cross-engineer features simply do not fire. `manthana-server serve`
  and `manthana-server doctor` both warn if you set it lower.
- Lowering it to 1 is meaningful only for a single-person install, where there is
  nobody to de-identify from. See [Solo use](../solo/index.md).

The reason for a floor rather than a name-blur: with three contributors, "someone
on the team spent two days fighting the auth migration" identifies a person to
anyone who knows the team. Aggregates only protect people when the aggregate is
big enough.

## `privacy_mode`: k_anon vs open

k-anonymity protects individuals from an org that has not agreed to be
individually visible. Some orgs *have* agreed — a four-person startup where the
founder is also a contributor gains nothing from de-identification and loses the
ability to help anyone.

| Mode | Founder sees | Set by |
|---|---|---|
| `k_anon` (**default**) | De-identified aggregates, floor-gated | `MANTHANA_SERVER_PRIVACY_MODE=k_anon` |
| `open` | Named, per-individual results | `MANTHANA_SERVER_PRIVACY_MODE=open`, or per-org via `PUT /v1/admin/orgs/{org_id}/privacy` |

Two things stay true in `open` mode: personal sessions still never sync, and
every named query is still audited and flagged as individual in the audit trail.
`open` widens what a founder may ask; it does not widen what was collected.

Prefer the per-org override to the server-wide default on a multi-tenant server —
one consenting tenant should not change the posture for everyone else.

## Credentials

| Credential | Held by | Scope | Lifetime |
|---|---|---|---|
| Invite code (`mia_…`) | engineer, briefly | redeems for a team token | 14 days default, single-use when identity-bound |
| Team token (JWT) | engineer's `manthana.toml` (`0600`) | push to one org + team | 365 days |
| Founder token | one founder | that org's console, query, digest | set at mint time |
| Engineer token | one named engineer | that org's **wiki only** — read + teach | set at mint time |
| Admin token | the operator | every org, every admin endpoint | until rotated |

All of these are **bearer credentials** — whoever holds one is trusted. That is
why the server refuses to start with the shipped dev secrets, warns loudly when
you bind a non-loopback address without HTTPS, and why the invite flow exists at
all: an invite code is worthless to anyone who is not redeeming it right now,
which is why invites go in Slack and tokens never do.

## What is audited

| Action | Endpoint | Where to read it |
|---|---|---|
| Every founder query and drill-down | `GET /v1/admin/audit?org_id=…` (admin), `GET /v1/founder/audit?org_id=…` (founder) | "Recent founder queries" panel in `/ui` |
| Every purge, including dry runs | `GET /v1/admin/purge-audit?org_id=…` | — |
| Local agent actions (tagging, loop warnings, prior-work surfacing) | local store | `manthana dashboard` → Actions |

## Deliberate restrictions, and why

- **The server does no LLM work by default.** Enrichment, consolidation, and
  project overviews are all off until an operator sets a flag. An operator should
  choose to spend money and to send text to a model provider; they should never
  discover after the fact that a background loop has been doing it.
- **`manthana purge` and `POST /v1/admin/purge` are dry-run by default and refuse
  an unfiltered request.** Deleting everything must take more than one command.
- **The wiki client must be same-origin with the server.** The session cookie is
  `httponly` and scoped `path=/ui`; a split-hostname deployment cannot
  authenticate, and no CORS setting can fix it. Details in
  [self-hosting/web-client.md](../self-hosting/web-client.md).

## Next

- [How Manthana works](architecture.md)
- [Founders: privacy posture & budgets](../founders/privacy-and-budgets.md)
- [Engineers: what happens to your data](../engineers/your-data.md)
