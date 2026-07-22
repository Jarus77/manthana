# Daily use

Once you've run `manthana setup`, the daemon does capture, compaction, release,
and sync on its own. This page is the handful of things worth typing.

## The dashboard

```bash
manthana dashboard      # http://127.0.0.1:8765
```

Binds loopback — it's your data, not a service. Everything you can do to a
session or a compaction lives here:

| Page | What it's for |
|---|---|
| **Home** | Recent sessions, cost, what's pending |
| **Sessions** | Set **Work / Personal**, trigger a **Compact** |
| **Compactions** | **Hold** or **Release**, and drill into what a digest actually says |
| **Ask** | The grounded question box over your own work |
| **Topics** | What you've been spending time on |
| **Cost** | Estimated API-equivalent spend, by project |
| **Skills** | Mine recurring patterns into draft skills |
| **Actions** | The local audit: what the agent did and why (including things it *suppressed*) |
| **Optimize** | Wire Claude Code through `headroom` for context compression, if installed |

## The five commands worth knowing

```bash
manthana doctor                                  # is everything healthy?
manthana insights --since 7d                     # token-free rollups of your week
manthana ask "why did the auth migration fail?"  # grounded, cited answer over your own work
manthana related <session-id>                    # prior work related to this session
manthana sessions --limit 20                     # list sessions, get an id
```

### `manthana insights`

Session and compaction counts, estimated API-equivalent cost, work by project and
outcome, recent friction, and any loop warnings — sessions flagged for repeated
failures. Costs nothing, calls nothing. `--since` accepts `7d`, `2w`, `12h`, or
an ISO date.

### `manthana ask`

A cited answer over **your own** compactions. It runs through your installed
`claude` CLI (falling back to `codex`) — **no API key needed**, nothing goes
anywhere your coding CLI doesn't already go. If neither CLI is on your `PATH`,
`doctor` tells you and `ask` degrades instead of erroring.

```bash
manthana ask "what did I work on last week?"
manthana ask "how did we end up choosing pgvector?" --source full
```

`--source full` restricts to full compactions, excluding the cheaper
summary-derived ones. Answers end with citations; if nothing grounded the answer,
it says so rather than inventing one.

### `manthana related`

```bash
manthana related 01J8Z…
# related prior work (3):
#   0.81  [billing] make the Stripe webhook idempotent
```

Local embeddings over your own history. The "you've done this before" surface.

### `manthana mine-skills`

```bash
manthana mine-skills                    # see the proposals
manthana mine-skills --write            # draft them to ~/.claude/skills/personal/
manthana mine-skills --threshold 0.6    # cluster more loosely
```

Clusters recurring patterns in your compactions into `SKILL.md` drafts.
Deterministic and offline by default. `--min-sessions` (default 3) sets how many
sessions a pattern needs before it counts.

### `manthana mcp`

Serves Manthana's read-only tools (`insights`, `ask`, `topics`, `thread`,
`drill_raw`) to Claude Code over MCP, scoped to your local data — so you can ask
about your own history from inside the tool you're already in. Needs the optional
MCP extra; the command tells you if it's missing.

## Controlling the daemon

```bash
manthana service status
manthana service uninstall     # stop capture entirely
manthana service install       # start again
```

Logs: `~/Library/Logs/manthana-watch.log` on macOS,
`journalctl --user -u manthana-watch.service` on Linux.

To run with different settings, uninstall the service and run `watch` yourself:

```bash
manthana watch --release-min 30 --settle-min 45
manthana watch --no-auto-release        # nothing releases without you saying so
manthana watch --no-sync-raw            # digests sync, raw transcripts don't
manthana watch --no-sync                # capture and compact only, push nothing
```

## Syncing by hand

```bash
manthana sync --check    # is the server reachable and my token accepted?
manthana sync            # push released, non-personal compactions
manthana sync --raw      # also push the redacted raw transcripts
```

The daemon does this for you. Reach for it when you've just released something
and don't want to wait for the next cycle.

## Recovering after a server reset

If your org's server was wiped, migrated, or re-onboarded, your laptop still
remembers what it already sent and will skip it — so your history would be
permanently missing from the new server. `doctor` flags this when raw uploads
start being rejected as unknown.

```bash
manthana resync              # dry run: what would be re-pushed
manthana resync --confirm    # clear the watermarks
manthana sync                # re-upload
```

`resync` deletes nothing locally — no sessions, no compactions, no transcripts.
It only clears the "already sent" marks. And it does not widen the gate: personal
and unreleased work stays put, exactly as before.

## Cleaning up local junk

```bash
manthana purge --self-generated              # dry run
manthana purge --structural-junk --confirm
```

Older versions compacted by shelling out to `claude -p`, which created a
transcript that was itself captured and compacted. `--self-generated` matches the
verbatim prompt text; `--structural-junk` catches the paraphrased ones by
requiring the session to have touched no files, have no real project, and be
abandoned. Your actual sessions touch files, so they survive it.

Dry run unless `--confirm`, and it refuses to run unfiltered.

## Upgrading

Manthana tells you when you've drifted behind. `manthana doctor` shows an
`agent version` line, and the CLI prints a short notice on stderr when your org
server runs a newer build than your agent — "latest" means *your org's server*,
not the newest public tag, because that's the version your admin actually
deployed. It never makes a network call on the command you ran, never prints in
CI, and never prints when output is piped.

Re-run the installer. It always converges on the requested release.

```bash
curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana version
```

Don't want the notice? `MANTHANA_NO_UPDATE_NOTIFIER=1`, or permanently in
`~/.manthana/manthana.toml`:

```toml
[update]
notifier = false
```

> If you installed before **2026-07-19**, the installer silently skipped
> upgrading when Manthana was already present, so you may be pinned to whatever
> version you first installed. Run the line above and check `manthana version`
> against what your admin expects.

## Next

→ [What happens to your data](your-data.md)
→ [Troubleshooting](../troubleshooting.md)
→ [Full CLI reference](../reference/cli.md)
