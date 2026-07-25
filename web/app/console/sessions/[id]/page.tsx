'use client'

/**
 * One session's digest.
 *
 * Never the raw turns. The console shows digests only; reading the original
 * transcript goes through an audited endpoint, which is what makes "your founder
 * could read this" an answerable question rather than a worry.
 */

import Link from 'next/link'
import { use } from 'react'
import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { usd } from '@/components/console/charts'
import { EmptyState, FactCard, Mono, Notice, SectionHeading } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, consoleFetcher, qs } from '@/lib/api'

type Detail = {
  org_id: string
  named: boolean
  id: string
  session_id: string
  started_at: string
  project: string
  surface: string
  outcome: string
  task_intent: string
  approach: string
  est_cost_usd: number
  friction_points: Array<{ category: string; description: string }>
  files_touched: string[]
  actor?: string
}

export default function SessionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const org = useOrgId()
  const { data, error, isLoading } = useSWR<Detail, ApiError>(
    org ? `/sessions/${encodeURIComponent(id)}${qs({ org_id: org })}` : null,
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (error?.status === 404) {
    return (
      <Notice tone="disputed" title="Not found in this organization.">
        The session may have been purged, or it belongs to a different org.{' '}
        <Link className="text-primary underline-offset-4 hover:underline" href="/console/sessions">
          Back to sessions
        </Link>
        .
      </Notice>
    )
  }
  if (isLoading || !data) return <Skeleton className="h-64 w-full" />

  return (
    <>
      <PageTitle>{data.task_intent}</PageTitle>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="order-2 lg:order-1">
          <SectionHeading>Approach</SectionHeading>
          {data.approach ? (
            <p className="whitespace-pre-wrap text-sm">{data.approach}</p>
          ) : (
            <EmptyState>Not recorded for this session.</EmptyState>
          )}

          <SectionHeading>Friction</SectionHeading>
          {data.friction_points.length ? (
            <ul className="space-y-2 text-sm">
              {data.friction_points.map((f, i) => (
                <li key={i}>
                  <span className="font-medium">{f.category}</span>
                  <span className="text-muted-foreground"> — {f.description}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>None recorded.</EmptyState>
          )}

          <SectionHeading>Files touched</SectionHeading>
          {data.files_touched.length ? (
            <ul className="space-y-1 text-sm">
              {data.files_touched.map((f) => (
                <li key={f}>
                  <Mono>{f}</Mono>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>None recorded.</EmptyState>
          )}
        </div>

        <div className="order-1 lg:order-2">
          <FactCard
            title="Session"
            rows={[
              ['When', data.started_at.slice(0, 16).replace('T', ' ')],
              ['Project', data.project || '—'],
              ...(data.named && data.actor
                ? ([['Engineer', data.actor]] as Array<[string, React.ReactNode]>)
                : []),
              ['Outcome', data.outcome],
              ['Surface', data.surface],
              ['Cost', <Mono key="c">{usd(data.est_cost_usd)}</Mono>],
              ['Session id', <Mono key="s">{data.session_id}</Mono>],
            ]}
          />
          <p className="mt-3 text-xs text-muted-foreground">
            Cost is the API list-price equivalent for this session, not a bill.
          </p>
        </div>
      </div>

      <p className="mt-10">
        <Link
          className="text-sm text-primary underline-offset-4 hover:underline"
          href={`/console/sessions${qs({ org })}`}
        >
          ← All sessions
        </Link>
      </p>
    </>
  )
}
