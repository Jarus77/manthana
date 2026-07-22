# Reading the wiki, the console, and the digest

There are three surfaces. They are for different questions, and the difference
matters.

| Surface | URL | Who | Answers |
|---|---|---|---|
| **The wiki** | `/` (Next.js client) | everyone with an engineer or founder token | "What is this project? What did we decide? What should I avoid?" |
| **The founder console** | `/ui` | founder / admin token | "What is the team doing, what's failing, where is money going?" |
| **The weekly digest** | `/ui/digest`, `manthana-server digest` | founder | "What happened this week?" |

If you only have the server running and no wiki client in front of it, the wiki
is served as plain server-rendered HTML under `/ui/home` instead. See
[the wiki client](../self-hosting/web-client.md).

## The wiki

The reading order that works:

1. **Main page** — org-wide digests and what's active right now.
2. **A project page** — the living article. What the project *is*, then what has
   been decided, learned, and hit. This is the ten-second onboarding for a new
   engineer, and it is the single page worth linking in a README.
3. **A note** — one typed claim (decision, convention, gotcha, benchmark) with
   its history and the sessions that produced it.
4. **A session** — the primary source. What was attempted (in the engineer's own
   first words), **What came out of it** — artifacts, PRs, tests, and a file count
   linking to the verbatim digest — **Dead ends**, and the measurable facts:
   duration, cost, tier.

The **Dead ends** section is worth pointing your team at. The digest has always
carried what was tried and abandoned, and a colleague about to walk down the same
one is the single reader this wiki can help most.

Every layer links down to the one below it. Nothing in the wiki asks you to take
it on faith; the evidence is one click away. If an article says something wrong,
open it and fix it — human edits are preserved, and the project-overview pass
never overwrites a project a human has edited.

**People and project pages** are built from co-occurrence: who has worked on
what, which projects touch which entities. That's how you find "who has touched
the billing service before" without asking in Slack.

## The founder console (`/ui`)

Sign in with your founder token (or the admin token on a self-hosted server).

| Panel | What it's for |
|---|---|
| **Ask** | A grounded, cited question over your org's released digests. Withheld with an explicit reason when it can't clear the k-anonymity floor. |
| **Topics** | Clusters of work across the org, floor-gated |
| **Sessions** | Released session digests, drillable |
| **Digest** | The weekly narrative |
| **Router** | Cost analyzer — what your sessions would have cost on cheaper tiers |
| **Mine skills** | Cluster recurring org-wide patterns into proposed skills |
| **Recent founder queries** | Your own audit trail |
| **Mint engineer token** | Give someone wiki access |

### "Insufficient data" is a real answer

When a query can't clear the floor, the console says so rather than showing you a
thin, re-identifiable slice. Same for the digest: it lists what it omitted. With
fewer than four contributors most cross-engineer output will be withheld, and
that is the system working. See
[Privacy posture](privacy-and-budgets.md#the-k-anonymity-floor).

### The drill-down

Session digests link to their raw transcript, which was redacted on the way out
of the engineer's laptop. This is Tier 2: org-scoped, and **every access writes
an audit row**, including the ones that come back empty.

That audit trail is not a formality. It's what makes it reasonable for your team
to leave raw sync on — they can see that looking leaves a mark. Read it with
`GET /v1/founder/audit?org_id=acme`, or the console panel.

## The weekly digest

```bash
manthana-server digest acme
manthana-server digest acme --since 2026-07-01 --until 2026-07-08
```

Prints each section with its citations, then an explicit list of what was omitted
for k-anonymity or lack of data. If nothing cleared the floor it tells you that
too, rather than printing something reassuring and empty.

A founder token can fetch the same thing over HTTP:
`GET /v1/founder/digest?org_id=acme`.

## The cost analyzer

```bash
manthana-server router-analysis acme
# org=acme  priced=310/412 sessions (skipped 102 pre-breakdown)
# current ~$482.10 → projected ~$291.40  = save $190.70 (39.6%)  downgrades: {...}
```

Then the ten largest individual opportunities. This is a counterfactual over work
already done — it tells you where the money went and what a different routing
policy would have cost, not what to do.

## If the wiki looks empty

In order of likelihood:

1. **The LLM passes are off.** They are off by default. See
   [Privacy posture & budgets](privacy-and-budgets.md#2-the-server-side-llm-passes).
2. **The budget cap is hit.** `manthana-server usage <org> --server-url …`.
3. **Enrichment is stuck** waiting for raw transcripts that never arrived.
   `GET /v1/admin/enrichment?org_id=…` shows the backlog and the abandoned ones.
4. **Fewer than four contributors**, so cross-engineer sections are withheld.
5. **Nobody has released anything yet.** Digests need a 30-minute settle plus a
   10-minute release window before their first sync.

Full symptom table in [Troubleshooting](../troubleshooting.md).

## Next

→ [Operating the server](operating.md)
