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
import { Infobox } from '@/components/primitives'
import { Loading } from '@/components/Loader'
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
      <div className="ambox ambox-serious">
        <b>Not found in this organization.</b>
        <p>
          The session may have been purged, or it belongs to a different org.{' '}
          <Link href="/console/sessions">Back to sessions</Link>.
        </p>
      </div>
    )
  }
  if (isLoading || !data) return <Loading />

  return (
    <>
      <PageTitle>{data.task_intent}</PageTitle>

      {/* The same right-floated fact table an article gets — a session digest IS
          a subject with key facts, and it should read like one. */}
      <Infobox
        title="Session"
        subtitle={data.project || undefined}
        rows={[
          ['When', data.started_at.slice(0, 16).replace('T', ' ')],
          ['Project', data.project || '—'],
          ...(data.named && data.actor
            ? ([['Engineer', data.actor]] as Array<[string, React.ReactNode]>)
            : []),
          ['Outcome', data.outcome],
          ['Surface', data.surface],
          ['Cost', <span key="c" className="mono">{usd(data.est_cost_usd)}</span>],
          ['Session id', <span key="s" className="mono">{data.session_id}</span>],
        ]}
      />

      <h2>Approach</h2>
      {data.approach ? (
        <p style={{ whiteSpace: 'pre-wrap' }}>{data.approach}</p>
      ) : (
        <div className="empty">Not recorded for this session.</div>
      )}

      <h2>Friction</h2>
      {data.friction_points.length ? (
        <ul>
          {data.friction_points.map((f, i) => (
            <li key={i}>
              <b>{f.category}</b>
              <span className="subtle"> — {f.description}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty">None recorded.</div>
      )}

      <h2>Files touched</h2>
      {data.files_touched.length ? (
        <ul>
          {data.files_touched.map((f) => (
            <li key={f} className="mono">
              {f}
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty">None recorded.</div>
      )}

      <div className="clear" />
      <p className="faint">
        Cost is the API list-price equivalent for this session, not a bill.
      </p>
      <p>
        <Link href={`/console/sessions${qs({ org })}`}>← All sessions</Link>
      </p>
    </>
  )
}
