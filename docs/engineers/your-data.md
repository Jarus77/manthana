# What happens to your data, and when

The honest timeline for one coding session, with the exact points where you can
intervene.

## The timeline

```
you close your editor
   │
   │  0s      capture already happened — every 5 seconds, into SQLite on
   │          your laptop. Nothing has been uploaded.
   │
   │  30 min  the SETTLE window. Thirty minutes with no new transcript
   │          activity and the session counts as finished, so it gets
   │          compacted: a deterministic, local, token-free digest.
   │          Resume the session later and it re-compacts.
   │
   │  +10 min the RELEASE grace window. The digest sits for ten minutes.
   │          This is your window. Hold it, or mark the session personal,
   │          and it never becomes eligible.
   │
   │          ↓ redaction runs here, on your machine
   │
   │          synced to the org server
```

## The two windows are different things

They get confused constantly. They are unrelated.

| | **Settle** | **Release grace** |
|---|---|---|
| Default | **30 minutes** | **10 minutes** |
| Question | "Is this session over?" | "Do you want to stop this being shared?" |
| Starts | last transcript activity | when the digest was built |
| Change with | `manthana watch --settle-min 45` | `manthana watch --release-min 30` |

The settle window is 30 minutes because that's the same gap used to decide where
one session ends and the next begins. A shorter settle would compact a session
that hadn't actually finished, producing a digest of half your work — and that's
been observed, so the default was raised from 10 to 30.

If ten minutes isn't enough thinking time for you, widen it:

```bash
manthana service uninstall
manthana watch --release-min 60      # or reinstall the service after editing it
```

Or turn auto-release off entirely and release by hand:

```bash
manthana watch --no-auto-release
```

## The three ways to keep something to yourself

### 1. Personal mode — the strong one

```bash
manthana mode <session-id> personal
```

Or the **Personal** toggle in the dashboard. A personal session:

- never auto-releases,
- is never pushed by `manthana sync`,
- is not pushed by `manthana resync --confirm` either, whose entire job is to
  re-upload everything,
- has no server-side setting, admin flag, or founder request that can pull it.

Use it for anything that isn't work — and for work you'd rather not narrate.

### 2. Hold — the reversible one

Press **Hold** on a compaction in the dashboard within the grace window. It stays
local until you release it, and a re-compaction (after you resume the session)
carries the hold forward rather than quietly clearing it.

Change your mind later:

```bash
manthana release <compaction-id>
```

### 3. Purge — the after-the-fact one

```bash
manthana purge --contains "spike-branch"          # dry run
manthana purge --contains "spike-branch" --confirm
```

Deletes local compactions matching a filter. Dry-run by default, and it refuses
to run with no filter at all. Sessions and turns survive, so `manthana compact`
can re-derive a digest if you purge one by mistake.

This deletes your **local** copy. Anything already synced is on the server —
ask your admin, who has an audited purge for that.

## What is actually in a digest

The structured facts about a session, not the conversation:

intent · project · files touched · commands run · tests added · PRs opened ·
languages · dead ends · outcome · friction · tokens and estimated cost · your
actor identity · timestamps.

The narrative fields are filled in **server-side**, after sync, on your org's
key — which is why compaction on your laptop costs nothing.

## What about the raw transcript?

When a compaction is released, `manthana watch` also uploads the session's raw
transcript — **redacted, per turn, before it leaves**. It exists so the org can
cite and search primary sources rather than trusting a summary.

It is not browsable. Reaching one goes through an org-scoped, founder-only
drill-down that writes an audit row every single time, including lookups that
return nothing. Your founder cannot read your sessions without leaving a mark,
and you can ask to see that trail.

Don't want raw uploaded at all?

```bash
manthana watch --no-sync-raw
```

Digests still sync; the transcripts stay local. Expect the wiki to cite your work
less richly.

## What redaction removes

On the way out, on your machine: AWS access keys, `-----BEGIN … PRIVATE KEY-----`
blocks, JWTs, GitHub tokens, `secret:` / `password:` / `api_key=` style
assignments, and a best-effort PII pass. Check it's on:

```bash
manthana config
# redact:     secrets=True pii=True
```

Toggle in `~/.manthana/manthana.toml`:

```toml
[redaction]
secrets = true
pii = true
```

Redaction is a safety net, not a guarantee that a pasted credential is
unrecoverable. If you pasted something you shouldn't have, mark the session
personal or purge the compaction.

## Where your data lives

```bash
manthana datahome
# data_home: /Users/you/.manthana
# db_path:   /Users/you/.manthana/manthana.db
```

Everything local is in that one directory: the SQLite store and `manthana.toml`
(mode `0600`, holds your team token). Move it with `MANTHANA_DATA_HOME`. Delete
it and Manthana forgets everything local.

## Next

→ [Daily use](daily.md)
→ [Privacy & security model](../reference/privacy.md) — the same guarantees from the server's side
