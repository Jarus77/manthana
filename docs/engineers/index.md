# Manthana for engineers

Someone sent you an invite. Here's what this is, what it does with your data, and
the ten minutes of setup.

## What it is

Manthana watches the transcripts your AI coding tools already write, distills each
finished session into a short structured digest, and — for the sessions you're
willing to share — sends those digests to your team's server, where they become a
small wiki your team can actually read.

You get something out of it locally too: a searchable memory of your own work,
"you've solved this before" surfacing, and cost rollups.

## What it does with your data — the short version

- **Capture is local.** Sessions go into SQLite on your laptop. Nothing is
  uploaded at capture time.
- **Compaction is local, deterministic, and free.** It spends no tokens and calls
  no model. It's structure extraction over a transcript you already have.
- **Nothing syncs until it is released**, and release is an *opt-out* window: a
  digest sits for 10 minutes before it becomes eligible.
- **Personal-mode sessions never sync.** Ever. There is no admin override.
- **Free text is redacted before it leaves** — keys, tokens, private key blocks,
  and a best-effort PII pass, on your machine, on the way out.
- **Your founder cannot browse your raw transcripts.** They exist on the server
  (redacted) for citation and search, behind a drill-down that writes an audit row
  every time it's used.

The full contract, including the parts that constrain your founder, is in
[What happens to your data](your-data.md). It's worth ten minutes.

## Install

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
```

Installs `uv` if you don't have it, then the `manthana` CLI. Then paste the line
your admin sent you:

```bash
manthana setup mia_…                       # an invite bound to you
manthana setup mia_… --actor you@acme.com  # a shared team invite
```

That one command redeems the invite, connects to your org server, installs the
capture daemon so it runs at login (macOS `launchd`, Linux `systemd --user`,
Windows Scheduled Task), runs a first capture, and confirms:

```
✓ connected as you@acme.com → https://manthana.acme.com
  captured 14 session(s) · auto-capture: installed (runs at login)
  dashboard: http://127.0.0.1:8765  ·  health check: manthana doctor
```

Prefer to run the daemon yourself? `manthana setup … --no-service`, then
`manthana watch` in a terminal you keep open.

## Verify

```bash
manthana doctor
```

Configured, server reachable, token accepted, database ready, model CLI available
for `manthana ask`, daemon installed, and your local data counts with the last
sync time. Exits non-zero if something critical is wrong. This is the first thing
to run whenever anything looks off.

## Then what?

Nothing. Work normally. Capture is automatic; the first digests appear once a
session has been quiet for 30 minutes.

When you want to look at what it has:

```bash
manthana dashboard      # http://127.0.0.1:8765
```

## Your path

1. **[What happens to your data](your-data.md)** — the timeline, the two windows,
   how to hold or hide anything.
2. **[Daily use](daily.md)** — the handful of commands worth knowing, and the
   dashboard.
3. **[Troubleshooting](../troubleshooting.md)** — when `doctor` isn't happy.

## Uninstalling

Nothing is installed system-wide beyond a CLI and a login service.

```bash
manthana service uninstall     # stop capture
uv tool uninstall manthana     # remove the CLI
rm -rf ~/.manthana             # remove your local store
```

Digests you already released stay on the org server; they're the org's record of
work, like merged commits. Anything you kept personal or held was never there.

## Next

→ [What happens to your data](your-data.md)
