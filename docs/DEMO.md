# Manthana — Demo Runbook

Everything below runs **on your laptop, on your real data** (471 captured sessions).
Read top-to-bottom once, then drive from the **Cheat-sheet** at the end.

> **Fastest path — the whole onboarding flow, zero infra:** `./scripts/quickstart_demo.sh`
> boots a server (SQLite + in-memory), enrolls a team, onboards an engineer with one
> `manthana setup`, and runs `manthana doctor` — then tears itself down. See
> [onboarding.md](onboarding.md). Engineers install with:
> `curl -LsSf https://github.com/Suraj-gameramp/manthana/releases/latest/download/install.sh | sh`

---

## 0. The one-sentence pitch (say this first)

> "Every engineer now codes with AI agents all day — and all of that work, the
> decisions, the dead-ends, the costs, just evaporates into transcripts nobody reads.
> **Manthana captures that exhaust, distills each session into a typed digest, and
> turns it into two things: an engineer who can ask their own history and cut their
> token costs, and a founder who gets grounded, cited visibility into what the team
> is actually doing — without ever spying on anyone.**"

### The core problem (30 seconds)
- AI coding sessions are the new unit of engineering work, but they're **write-only** —
  the learning, the friction, the cost is lost the moment the terminal closes.
- Founders fly blind: they can't see what's working, what's stuck, or where money goes,
  except by interrupting people.
- Naive "just log everything to a dashboard" = surveillance. Engineers won't accept it.

### The solution in one breath
Local-first capture → typed **compaction** per session → the engineer owns it and can
**ask / optimize** → only what they **explicitly release** flows up, **redacted** and
**k-anonymized**, to a founder console that answers questions **with citations**.

---

## 1. The mental model (draw this)

```
ENGINEER's laptop (owns everything)          ORG server (sees only released+redacted)
┌─────────────────────────────────┐          ┌──────────────────────────────┐
│ Claude Code transcripts          │          │  Founder console (/ui)        │
│      ↓ capture                   │          │   "what's the team doing?"    │
│ Sessions + Turns (local SQLite)  │          │   → grounded, CITED narrative │
│      ↓ compact (their model)     │   release│   "what went wrong?"          │
│ Typed Compaction  ──────────────────────────▶  → friction, cited           │
│   ask · insights · optimize      │  redacted │  cross-engineer skill mining  │
│ (personal-mode NEVER leaves)     │  k-anon=4 │                              │
└─────────────────────────────────┘          └──────────────────────────────┘
        Apache-2.0                                      AGPL-3.0
```

**The trust contract is the product.** Say it out loud during the demo:
1. The employee owns the local store.
2. The org sees **only** what's *released* — after *redaction* (secrets/PII scrubbed)
   and *k-anonymity* (≥4 contributors, names dropped).
3. **Personal-mode sessions never leave the laptop** — enforced by a test from commit one.

---

## 2. One-time setup (do this BEFORE the demo, ~1 min)

```bash
cd /Users/suraj/Desktop/project
uv sync --all-packages            # deps
uv run manthana capture           # ingest your transcripts (you already have 471 sessions)
```

You already have 10 real compactions generated. Confirm:
```bash
uv run manthana insights --since 21d
```
You should see projects (scribe, dab_clone, bird-bench…), outcomes, and recent friction.

> If asked "did you spend my tokens?" — capture/insights are **token-free**. Compaction
> uses *your* `claude` CLI (your subscription), only when you click it.

---

## 3. DEMO PART 1 — The Engineer side (5–7 min)

### 3a. Token-free insight (no model call) — start here, it's instant
```bash
uv run manthana insights --since 14d
```
**Say:** "Zero tokens, zero LLM — this is just structure over my own captured work:
what I worked on, how it ended, and the real friction I hit. This already beats trying
to remember my week."

### 3b. Ask your own history — grounded + cited (uses your `claude` CLI)
```bash
uv run manthana ask "what did I work on in the last two weeks and what got stuck?"
```
**Say:** "Now natural language, but **grounded** — every claim is backed by a specific
compaction id. If it can't ground a claim, it says so instead of hallucinating."

### 3c. The dashboard (visual) — the centerpiece
```bash
uv run manthana dashboard          # opens http://127.0.0.1:8765
```
Walk these tabs in order:
- **Sessions** — "Every session, tagged. The Work/Personal toggle is the trust line:
  flip one to Personal and it can *never* sync." (flip one live)
- **Compactions** — "The typed digest per session: intent, approach, outcome, friction,
  files. This is the unit everything else is built on. I **Release** the ones I'm happy
  to share — nothing leaves until I do." (open one `<details>`, show intent/approach)
