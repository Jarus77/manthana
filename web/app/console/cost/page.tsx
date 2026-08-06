'use client'

/**
 * Cost — the only genuinely quantitative page in the product.
 *
 * Two different questions, deliberately kept apart rather than merged into one
 * chart with two scales:
 *
 *   SERVER AI SPEND is Manthana's own bill for enriching and consolidating, metered
 *   against a monthly cap. This is the one that can stop the product working.
 *
 *   SESSION COST is what the team's own coding sessions cost, re-priced at a
 *   cheaper tier to show what routing would have saved. It is an observation, not
 *   a bill, and nothing acts on it.
 *
 * Putting them on one axis would be the classic dual-scale mistake — they are
 * different measures that happen to share a unit.
 */

import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { CostComparison, SpendByMonth, SpendByPurpose, usd } from '@/components/console/charts'
import { Loading } from '@/components/Loader'
import { ApiError, consoleFetcher, qs } from '@/lib/api'

type Usage = {
  org_id: string
  month: string
  spent_usd: number
  cap_usd: number
  cap_is_override: boolean
  blocked: boolean
  purposes: Array<{ purpose: string; calls: number; est_cost_usd: number }>
  months: Array<{ month: string; calls: number; est_cost_usd: number }>
}

type Cost = {
  org_id: string
  sessions: number
  priced: number
  skipped_no_tokens: number
  current_usd: number
  projected_usd: number
  savings_usd: number
  savings_pct: number
  by_target: Record<string, number>
  rows: Array<{
    id: string
    project: string
    tier: string
    current_usd: number
    safe_to_downgrade: boolean
    target_tier: string | null
    savings_usd: number
  }>
}

export default function CostPage() {
  const org = useOrgId()
  const opts = { revalidateOnFocus: false, shouldRetryOnError: false }
  const { data: usage } = useSWR<Usage, ApiError>(
    org ? `/usage${qs({ org_id: org })}` : null, consoleFetcher, opts,
  )
  const { data: cost } = useSWR<Cost, ApiError>(
    org ? `/cost${qs({ org_id: org })}` : null, consoleFetcher, opts,
  )

  return (
    <>
      <PageTitle aside={usage ? `month ${usage.month}` : undefined}>Cost</PageTitle>

      {usage?.blocked && (
        <div className="ambox ambox-serious">
          <b>This org has reached its monthly AI budget.</b>
          <p>
            Enrichment has stopped, so new sessions will stay unsummarised until the quota
            resets at the start of next month. Nothing else breaks, and nothing is lost — but
            the wiki will look stalled until then.
          </p>
        </div>
      )}

      {/* ── Manthana's own spend ─────────────────────────────────────── */}
      <h2>Server AI spend</h2>
      <p>
        What Manthana spent turning this org&rsquo;s sessions into digests, notes, and
        narratives. Metered against the monthly cap.
      </p>
      {!usage ? (
        <Loading />
      ) : (
        <div className="panel-grid">
          <div>
            <h3>By month</h3>
            <SpendByMonth months={usage.months} capUsd={usage.cap_usd} />
          </div>
          <div>
            <h3>What spent it, this month</h3>
            <p className="faint">
              {usage.cap_is_override
                ? 'This org has its own cap, set by the operator.'
                : 'Using the server default cap.'}
            </p>
            <SpendByPurpose purposes={usage.purposes} />
          </div>
        </div>
      )}

      {/* ── the team's session cost ──────────────────────────────────── */}
      <h2>What your sessions cost</h2>
      <p>
        Your engineers&rsquo; own coding sessions, re-priced at one tier down wherever that
        looked safe. An observation about work that already happened — Manthana does not
        route anything.
      </p>
      {!cost ? (
        <Loading />
      ) : (
        <>
          <CostComparison
            currentUsd={cost.current_usd}
            projectedUsd={cost.projected_usd}
            savingsUsd={cost.savings_usd}
            savingsPct={cost.savings_pct}
            priced={cost.priced}
            skipped={cost.skipped_no_tokens}
          />

          {cost.rows.length > 0 && (
            <>
              <h3>Session by session</h3>
              <div className="scroll-x">
                <table className="wikitable">
                  <thead>
                    <tr>
                      <th>Project</th>
                      <th>Ran on</th>
                      <th>Cost</th>
                      <th>Could have run on</th>
                      <th>Would save</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cost.rows.slice(0, 50).map((r) => (
                      <tr key={r.id}>
                        <td>{r.project || '—'}</td>
                        <td className="mono">{r.tier}</td>
                        <td className="tabular">{usd(r.current_usd)}</td>
                        <td>
                          {r.safe_to_downgrade && r.target_tier ? (
                            <span className="mono">{r.target_tier}</span>
                          ) : (
                            <span className="subtle">stays where it is</span>
                          )}
                        </td>
                        <td className="tabular">
                          {r.savings_usd > 0 ? (
                            <span className="status-confirmed">{usd(r.savings_usd)}</span>
                          ) : (
                            <span className="subtle">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {cost.rows.length > 50 && (
                <p className="faint">
                  Showing the 50 most expensive of {cost.rows.length}.
                </p>
              )}
            </>
          )}
        </>
      )}
    </>
  )
}
