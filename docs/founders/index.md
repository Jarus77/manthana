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

## Your path

1. **[Provisioning](provisioning.md)** — stand up a server, create the org and
   teams, invite your engineers.
2. **[Privacy posture & budgets](privacy-and-budgets.md)** — choose `k_anon` or
   `open`, turn on the server-side LLM passes (they're off until you do), set a
   monthly cap.
3. **[Reading the wiki & the digest](reading-the-wiki.md)** — the console, the
   wiki, the founder digest, and the drill-down.
4. **[Operating the server](operating.md)** — day-to-day: backlogs, spend,
   audits, adding people, purging.

If you're deploying the server yourself rather than using a hosted one, do
[Self-hosting](../self-hosting/index.md) first, then come back to step 2.

## The fastest honest start

Five minutes, no domain, no certificates, using Tailscale as the private network:

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

# 5. Send the printed `manthana setup mia_…` line to each engineer. That's their
#    entire onboarding.
```

Then read [Privacy posture & budgets](privacy-and-budgets.md) — until you enable
the server-side passes, the server stores digests faithfully but writes no wiki
articles.

Two things worth knowing before you start:

- **Cross-engineer features need ≥4 contributors.** The k-anonymity floor is 4 by
  default, so org rollups, skill mining, and most digest sections stay empty with
  three people. Onboard the team, not one person.
- **You can try the whole flow locally first**, with no infrastructure and no
  permanent changes: `./scripts/quickstart_demo.sh` from a repo checkout runs
  admin `serve` + `enroll` + engineer `setup` + `doctor` in throwaway temp dirs.

## Next

→ [Provisioning an org and teams](provisioning.md)