- **Cost** — "Token volume per session, plus an **API-list-equivalent** dollar figure —
  labeled honestly, because I'm on a subscription, not paying per token. The big number
  is cache-reads, not spend." (this honesty is a credibility point — see §5)
- **Skills** — "It mines recurring patterns across my sessions into proposed reusable
  **SKILL.md** files." (click Mine if you want)
- **Ask** — same grounded Q&A, in the browser.
- **Optimize** — "Direct integration with *headroom* to route Claude Code through
  context compression — 60–95% fewer tokens. Cost reduction is something Manthana
  *does*, not a chart you look at."

---

### 3d. The token & cost story (do this deliberately — it's your ROI beat)
Show the **footprint**, then the **lever** on it.

```bash
uv run manthana insights --since 21d      # footprint: tokens + API-list-equivalent $
```
Then on the dashboard: **Cost** tab (per-session tokens + list-equiv) → **Optimize** tab.

**Say (honest framing — this is the credibility move):**
> "This is the team's token **footprint** — and the dollar number is **API list-price
> equivalent**, mostly cache-reads, *not* my subscription bill. The real magnitude is
> the **token volume**. And here's the point: Manthana doesn't just *report* cost — via
> the Optimize integration it **routes Claude Code through context compression and cuts
> 60–95% of tokens** on future runs. Footprint *and* the lever, in one place."

**Why this matters to a founder:** AI-coding spend is becoming a real line item and it's
invisible today. Manthana makes it visible per-engineer/per-project (k-anonymized) **and**
acts to reduce it. Do NOT overclaim the dollar figure — let token volume carry the weight.

---

## 4. DEMO PART 2 — The Founder & Manager side (5–7 min)

**Set up the framing:** "Switch hats — I'm the founder now. I do NOT see transcripts or
personal sessions. I see only released, redacted, **k-anonymized** digests, and I get
answers **with citations**." To make this real we seed a **synthetic 12-engineer org**
(clearly fake `@acme.demo` data, isolated DB — never your real store).

### Setup (once, ~2s, no API key)
```bash
uv run python validation/seed_demo_org.py      # ~44 synthetic compactions, 11 engineers, 5 projects
```

### The demo — terminal, NO API key (reliable; this is the live demo)
```bash
uv run python validation/demo_queries.py       # uses your claude CLI at k_anon_floor=4
```
It walks the whole permission story over real, multi-contributor data:

| # | Question | What happens | Why |
|---|----------|--------------|-----|
| 1 | "Where did the team spend time on **LLM-evaluation**?" | grounded, **cited** rollup | project aggregate, ≥4 contributors |
| 2 | "What **kept failing** recently across the team?" | grounded, **cited** friction | friction aggregate |
| 3 | "What did **Suraj** work on this week?" *(founder)* | **INSUFFICIENT DATA** | k-anon won't single out a person |
| 4 | "What did **Suraj** work on this week?" *(manager)* | grounded, cited — **and LOGGED** | audited escalation |

**Say (the killer line):** "Watch this — as a **founder** I literally *cannot* ask 'what
did Suraj do' — the system refuses, because singling out a person breaks the privacy
contract. As a **manager** with a separate token, I can — but **every such lookup is
logged**. That's the whole pitch: **privacy by default, accountable escalation.** Not
surveillance."

### Map of YOUR example questions → the right surface
- "What did Suraj/Tarun/Atharva work on this week?" → **Manager view** (named, audited);
  founder view refuses it. One at a time *or* all (drop the name → team aggregate).
- "Where was time spent on LLM-eval?" / "What kept failing in the last 30 sessions?" →
  **Founder** aggregate (cited).
- "Show me sessions where I abandoned the approach" → **Engineer's own** view, on your
  real data: `uv run manthana ask "show me my abandoned sessions"` (no server needed).

### Optional — the same in the browser (polished)
```bash
export MANTHANA_SERVER_DB_URL="sqlite:///./manthana-demo.db"
export MANTHANA_SERVER_JWT_SECRET="$(python -c 'import secrets;print(secrets.token_hex(24))')"
export MANTHANA_SERVER_ADMIN_TOKEN="adm-demo"   MANTHANA_SERVER_MANAGER_TOKEN="mgr-demo"
export MANTHANA_SERVER_K_ANON=4
# real narratives in the browser need a model: set MANTHANA_SERVER_LLM=anthropic + a FRESH key
uv run manthana-server serve --port 8000
```
- **Founder console** → http://127.0.0.1:8000/ui (admin token) — aggregate, k-anon.
- **Manager view** → http://127.0.0.1:8000/ui/manager (manager token) — named queries,
  each shows a "named query — logged" banner; they appear in the audit panel.
*(More moving parts — rehearse it. The terminal runner above is the safe demo.)*

> **The k-anon floor is 4 in this demo (production value)** — it's real, not lowered.
> The synthetic org has ≥4 contributors per project, so aggregates clear it while
> single-person founder queries are correctly refused.

