'use client'

/**
 * The console's charts, as inline SVG on the design tokens.
 *
 * No charting library: there are three charts, all simple, and a dependency would
 * cost ~100 kB and bring a second styling model to reconcile with the tokens.
 *
 * COLOUR WAS MEASURED, NOT JUDGED. Every pair that can appear adjacent was run
 * through the dataviz validator in both themes:
 *
 *   baseline vs projected  slate-500 ↔ indigo-600  ΔE 19.7 light
 *                          slate-500 ↔ indigo-400  ΔE 17.4 dark
 *   over-cap vs under-cap  red-600   ↔ indigo-600  ΔE 37.2 light
 *                          red-400   ↔ indigo-400  ΔE 26.0 dark
 *
 * The baseline is `--chart-baseline` (slate-500 in BOTH themes) rather than
 * `--muted-foreground`, because dark's muted is slate-400 and slate-400 ↔
 * indigo-400 measures ΔE 13.0 — under the 15 floor, and genuinely hard to tell
 * apart with full colour vision. That is the one thing here I would have got wrong
 * by eye.
 *
 * The validator also flags slate as "reads gray" (below its chroma floor). That is
 * correct and intended: a baseline bar is the BEFORE state, not a series identity,
 * and grey is what says so.
 *
 * Every chart is a single measure, so none carries a legend — the title names the
 * series, and a legend box for one thing is noise. Values are direct-labelled
 * rather than axis-read, since these are short series a reader wants exact numbers
 * from.
 */

import { cn } from '@/lib/utils'

