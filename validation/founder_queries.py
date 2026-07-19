"""End-to-end founder query validation — 10 queries, auto-scored.

Two halves (the user asked for cross-engineer queries, which single-actor real data
can't supply, so cross-engineer runs against the synthetic 11-engineer org):

  A. REAL data (your own 10 compactions) — self per-person view, k-anon 1.
  B. SYNTHETIC org (11 engineers, 5 projects) — founder aggregates at k-anon 4,
     plus the k_anon-refuses-vs-open-allows per-person contrast.

For each query it records: the parsed filter (vs an expected filter → (a) filter
correct), the rollup, the narrative, and every citation verified against the visible
set + the filter's own constraint → (b) citations accurate. (c) narrative usefulness
is left for the human to score. Emits validation/founder_scoring_sheet.md.

Prereqs: `validation/recompact_ten.py` (fresh real digests) and
`validation/seed_demo_org.py` (synthetic org). Spends real tokens (parse+narrative
per query via `claude -p`).

Run:  uv run python validation/founder_queries.py
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manthana.agent.llm.provider import ClaudeCLIProvider
from manthana.schemas import EngineeringCompaction
from manthana.server import ServerConfig, ServerStore
from manthana.server.founder import FounderResult, run_query

REAL_DB = str(Path.home() / ".manthana" / "manthana.db")
DEMO_DB_URL = "sqlite:///./manthana-demo.db"
DEMO_ORG = "acme-demo"

REAL_IDS = [
    "comp-59896ed2-495e-4661-a0a5-ec85c8318ed9.11",
    "comp-138db3b9-4762-4d01-8633-2764c60b197c.2",
    "comp-1782ce84-8cc9-44c7-9f70-c17a85fb1111.7",
    "comp-7f09f08c-a4c3-4360-ab62-6063864bdce9",
    "comp-10375d20-b5a4-4e08-8d51-198705175757",
    "comp-b636d78d-c0c8-4e94-a7c7-52f8d0a8dfa3.96",
    "comp-2eddfe69-cd5e-46c8-bfae-a3b77eefe20f.7",
    "comp-a8aeb113-5f21-41cd-8e7d-3a9ecb6d8cf7.12",
    "comp-4811a97d-8461-4c22-b9b1-d4951bd853ab",
    "comp-2647d07f-646e-444d-b3a6-c16c94f67b70",
]


@dataclass
class Q:
    """A scored query. ``expect`` keys are filter fields that SHOULD be set (with the
    expected value); ``forbid`` are fields that should stay null (the classic trap:
    'what failed' must NOT set outcome=abandoned). ``privacy_open`` runs the query as an
    org whose privacy_mode is "open" (named, audited individual view) rather than the
    default "k_anon"; ``expect_insufficient`` asserts k-anon refuses."""

    bank: str  # "REAL" | "SYNTH"
    query: str
    expect: dict[str, str] = field(default_factory=dict)
    forbid: list[str] = field(default_factory=list)
    privacy_open: bool = False
    expect_insufficient: bool = False
    note: str = ""


QUERIES = [
    # ── A. REAL data — your own 10, self per-person view (k-anon 1) ──────────
    # A k_anon org correctly REFUSES per-person actor queries; "what did I work on"
    # is a self lookup, so these run as an "open" org (allow_individual, audited).
    # The abandoned-outcome demo lives in SYNTH (the real 10 contain zero abandoned
    # sessions).
    Q("REAL", "what did I work on recently?", privacy_open=True,
      note="broad self rollup; cites several"),
    Q("REAL", "where did I hit the most friction or blockers recently?", privacy_open=True,
      forbid=["outcome"], note="friction question — must NOT collapse to outcome filter"),
    Q("REAL", "where did I spend time on LLM evaluation or benchmark work?", privacy_open=True,
      note="semantic; should cite the eval/benchmark sessions, not all 10"),
    # ── B. SYNTHETIC org — cross-engineer aggregates (k-anon 4) ──────────────
    Q("SYNTH", "where did the team spend time on LLM evaluation work?",
      expect={"project": "llm-eval"}, note="project aggregate across 5 engineers"),
    Q("SYNTH", "what kept failing recently across the team?", forbid=["outcome"],
      note="friction question — must NOT collapse to outcome=abandoned"),
    Q("SYNTH", "which sessions did the team abandon?", expect={"outcome": "abandoned"},
      note="explicit outcome restriction → outcome=abandoned"),
    Q("SYNTH", "what is the team building in the text-to-sql project?",
      expect={"project": "text-to-sql"}, note="project aggregate"),
    Q("SYNTH", "what went wrong in the ASR / speech work?", forbid=["outcome"],
      note="project-scoped friction; project≈asr, outcome null"),
    # per-person: k_anon org REFUSED, open org ALLOWED (same question)
    Q("SYNTH", "what did Suraj work on this week?", expect={"actor": "suraj"},
      expect_insufficient=True, note="K-ANON org per-person → k-anon refuses"),
    Q("SYNTH", "what did Suraj work on this week?", expect={"actor": "suraj"},
      privacy_open=True, note="OPEN org per-person → allowed + audited"),
]


def _load_real() -> list[EngineeringCompaction]:
    con = sqlite3.connect(REAL_DB)
    out: list[EngineeringCompaction] = []
    for cid in REAL_IDS:
        row = con.execute("SELECT data FROM compaction WHERE id=?", (cid,)).fetchone()
        if not row:
            print(f"  (missing {cid})")
            continue
        data = json.loads(row[0])
        data["released"] = True
        out.append(EngineeringCompaction.model_validate(data))
    con.close()
    return out


def _filter_score(spec: Any, q: Q) -> tuple[str, str]:
    """(verdict, detail) for (a) filter correct."""
    got = {k: getattr(spec, k, None) for k in ("project", "outcome", "actor", "surface")}
    problems: list[str] = []
    for key, want in q.expect.items():
        val = (got.get(key) or "")
        # actor/project matched by substring (parser may emit 'suraj@acme.demo' or 'llm-eval')
        if want.lower() not in str(val).lower():
            problems.append(f"{key}: want~{want!r} got {val!r}")
    for key in q.forbid:
        if got.get(key):
            problems.append(f"{key} should be null, got {got[key]!r}")
    nonnull = {k: v for k, v in got.items() if v}
    if not problems:
        return "PASS", f"filter ok {nonnull}"
    return "FAIL", "; ".join(problems) + f"  (full: {nonnull})"


def _cite_score(res: FounderResult, visible_by_id: dict[str, Any], q: Q) -> tuple[str, str]:
    """(verdict, detail) for (b) citations accurate: each citation exists, and if the
    filter restricts outcome, every cited comp actually has that outcome."""
    if not res.citations:
        return ("N/A", "no citations (insufficient/empty)")
    bad: list[str] = []
    want_outcome = q.expect.get("outcome")
    for cid in res.citations:
        c = visible_by_id.get(cid)
        if c is None:  # try unique prefix
            cand = [x for x in visible_by_id if x.startswith(cid)]
            c = visible_by_id[cand[0]] if len(cand) == 1 else None
        if c is None:
            bad.append(f"{cid[:24]}=GHOST")
        elif want_outcome and getattr(c.outcome, "value", str(c.outcome)) != want_outcome:
            bad.append(f"{cid[:24]}=outcome {c.outcome} != {want_outcome}")
    if bad:
        return "FAIL", "; ".join(bad)
    return "PASS", f"{len(res.citations)} citations all valid"


def _run_bank(bank: str, store: ServerStore, config: ServerConfig, org: str,
              visible_by_id: dict[str, Any], provider: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, q in enumerate([x for x in QUERIES if x.bank == bank], 1):
        print("\n" + "=" * 80)
        tag = "OPEN" if q.privacy_open else "K-ANON"
        print(f"[{bank} {i}] ({tag}) {q.query}")
        res = run_query(store, config, org_id=org, query=q.query,
                        provider=provider, allow_individual=q.privacy_open)
        fv, fd = _filter_score(res.filter, q)
        print(f"  (a) FILTER  [{fv}] {fd}")
        if res.insufficient_data:
            ok = "PASS" if q.expect_insufficient else "FAIL"
            print(f"      -> insufficient_data (k-anon refused) "
                  f"[expected={q.expect_insufficient} -> {ok}]")
            rows.append(dict(bank=bank, query=q.query, view=tag, filter_verdict=fv,
                             filter_detail=fd, cite_verdict="N/A",
                             cite_detail="refused", insufficient=True,
                             expect_insufficient=q.expect_insufficient, narrative="(refused)",
                             citations=[], note=q.note))
            continue
        if q.expect_insufficient:
            print("      -> WARNING: expected k-anon refusal but got data!")
        cv, cd = _cite_score(res, visible_by_id, q)
        r = res.rollup
        if r:
            print(f"      rollup: sessions={r.session_count} "
                  f"contributors={r.distinct_contributors} tokens={r.total_tokens:,}")
        print(f"  (b) CITES   [{cv}] {cd}")
        print(f"      narrative: {res.narrative[:280]}")
        rows.append(dict(bank=bank, query=q.query, view=tag, filter_verdict=fv,
                         filter_detail=fd, cite_verdict=cv, cite_detail=cd,
                         insufficient=False, expect_insufficient=q.expect_insufficient,
                         narrative=res.narrative, citations=res.citations, note=q.note))
    return rows


def _sheet(rows: list[dict[str, Any]]) -> str:
    out = ["# Founder query scoring sheet",
           "",
           "Auto-scored: **(a) filter correct** and **(b) citations accurate**. "
           "**(c) narrative useful** is for you to score 1–5.",
           "",
           "| # | bank | view | query | (a) filter | (b) cites | (c) useful (you) | overall |",
           "|---|------|------|-------|-----------|-----------|------------------|---------|"]
    for i, r in enumerate(rows, 1):
        a = r["filter_verdict"]
        b = r["cite_verdict"]
        out.append(
            f"| {i} | {r['bank']} | {r['view']} | {r['query'][:52]} | {a} | {b} | ___ | ___ |"
        )
    out += ["", "## Per-query detail", ""]
    for i, r in enumerate(rows, 1):
        out += [f"### {i}. [{r['bank']}/{r['view']}] {r['query']}",
                f"- note: {r['note']}",
                f"- (a) filter: **{r['filter_verdict']}** — {r['filter_detail']}",
                f"- (b) citations: **{r['cite_verdict']}** — {r['cite_detail']}",
                f"- citations: {r['citations']}",
                f"- narrative: {r['narrative']}",
                ""]
    npass_a = sum(1 for r in rows if r["filter_verdict"] == "PASS")
    npass_b = sum(1 for r in rows if r["cite_verdict"] == "PASS")
    n_cite = sum(1 for r in rows if r["cite_verdict"] in ("PASS", "FAIL"))
    out += ["## Auto-score summary",
            f"- (a) filter correct: {npass_a}/{len(rows)}",
            f"- (b) citations accurate: {npass_b}/{n_cite} (of queries that returned citations)",
            ""]
    return "\n".join(out)


def main() -> None:
    if not Path("manthana-demo.db").exists():
        raise SystemExit("run `uv run python validation/seed_demo_org.py` first")
    provider = ClaudeCLIProvider(timeout=300)
    print(f"provider={provider.name} available={provider.available()}")

    rows: list[dict[str, Any]] = []

    # ── A. REAL data (self view, k-anon 1) ───────────────────────────────────
    real = _load_real()
    print(f"\nloaded {len(real)} REAL compactions")
    rstore = ServerStore.open("sqlite://")
    rconfig = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=1)
    rstore.create_org("real", "You (real)")
    for c in real:
        rstore.ingest_compaction(c, org_id="real", team_id="t1")
    rows += _run_bank("REAL", rstore, rconfig, "real", {c.id: c for c in real}, provider)

    # ── B. SYNTHETIC org (cross-engineer, k-anon 4) ──────────────────────────
    dstore = ServerStore.open(DEMO_DB_URL)
    dconfig = ServerConfig(jwt_secret="x" * 40, admin_token="adm", k_anon_floor=4)
    # visible set for citation checks = all demo compactions
    con = sqlite3.connect("manthana-demo.db")
    demo_by_id: dict[str, Any] = {}
    for (data,) in con.execute("SELECT data FROM released_compaction"):
        c = EngineeringCompaction.model_validate(json.loads(data))
        demo_by_id[c.id] = c
    con.close()
    print(f"\nloaded {len(demo_by_id)} SYNTHETIC compactions (org={DEMO_ORG})")
    rows += _run_bank("SYNTH", dstore, dconfig, DEMO_ORG, demo_by_id, provider)

    sheet = _sheet(rows)
    dest = Path(__file__).parent / "founder_scoring_sheet.md"
    dest.write_text(sheet)
    json_dest = Path(__file__).parent / "founder_query_results.json"
    json_dest.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nwrote {dest}\nwrote {json_dest}")


if __name__ == "__main__":
    main()
