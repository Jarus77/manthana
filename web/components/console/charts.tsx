'use client'

/**
 * The console's charts, as bars on the encyclopedia stylesheet.
 *
 * No charting library: there are three charts, all simple, and a dependency would
 * cost ~100 kB and bring a second styling model to reconcile with the sheet.
 *
 * COLOUR WAS MEASURED, NOT JUDGED — the same discipline these carried on the
 * design system, re-run because the palette changed. Every pair that can appear
 * adjacent, against the white article surface (OKLab ΔE ×100, min protan/deutan):
 *
 *   baseline vs proposal   #54595d ↔ #36c   CVD 17.8 · normal 17.5
 *   over-cap vs under-cap  #bf3c2c ↔ #36c   CVD 24.6 · normal 30.4
 *
 * The validator flags the grey baseline as "reads gray" — below its chroma floor.
 * Intended: a baseline bar is the BEFORE state, not a series identity, and grey
 * is what says so. Same check, knowingly waived, as before the move.
 *
 * One theme to validate instead of two is the single thing this sheet makes
 * easier here. Dark was where the near-miss lived: slate-400 ↔ indigo-400
 * measured ΔE 13.0, under the 15 floor, and it was invisible by eye.
 *
 * Every chart is a single measure, so none carries a legend — the heading names
 * the series, and a legend box for one thing is noise. Values are direct-labelled
 * rather than axis-read, since these are short series a reader wants exact
 * numbers from.
 */

export function usd(n: number): string {
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(4)}`
}

/** Shared empty state, so a chart with no data never renders as a broken axis. */
function NoData({ children }: { children: React.ReactNode }) {
  return <div className="chart-nodata">{children}</div>
}

/* ── 1. spend by month, against the cap ─────────────────────────────────── */

/**
 * Answers "am I about to be throttled", which today has no other symptom — a hit
 * cap just stops enrichment, and the wiki quietly fills with unsummarised sessions
 * that read as a product bug.
 *
 * The cap is stated in words beneath rather than drawn as a line: it is a
 * threshold, not a measurement, and a rule across the plot invites reading it as
 * more spend. Months at or over it turn red, so the chart states the condition
 * rather than leaving it to be inferred from a crossing.
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

  return (
    <div>
      <div className="chart-columns" style={{ height: H + 40 }}>
        {series.map((m) => {
          const over = capped && m.est_cost_usd >= capUsd
          const h = Math.max((m.est_cost_usd / peak) * H, m.est_cost_usd > 0 ? 2 : 0)
          return (
            <div key={m.month} className="chart-col">
              <div className="chart-caption">{usd(m.est_cost_usd)}</div>
              {/* Anchored to the baseline, so heights compare honestly. */}
              <div
                className={over ? 'chart-bar chart-bar-over' : 'chart-bar'}
                style={{ height: h }}
                role="presentation"
              />
              <div className="chart-caption">{m.month.slice(5)}</div>
            </div>
          )
        })}
      </div>
      {capped ? (
        <p className="subtle">
          Monthly cap <b className="tabular">{usd(capUsd)}</b>
          {series.some((m) => m.est_cost_usd >= capUsd) && (
            <span className="status-disputed">
              {' '}
              — reached; enrichment stops until next month
            </span>
          )}
        </p>
      ) : (
        <p className="subtle">No monthly cap set.</p>
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
    <div>
      {rows.map((r) => (
        <div key={r.purpose} className="chart-row">
          <div>{r.purpose}</div>
          <div className="chart-track">
            <div
              className="chart-fill"
              style={{ width: `${Math.max((r.est_cost_usd / peak) * 100, 1)}%` }}
              role="presentation"
            />
          </div>
          <div className="chart-value">
            {usd(r.est_cost_usd)} <span className="faint">{r.calls} calls</span>
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
 * what already happened, and the blue one is the proposal.
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
  const bar = (value: number, extra?: string) => (
    <div className="chart-track">
      <div
        className={extra ? `chart-fill ${extra}` : 'chart-fill'}
        style={{ width: `${Math.max((value / peak) * 100, 1)}%` }}
        role="presentation"
      />
    </div>
  )

  return (
    <div>
      <p>
        <span className="chart-headline">{usd(savingsUsd)}</span>{' '}
        <span className="subtle">could have been saved ({savingsPct.toFixed(0)}%)</span>
      </p>
      <div className="chart-row">
        <div className="subtle">What it cost</div>
        {bar(currentUsd, 'chart-fill-baseline')}
        <div className="chart-value">{usd(currentUsd)}</div>
      </div>
      <div className="chart-row">
        <div>Routed cheaper</div>
        {bar(projectedUsd)}
        <div className="chart-value">{usd(projectedUsd)}</div>
      </div>
      <p className="faint">
        Priced {priced} session{priced === 1 ? '' : 's'}
        {skipped > 0 && ` · ${skipped} skipped for want of a token breakdown`}. These numbers
        price work that already happened at another tier&rsquo;s rates — they never influence
        which route anything takes.
      </p>
    </div>
  )
}
