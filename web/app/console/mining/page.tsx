'use client'

/**
 * Skill mining — proposals, never publications.
 *
 * The run is asynchronous and its state is in-process, deliberately not persisted:
 * this is progress feedback for a click, and the durable artifacts are the queued
 * proposals. So "no run yet" after a restart is ordinary, and the page says so
 * rather than showing an error.
 *
 * Polling only while a run is actually in flight. A page that polls forever is a
 * page that keeps a laptop awake for nothing.
 */

import { useState } from 'react'
import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { QuotaNotice, quotaFrom, type QuotaDetail } from '@/components/console/QuotaNotice'
import { EmptyState, Notice, SectionHeading, StatusText } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, consoleFetcher, consolePost, qs } from '@/lib/api'

type Status = {
  org_id: string
  pending_proposals: number
  run: {
    state: string
    detail: string
    started_at: string
    finished_at: string | null
    window_days: number
    since: string | null
    matched: number
    scanned: number
    max_items: number
    queued: number
    capped: boolean
    coverage_note: string
  } | null
}

export default function MiningPage() {
  const org = useOrgId()
  const [busy, setBusy] = useState(false)
  const [quota, setQuota] = useState<QuotaDetail | null>(null)
  const [failure, setFailure] = useState('')

  const { data, isLoading, mutate } = useSWR<Status, ApiError>(
    org ? `/mine-status${qs({ org_id: org })}` : null,
    consoleFetcher,
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
      // Only while something is actually running.
      refreshInterval: (latest) => (latest?.run?.state === 'running' ? 3000 : 0),
    },
  )

  async function start() {
    setBusy(true)
    setQuota(null)
    setFailure('')
    try {
      await consolePost('/mine', { org_id: org })
      await mutate()
    } catch (err) {
      const q = quotaFrom(err)
      if (q) setQuota(q)
      else setFailure(err instanceof ApiError ? err.message : 'Could not start a run.')
    }
    setBusy(false)
  }

  const run = data?.run
  const running = run?.state === 'running'

  return (
    <>
      <PageTitle
        aside={
          data ? `${data.pending_proposals} proposal(s) awaiting approval` : undefined
        }
      >
        Skill mining
      </PageTitle>

      <p className="mb-6 max-w-2xl text-sm text-muted-foreground">
        Looks for skills the team keeps demonstrating and proposes them. Nothing is published
        until you approve it — mining only ever adds to the queue.
      </p>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Button onClick={start} disabled={busy || running}>
          {running ? 'Mining…' : busy ? 'Starting…' : 'Mine org skills'}
        </Button>
        {running && (
          <span className="text-sm text-muted-foreground">
            Running in the background — you can leave this page.
          </span>
        )}
      </div>

      {quota && (
        <div className="mb-6">
          <QuotaNotice quota={quota} />
        </div>
      )}
      {failure && (
        <div className="mb-6">
          <Notice tone="disputed">{failure}</Notice>
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !run ? (
        <EmptyState>
          No run yet in this server process. Run state is not persisted — after a restart
          this is what you see, even if a run happened earlier.
        </EmptyState>
      ) : (
        <>
          <SectionHeading>Last run</SectionHeading>
          <div className="rounded-lg border p-5">
            <p className="text-sm">
              {run.state === 'running' && <StatusText tone="warning">running</StatusText>}
              {run.state === 'quota' && (
                <StatusText tone="danger">stopped — budget spent</StatusText>
              )}
              {run.state === 'failed' && <StatusText tone="danger">failed</StatusText>}
              {!['running', 'quota', 'failed'].includes(run.state) && (
                <StatusText tone="distilled">done</StatusText>
              )}
              {run.detail && <span className="ml-2 text-muted-foreground">{run.detail}</span>}
            </p>

            {/* No silent caps: when a bound bit, say so loudly — a partial run
                must never be mistaken for a complete one. */}
            <p
              className={`mt-3 text-sm ${run.capped ? 'text-warning' : 'text-muted-foreground'}`}
            >
              {run.coverage_note}
            </p>

            <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
              {[
                ['Started', run.started_at.slice(0, 16).replace('T', ' ')],
                ['Finished', run.finished_at?.slice(0, 16).replace('T', ' ') ?? '—'],
                ['Window', `${run.window_days} days`],
                ['Proposed', String(run.queued)],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="tabular">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </>
      )}
    </>
  )
}
