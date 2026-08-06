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
import { Loading } from '@/components/Loader'
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

      <p>
        Looks for skills the team keeps demonstrating and proposes them. Nothing is published
        until you approve it — mining only ever adds to the queue.
      </p>

      <p>
        <button
          type="button"
          className="button button-progressive"
          onClick={start}
          disabled={busy || running}
        >
          {running ? 'Mining…' : busy ? 'Starting…' : 'Mine org skills'}
        </button>
        {running && (
          <span className="subtle"> Running in the background — you can leave this page.</span>
        )}
      </p>

      {quota && <QuotaNotice quota={quota} />}
      {failure && <div className="error-box">{failure}</div>}

      {isLoading ? (
        <Loading />
      ) : !run ? (
        <div className="empty">
          No run yet in this server process. Run state is not persisted — after a restart this
          is what you see, even if a run happened earlier.
        </div>
      ) : (
        <>
          <h2>Last run</h2>
          <p>
            {run.state === 'running' && <span className="status-unreviewed">running</span>}
            {run.state === 'quota' && (
              <span className="status-disputed">stopped — budget spent</span>
            )}
            {run.state === 'failed' && <span className="status-disputed">failed</span>}
            {!['running', 'quota', 'failed'].includes(run.state) && (
              <span className="status-confirmed">done</span>
            )}
            {run.detail && <span className="subtle"> {run.detail}</span>}
          </p>

          {/* No silent caps: when a bound bit, say so loudly — a partial run
              must never be mistaken for a complete one. */}
          <p className={run.capped ? 'status-unreviewed' : 'subtle'}>{run.coverage_note}</p>

          <div className="stat-row">
            {[
              ['Started', run.started_at.slice(0, 16).replace('T', ' ')],
              ['Finished', run.finished_at?.slice(0, 16).replace('T', ' ') ?? '—'],
              ['Window', `${run.window_days} days`],
              ['Proposed', String(run.queued)],
            ].map(([k, v]) => (
              <div key={k} className="stat">
                <span className="stat-value">{v}</span>
                <span className="stat-label">{k}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  )
}
