# Privacy posture, the LLM passes, and budgets

Three decisions to make once, deliberately. Two of them cost money; all three
affect trust.

## 1. Privacy posture

The full model is in [Privacy & security model](../reference/privacy.md). The
decision you own is `privacy_mode`.

| Mode | You see | Good for |
|---|---|---|
| `k_anon` (**default**) | De-identified aggregates, withheld below 4 distinct contributors | Any org where individual visibility hasn't been explicitly agreed |
| `open` | Named, per-individual results | A small team that has agreed to it — often one where the founder is also a contributor |

```bash
# Per-org (preferred, especially on a shared server)
curl -X PUT https://manthana.acme.com/v1/admin/orgs/acme/privacy \
  -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' \
  -d '{"privacy_mode":"open"}'

# Server-wide default
MANTHANA_SERVER_PRIVACY_MODE=open
```

Two things do not change in `open` mode: personal-mode sessions still never sync,
and every named query is still written to the audit trail flagged as individual.
`open` widens what you may *ask*; it does not widen what was *collected*.

**Have the conversation before you flip it.** The switch is one line of config;
the trust it spends is not recoverable. Tell your team which mode you're in.

### The k-anonymity floor

`MANTHANA_SERVER_K_ANON` defaults to `4`. Below it, cross-engineer views are
withheld rather than shown thinly — org rollups, topic clusters, skill mining,
and most weekly-digest sections.

The practical consequence: **with three engineers the digest will be mostly
empty**, and that is working correctly. Onboard the team. The only legitimate
reason to lower the floor is a genuinely single-person install, where there is
nobody to de-identify from — see [Solo use](../solo/index.md).

## 2. The server-side LLM passes

**This is the fact most new operators miss: the server does nothing LLM-shaped
until you turn it on.** All three passes default to off. Out of the box the
server faithfully stores digests, serves the console, and writes no wiki articles
at all.

That default is deliberate. Each pass is a background loop that spends real money
and sends text to a model provider. An operator should *choose* that, not
discover it on an invoice.

| Pass | Flag | Model | Does |
|---|---|---|---|
| **Enrichment** | `MANTHANA_SERVER_ENABLE_ENRICHMENT=1` | `claude-haiku-4-5` | Fills the qualitative fields on the deterministic digests agents send. Everything downstream depends on it. |
| **Consolidation** | `MANTHANA_SERVER_ENABLE_CONSOLIDATION=1` | `claude-haiku-4-5` | Turns enriched digests into typed notes: decisions, conventions, gotchas, benchmarks. **Requires enrichment** — it only reads digests enrichment has filled in. |
| **Project overviews** | `MANTHANA_SERVER_ENABLE_PROJECT_OVERVIEW=1` | narrative model | Writes the living "what this project is" article per project. |

Plus a real model. Two ways:

```bash
# The API — the normal choice for a deployed server
MANTHANA_SERVER_LLM=anthropic
ANTHROPIC_API_KEY=sk-ant-…

# …or bring your own: a Claude CLI logged in as the user running the server,
# so you spend the subscription you already have instead of a second key.
MANTHANA_SERVER_LLM=claude_cli
MANTHANA_SERVER_CLAUDE_CLI=claude      # only if the binary isn't called `claude`
```

**`claude_cli` works on a laptop and not in a container.** It needs the binary
*and* a logged-in `$HOME`, so it's right for a server you run as yourself and
wrong for the published images. If the binary isn't reachable the server logs
loudly and falls back to the mock — it doesn't crash, and the wiki stays honest
but empty. Its cost is metered from what the CLI actually reports rather than
estimated from a price table.

Without either, narratives come from a deterministic mock that returns
"insufficient data" rather than inventing something.

**A working wiki configuration:**

```bash
MANTHANA_SERVER_LLM=anthropic
ANTHROPIC_API_KEY=sk-ant-…
MANTHANA_SERVER_ENABLE_ENRICHMENT=1
MANTHANA_SERVER_ENABLE_CONSOLIDATION=1
MANTHANA_SERVER_ENABLE_PROJECT_OVERVIEW=1
MANTHANA_SERVER_LLM_MONTHLY_CAP_USD=100
```

Turn them on in that order and give enrichment a pass or two to drain before
enabling consolidation — consolidation has nothing to read until enrichment has
run.

Every bound (batch sizes, intervals, retry limits) is listed in
[Environment variables](../reference/environment.md#enrichment--fills-in-the-qualitative-fields).
The defaults are sane; the ones worth knowing are `ENRICH_INTERVAL` (300s) and
`ENRICH_BATCH_PER_ORG` (25), which together mean a large backlog drains over
hours, not minutes, and cannot starve other tenants.

### Cost, roughly

Enrichment and consolidation run on Haiku and are bulk structured summarization,
not reasoning — on the order of a cent or two per session. Project overviews are
bounded by a hash of each project's contributing sessions: they regenerate when
the *work* changes, not on a timer, and never re-describe a project a human has
edited. Watch real numbers rather than trusting an estimate:

```bash
manthana-server usage acme --server-url https://manthana.acme.com
```

## 3. Budgets

```bash
# Server-wide default for every org (0 = unlimited)
MANTHANA_SERVER_LLM_MONTHLY_CAP_USD=100

# …or per org, at any time
manthana-server set-quota acme 100 --server-url https://manthana.acme.com
```

The shipped default is `0` — **unlimited**. That's right for a self-hoster paying
their own model bill: being throttled by a number someone else chose is worse
than the bill. Usage is still recorded either way.

If you run a **hosted, multi-tenant** server you are spending your own money on
other people's orgs, so set a real cap. `manthana-server onboard-org` already
provisions each new customer org with an explicit **$100/month** override.

$100 rather than something tighter because the failure mode of a low cap is
invisible and awful: enrichment stops, every session stays `pending`, and the
wiki quietly fills with unsummarised work that looks like a bug rather than a
bill. The cap exists to stop a runaway, not to ration normal use.

### Watching spend

```bash
manthana-server usage acme --server-url https://manthana.acme.com
# org=acme  cap=$100.00/mo (org override)
#   2026-07  $12.4180  (843 calls, 4,102,933 in / 210,447 out tokens)
```

For "*which* pass is eating the budget", the HTTP endpoint breaks spend down by
purpose (`enrich`, `consolidate`, and the narrative passes):

```bash
curl -H "X-Admin-Token: $ADMIN" \
  "https://manthana.acme.com/v1/admin/usage?org_id=acme"
```

That response also returns `spent_usd` for the current month and a plain
`quota_blocked` boolean, read from the same row that actually gates the passes —
so it can never disagree with the thing doing the blocking. Check it first when
the wiki stalls: an exhausted cap raises no error anywhere a human looks. It just
stops enrichment, and every session stays `pending`, which reads as a bug rather
than a bill.

## Next

→ [Reading the wiki & the founder digest](reading-the-wiki.md)
→ [Operating the server](operating.md)
