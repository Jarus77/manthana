# Manthana for founders & admins

Your team already produces an enormous amount of reasoning every week, inside AI
coding sessions, and then throws almost all of it away. The decisions, the dead
ends, the "oh, that's why the migration fails on staging" — none of it survives
the session it happened in. Nobody writes it down, because writing it down is a
second job.

Manthana keeps it. Automatically, from the sessions your engineers are already
having, without asking anyone to document anything and without turning you into
someone who reads their team's chat logs.

## What you actually get

**A living wiki.** One small article per project that a new engineer absorbs in
ten seconds — what this thing is, what was decided, what to avoid. Written by the
server from the sessions underneath it, and every claim links down to the session
that supports it.

**Session digests as primary sources.** One typed digest per session: intent,
what changed, files touched, tests added, dead ends, outcome, cost. This is the
evidence layer. The wiki is a summary of it, never a substitute for it.

**A weekly digest.** What the team shipped, what fought back, where money went —
grounded in citations, with an explicit list of what was withheld for privacy.

**A cost analyzer.** What your sessions would have cost on cheaper model tiers.

## What you don't get, on purpose

You cannot read your engineers' sessions freely. Personal-mode sessions never
leave their laptops at all, and nothing else leaves without being released and
redacted. Raw transcripts exist on your server but sit behind an audited
drill-down that records every look.

This is not a limitation we're apologising for — it's the reason engineers leave
Manthana turned on. A tool your team quietly disables produces nothing.
[Privacy & security model](../reference/privacy.md) has the full contract; it's
worth reading before you pitch this internally, because the first question you'll
get is "so you can read my chats?" and the answer is no.

## The fastest start: use the hosted server (no setup)

If a Manthana server is already running for your org, you stand up **nothing** —
no Docker, no Postgres, no domain, no certificates, no Tailscale. You provision
your org and hand each engineer a one-line invite. latentspaces runs a hosted
server at `https://api.latentspaces.in`; substitute your own if you were given a
different one.

There are two shapes to this, depending on whether you hold the server's operator
admin token.

### A. You run (or operate) the hosted server

One command creates the org, its team(s), the engineer invites, your founder
console token, and the monthly AI budget — and prints a paste-ready welcome
block. It works purely over the admin HTTP API, so you run it from your laptop;
the server's database is never touched directly.

```bash
# Install the CLI once (this is the client, not a server — nothing to run):
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server

# The operator admin token gates provisioning. On the latentspaces AWS server it
# lives in Secrets Manager; anywhere else, it's whatever you set at deploy time.
export MANTHANA_SERVER_ADMIN_TOKEN=…

# Provision the whole org in one call. --open = one shared invite the whole team
# redeems; use --emails <file> instead to mint a single-use invite per address.
manthana-server onboard-org acme "Acme Inc" --open \
  --server-url https://api.latentspaces.in
```

That prints something you can paste straight into an email or Slack:

```
══ Welcome to Manthana — Acme Inc ══
Server: https://api.latentspaces.in

Engineers — each runs ONE command (invites expire in 14d):
  [team core] manthana setup mia_eyJzZXJ2ZXIiOi…

Founder console: https://api.latentspaces.in/ui
  sign-in token (founder-only, keep private): fdr_…

AI budget: $100.00/month
```

Send the `manthana setup mia_…` line to your engineers — that is their **entire**
onboarding, and the token never travels in chat, only the invite that redeems for
one. Keep the founder token private; it's how *you* read the wiki and digest at
`/ui`. Need more invites later, or a new hire? Run `onboard-org` again (it's
idempotent on the org) or see [Provisioning](provisioning.md) for the individual
`enroll` / invite commands.

### B. Someone hosts it for you

If latentspaces (or another operator) runs the server on your behalf, you install
**nothing at all**. They send you the welcome block above; you forward the
`manthana setup mia_…` lines to your engineers and sign in to `/ui` with your
founder token. To add people or raise your budget, you ask the operator — the
provisioning commands need the admin token, which stays with whoever runs the
server.

Either way, the invite is the whole story for an engineer: one `manthana setup`
command and their sessions start flowing.

## Your path from here

1. **[Provisioning](provisioning.md)** — the full command surface: orgs, teams,
   individual vs. shared invites, adding one engineer later. (Self-hosting a
   server of your own is [Self-hosting](../self-hosting/index.md).)
2. **[Privacy posture & budgets](privacy-and-budgets.md)** — choose `k_anon` or
   `open`, turn on the server-side LLM passes (they're off until you do), set a
   monthly cap.
3. **[Reading the wiki & the digest](reading-the-wiki.md)** — the console, the
   wiki, the founder digest, and the drill-down.
4. **[Operating the server](operating.md)** — day-to-day: backlogs, spend,
   audits, adding people, purging.

Two things worth knowing before you invite people:

- **The wiki is empty until the LLM passes are on.** A freshly provisioned org
  stores every digest faithfully but writes no articles until you enable
  enrichment and the overview pass — see [Privacy posture & budgets](privacy-and-budgets.md).
  On the hosted latentspaces server these are already on.
- **Cross-engineer features need ≥4 contributors.** The k-anonymity floor is 4 by
  default, so org rollups, skill mining, and most digest sections stay empty with
  three people. Onboard the team, not one person.

## Or self-host in five minutes (Tailscale)

Prefer to run the server yourself, with no domain or certificates? Tailscale
gives you a private network with automatic HTTPS:

```bash
# 1. Everyone (you + engineers) installs Tailscale and joins your tailnet,
#    with MagicDNS + HTTPS enabled in the Tailscale admin console.
tailscale up

# 2. On your machine only — install the server:
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh -s server

# 3. Run it. Auto-generates and persists secrets; Tailscale provides HTTPS.
manthana-server serve --tailscale
#    → prints your admin token and https://<machine>.<tailnet>.ts.net

# 4. Create one shared invite for the team:
manthana-server enroll acme platform --open \
  --server-url https://<machine>.<tailnet>.ts.net

# 5. Send the printed `manthana setup mia_…` line to each engineer.
```

Then read [Privacy posture & budgets](privacy-and-budgets.md) to turn on the wiki
passes. For a real domain, Docker, or cloud deployment instead, see
[Self-hosting](../self-hosting/index.md).

You can also try the whole flow locally first, with no infrastructure and no
permanent changes: `./scripts/quickstart_demo.sh` from a repo checkout runs admin
`serve` + `enroll` + engineer `setup` + `doctor` in throwaway temp dirs.

## Next

→ [Provisioning an org and teams](provisioning.md)
