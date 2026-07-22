# Manthana documentation

Manthana turns the AI coding sessions your team is already having into a shared,
searchable org wiki — without anyone writing documentation, and without anyone
being surveilled.

Everything starts on the laptop. Sessions are captured locally, digested locally,
and only the parts an engineer releases ever travel. Read
[How Manthana works](reference/architecture.md) once and the rest of these pages
will make sense.

## Pick your path

| You are… | Start here | You'll end up able to |
|---|---|---|
| **A founder or admin** setting Manthana up for a team | [Founders & admins](founders/index.md) | Provision an org, invite engineers, set the privacy posture and AI budget, read the wiki and the weekly digest |
| **An engineer** who was sent an invite | [Engineers](engineers/index.md) | Install in one command, know exactly what leaves your laptop and when, hold or hide anything you want to |
| **On your own** — no company, no server, no API key | [Solo & independent use](solo/index.md) | Run `manthana solo` and get the whole thing locally: your own wiki, grounded questions over your own work, mined skills |
| **Self-hosting the server** for someone else | [Self-hosting](self-hosting/index.md) | Deploy the server + wiki client behind HTTPS, manage secrets, upgrade safely |

## Reference (all audiences)

| Page | What's in it |
|---|---|
| [How Manthana works](reference/architecture.md) | The pipeline end to end, the two-layer model, where every piece of data lives |
| [Privacy & security model](reference/privacy.md) | What never leaves the laptop, redaction, k-anonymity, `privacy_mode`, audit trails |
| [CLI reference](reference/cli.md) | Every `manthana` and `manthana-server` command and flag |
| [Environment variables](reference/environment.md) | Every `MANTHANA_*` variable, its default, and what it does |
| [Troubleshooting](troubleshooting.md) | Symptom → cause → fix, starting with `doctor` |

## The short version

```
your AI coding session
  ↓ captured every 5s to SQLite on your laptop         (never uploaded)
  ↓ compacted 30 min after the transcript goes quiet   (deterministic, local, free)
  ↓ auto-released after a 10 min grace window          (personal/held sessions never are)
  ↓ synced to your org server, redacted                (only released, non-personal work)
  ↓ enriched + consolidated server-side into wiki articles
```

Two windows, two different jobs — the **30-minute settle window** decides when a
session is *finished*; the **10-minute release window** is your chance to stop it
from being shared. They are explained properly in
[What happens to your data](engineers/your-data.md).

## Licensing

Everything an engineer runs is Apache-2.0. The org server is AGPL-3.0-or-later.
See [`../LICENSE`](../LICENSE) and [`../NOTICE`](../NOTICE).