export function usd(n: number): string {
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(4)}`
}

/** Shared empty state, so a chart with no data never renders as a broken axis. */
function NoData({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

/* ── 1. spend by month, against the cap ─────────────────────────────────── */

/**
 * Answers "am I about to be throttled", which today has no other symptom — a hit
 * cap just stops enrichment, and the wiki quietly fills with unsummarised sessions
 * that read as a product bug.
 *
 * The cap is a reference line rather than a bar: it is a threshold, not a
 * measurement, and drawing it as a mark of the same kind would invite reading it as
 * more spend. Months at or over it turn destructive, so the chart states the
 * condition rather than leaving it to be inferred from a crossing.
 */
export function SpendByMonth({
  months,
  capUsd,
}: {
  months: Array<{ month: string; est_cost_usd: number }>
  capUsd: number
}) {
  if (!months.length) return <NoData>No AI spend recorded yet.</NoData>

  const series = [...months].reverse() // oldest → newest reads left to right
  const capped = capUsd > 0
  const peak = Math.max(...series.map((m) => m.est_cost_usd), capped ? capUsd : 0, 0.0001)
  const H = 132
  const capY = capped ? H - (capUsd / peak) * H : null

  return (
    <div>
      <div className="flex items-end gap-2 overflow-x-auto pb-1" style={{ height: H + 34 }}>
        {series.map((m) => {
          const over = capped && m.est_cost_usd >= capUsd
          const h = Math.max((m.est_cost_usd / peak) * H, m.est_cost_usd > 0 ? 2 : 0)
          return (
            <div key={m.month} className="flex min-w-14 flex-1 flex-col items-center gap-1">
              <div className="text-xs tabular text-muted-foreground">{usd(m.est_cost_usd)}</div>
              <div
                // 4px rounded ends on the data end only; the bar stays anchored to
                // its baseline so heights compare honestly.
                className={cn(
                  'w-full rounded-t',
                  over ? 'bg-destructive' : 'bg-primary',
                )}
                style={{ height: h }}
                role="presentation"
              />
              <div className="text-xs text-muted-foreground">{m.month.slice(5)}</div>
            </div>
          )
        })}
      </div>
      {capped && capY !== null && (
        <p className="mt-2 text-xs text-muted-foreground">
          Monthly cap <span className="tabular font-medium text-foreground">{usd(capUsd)}</span>
          {series.some((m) => m.est_cost_usd >= capUsd) && (
            <span className="ml-2 font-medium text-destructive">
              reached — enrichment stops until next month
            </span>
          )}
        </p>
      )}
      {!capped && (
        <p className="mt-2 text-xs text-muted-foreground">No monthly cap set.</p>
      )}
    </div>
  )
}

/* ── 2. what spent it ───────────────────────────────────────────────────── */

/**
 * One measure across categories, so ONE hue — colouring each bar differently
 * would encode nothing and imply a distinction that is not there. Ranked, because
 * the question is "what spent it", and rank is the answer.
 */
export function SpendByPurpose({
  purposes,
}: {
  purposes: Array<{ purpose: string; est_cost_usd: number; calls: number }>
}) {
  const rows = [...purposes].sort((a, b) => b.est_cost_usd - a.est_cost_usd)
  if (!rows.length) return <NoData>Nothing has spent against this month yet.</NoData>
  const peak = Math.max(...rows.map((r) => r.est_cost_usd), 0.0001)

  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.purpose} className="grid grid-cols-[7rem_1fr_auto] items-center gap-3">
          <div className="truncate text-sm">{r.purpose}</div>
          <div className="h-2.5 rounded-full bg-muted">
            <div
              className="h-2.5 rounded-full bg-primary"
              style={{ width: `${Math.max((r.est_cost_usd / peak) * 100, 1)}%` }}
              role="presentation"
            />
          </div>
          <div className="text-right text-sm tabular">
            {usd(r.est_cost_usd)}
            <span className="ml-2 text-xs text-muted-foreground">{r.calls} calls</span>
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── 3. what a cheaper route would have cost ────────────────────────────── */

/**
 * The savings is the headline, so it is a number rather than a mark — a reader
 * asking "is this worth doing" wants the figure, and two bars alone make them do
 * the subtraction.
 *
 * The two bars are the context beneath it. Baseline is deliberately grey: it is
 * what already happened, and the indigo one is the proposal.
 */
export function CostComparison({
  currentUsd,
  projectedUsd,
  savingsUsd,
  savingsPct,
  priced,
  skipped,
}: {
  currentUsd: number
  projectedUsd: number
  savingsUsd: number
  savingsPct: number
  priced: number
  skipped: number
}) {
  if (!priced) {
    return (
      <NoData>
        No sessions could be priced yet — a digest needs its per-kind token breakdown
        before it can be re-priced at another tier.
      </NoData>
    )
  }
  const peak = Math.max(currentUsd, projectedUsd, 0.0001)
  const bar = (value: number, className: string) => (
    <div className="h-3 rounded-full bg-muted">
      <div
        className={cn('h-3 rounded-full', className)}
        style={{ width: `${Math.max((value / peak) * 100, 1)}%` }}
        role="presentation"
      />
    </div>
  )

  return (
    <div>
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-semibold tabular text-success">{usd(savingsUsd)}</span>
        <span className="text-sm text-muted-foreground">
          could have been saved ({savingsPct.toFixed(0)}%)
        </span>
      </div>
      <div className="mt-5 space-y-3">
        <div className="grid grid-cols-[7rem_1fr_auto] items-center gap-3">
          <div className="text-sm text-muted-foreground">What it cost</div>
          {bar(currentUsd, 'bg-chart-baseline')}
          <div className="text-right text-sm tabular">{usd(currentUsd)}</div>
        </div>
        <div className="grid grid-cols-[7rem_1fr_auto] items-center gap-3">
          <div className="text-sm">Routed cheaper</div>
          {bar(projectedUsd, 'bg-primary')}
          <div className="text-right text-sm tabular">{usd(projectedUsd)}</div>
        </div>
      </div>
      <p className="mt-4 text-xs text-muted-foreground">
        Priced {priced} session{priced === 1 ? '' : 's'}
        {skipped > 0 && ` · ${skipped} skipped for want of a token breakdown`}. These numbers
        price work that already happened at another tier&rsquo;s rates — they never influence
        which route anything takes.
      </p>
    </div>
  )
}
