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
import { Mono, Notice, SectionHeading } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
        <div className="mb-6">
          <Notice tone="disputed" title="This org has reached its monthly AI budget.">
            Enrichment has stopped, so new sessions will stay unsummarised until the quota
            resets at the start of next month. Nothing else breaks, and nothing is lost —
            but the wiki will look stalled until then.
          </Notice>
        </div>
      )}

      {/* ── Manthana's own spend ─────────────────────────────────────── */}
      <SectionHeading>Server AI spend</SectionHeading>
      <p className="mb-5 max-w-2xl text-sm text-muted-foreground">
        What Manthana spent turning this org&rsquo;s sessions into digests, notes, and
        narratives. Metered against the monthly cap.
      </p>
      {!usage ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="rounded-lg border p-5">
            <h3 className="mb-4 text-sm font-medium">By month</h3>
            <SpendByMonth months={usage.months} capUsd={usage.cap_usd} />
          </div>
          <div className="rounded-lg border p-5">
            <h3 className="mb-1 text-sm font-medium">What spent it, this month</h3>
            <p className="mb-4 text-xs text-muted-foreground">
              {usage.cap_is_override
                ? 'This org has its own cap, set by the operator.'
                : 'Using the server default cap.'}
            </p>
            <SpendByPurpose purposes={usage.purposes} />
          </div>
        </div>
      )}

      {/* ── the team's session cost ──────────────────────────────────── */}
      <SectionHeading>What your sessions cost</SectionHeading>
      <p className="mb-5 max-w-2xl text-sm text-muted-foreground">
        Your engineers&rsquo; own coding sessions, re-priced at one tier down wherever that
        looked safe. An observation about work that already happened — Manthana does not
        route anything.
      </p>
      {!cost ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <>
          <div className="rounded-lg border p-5">
            <CostComparison
              currentUsd={cost.current_usd}
              projectedUsd={cost.projected_usd}
              savingsUsd={cost.savings_usd}
              savingsPct={cost.savings_pct}
              priced={cost.priced}
              skipped={cost.skipped_no_tokens}
            />
          </div>

          {cost.rows.length > 0 && (
            <>
              <h3 className="mt-8 mb-3 text-sm font-medium">Session by session</h3>
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Project</TableHead>
                      <TableHead>Ran on</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead>Could have run on</TableHead>
                      <TableHead className="text-right">Would save</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {cost.rows.slice(0, 50).map((r) => (
                      <TableRow key={r.id}>
                        <TableCell>{r.project || '—'}</TableCell>
                        <TableCell>
                          <Mono>{r.tier}</Mono>
                        </TableCell>
                        <TableCell className="text-right tabular">
                          {usd(r.current_usd)}
                        </TableCell>
                        <TableCell>
                          {r.safe_to_downgrade && r.target_tier ? (
                            <Mono>{r.target_tier}</Mono>
                          ) : (
                            <span className="text-muted-foreground">stays where it is</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular">
                          {r.savings_usd > 0 ? (
                            <span className="text-success">{usd(r.savings_usd)}</span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {cost.rows.length > 50 && (
                <p className="mt-2 text-xs text-muted-foreground">
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