---

## 5. The technical story (for the technical evaluator, 3–4 min)

- **Monorepo, one namespace** (`manthana.{schemas,collectors,agent,server,skills}`),
  **dual-licensed**: client tooling Apache-2.0, server AGPL-3.0 — a real, separately
  distributable boundary.
- **Typed everything**: Pydantic v2 (`extra="forbid"`) with a mirrored JSON Schema;
  document-store-with-indexes over SQLite (local) / Postgres+pgvector (server).
- **The trust chokepoint is one function** (`eligible_for_sync`) — personal/unreleased
  never pass. Redaction (ECC-derived secret + PII patterns) runs on the path to release.
  K-anonymity floor (4) gates every founder aggregate; the actor is bound at ingest so it
  can't be spoofed.
- **Grounding is enforced in code**: the founder narrative resolves every `[citation]`
  to a real compaction by exact-or-unique-prefix; unmatched → withheld.
- **Skill miner**: embed (bge-large / hashing fallback) → community-detection cluster →
  LLM-synthesize → SKILL.md + provenance (content-hash, evidence trail), k-anon gated.
- **Deployable**: `docker compose up` (server + Postgres + MinIO), GHCR image, k8s
  manifests; engineers run a one-time `manthana login` + a launchd daemon, then it's
  hands-off.

### The credibility move: "we validated it, and here's what we found"
> "I didn't just build it — I ran an **empirical validation** on 10 real sessions and
> scored the output by hand. The reasoning quality was strong, but I found and **fixed
> five real defects**: cost was priced at list rates and looked like spend (now per-turn
> + token-volume + honestly labeled); a cumulative-summary bug duplicated one session
> into 46 compactions (now one); file extraction was unreliable (now deterministic from
> tool calls); long sessions dropped their endings (now head+tail); and the founder
> 'what went wrong?' query was a dead-end (now routes through friction). 204 tests,
> ruff+pyright clean. The findings and fixes are in `validation/` and `spec/§30`."

This is your strongest technical signal: *you measure and fix, not just ship.*

---

## 6. What's real vs roadmap (be honest — it builds trust)

**Real & demoable today:** capture, typed compaction (incl. reuse of Claude's own
summaries), engineer ask/insights/optimize, the dashboard, skill mining, the founder
console + grounded cited narratives, the full trust/redaction/k-anon contract, and the
deploy stack. 204 tests.

**Deferred (name them as the roadmap):**
- **Act** — Manthana proactively *doing* things (the governance dispatcher + approval
  queue exist; the action handlers are next).
- **Mine my own codebases** — the miner is input-agnostic; a code/AST collector plugs in.
- Resume-thread stitching; IDE collectors (Cursor); cross-org skill marketplace.

---

## 7. Demo cheat-sheet (drive from this)

| # | Do | Say (one line) |
|---|----|----------------|
| 1 | (pitch) | "AI sessions are the new unit of work — and it all evaporates." |
| 2 | `manthana insights --since 14d` | "Zero tokens — structure over my real work." |
| 3 | `manthana ask "...what got stuck?"` | "Natural language, but every claim is cited." |
| 4 | `manthana dashboard` → tabs | "I own this. Personal-mode never leaves. I release what I choose." |
| 5 | Cost tab | "Honest cost — tokens + API-list-equiv, not fake spend." |
| 6 | Optimize tab | "It actively cuts my token usage via headroom." |
| 7 | *switch hats* + `seed_demo_org.py` | "Now I'm the founder over a 12-person team — released + redacted + k-anon only." |
| 8 | `python validation/demo_queries.py` | "Aggregates are cited; 'what did Suraj do' is **refused** for a founder…" |
| 9 | (manager line) | "…but a **manager** can — and every named lookup is **logged**. Privacy by default, accountable escalation." |
| 10 | `manthana ask "my abandoned sessions"` | "And as the engineer, I query my OWN work freely." |
| 11 | (tech) | "Dual-licensed, one trust chokepoint, grounding + k-anon enforced in code." |
| 12 | (credibility) | "I validated on real data and fixed 5 defects. 200+ tests." |

**Pre-flight checklist (run 2 min before):**
- [ ] `uv run manthana insights --since 14d` returns data
- [ ] `uv run manthana dashboard` opens at :8765
- [ ] `uv run python validation/seed_demo_org.py` then `validation/demo_queries.py` runs
- [ ] (browser option only) server boots, `/ui` and `/ui/manager` load
- [ ] **Rotate the old Anthropic API key**; if using the browser narrative, put a fresh one in `.env`

**If something fails live:** fall back to `validation/FINDINGS.md` and `docs/report/` —
they tell the whole story without anything needing to run.
```
