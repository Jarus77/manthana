'use client'

/**
 * The session browser — the founder-facing answer to "let me actually read what my
 * team did".
 *
 * Digests only. Raw transcripts are never on this path; that drill-down is an
 * audited endpoint of its own, on purpose.
 *
 * The engineer column appears only when the server says `named`. On a de-identified
 * org the actor is not sent at all, so there is nothing here to accidentally
 * render — the filter simply is not offered.
 */

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { Loading } from '@/components/Loader'
import { ApiError, consoleFetcher, qs } from '@/lib/api'

type SessionRow = {
  id: string
  started_at: string
  project: string
  task_intent: string
  outcome: string
  actor?: string
}
type Listing = {
  org_id: string
  named: boolean
  limit: number
  projects: string[]
  engineers: Array<{ id: string; display_name: string }>
  sessions: SessionRow[]
}

export default function SessionsPage() {
  const org = useOrgId()
  const params = useSearchParams()
  const router = useRouter()
  const project = params.get('project') ?? ''
  const engineer = params.get('engineer') ?? ''

  const { data, isLoading } = useSWR<Listing, ApiError>(
    org ? `/sessions${qs({ org_id: org, project, engineer })}` : null,
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params.toString())
    if (value) next.set(key, value)
    else next.delete(key)
    router.push(`/console/sessions?${next.toString()}`)
  }

  if (isLoading || !data) return <Loading />

  return (
    <>
      <PageTitle
        aside={
          <>
            {data.sessions.length} session{data.sessions.length === 1 ? '' : 's'} · digests only
            {!data.named && ' · de-identified'}
          </>
        }
      >
        Sessions
      </PageTitle>

      <div className="form-row">
        <select
          aria-label="Project"
          value={project}
          onChange={(e) => setFilter('project', e.target.value)}
        >
          <option value="">All projects</option>
          {data.projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {data.named && (
          <select
            aria-label="Engineer"
            value={engineer}
            onChange={(e) => setFilter('engineer', e.target.value)}
          >
            <option value="">All engineers</option>
            {data.engineers.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))}
          </select>
        )}
      </div>

      {data.sessions.length === 0 ? (
        <div className="empty">
          No sessions match. Sessions appear once an engineer runs{' '}
          <span className="mono">manthana setup</span> and releases work.
        </div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>When</th>
                {data.named && <th>Engineer</th>}
                <th>Project</th>
                <th>What they set out to do</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {data.sessions.map((s) => (
                <tr key={s.id}>
                  <td style={{ whiteSpace: 'nowrap' }} className="subtle">
                    {s.started_at.slice(0, 16).replace('T', ' ')}
                  </td>
                  {data.named && <td>{(s.actor ?? '').split('@')[0]}</td>}
                  <td className="subtle">{s.project || '—'}</td>
                  <td>
                    <Link href={`/console/sessions/${encodeURIComponent(s.id)}${qs({ org })}`}>
                      {s.task_intent}
                    </Link>
                  </td>
                  <td>
                    {s.outcome === 'success' ? (
                      <span className="status-confirmed">success</span>
                    ) : (
                      <span className="subtle">{s.outcome}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.sessions.length >= data.limit && (
        <p className="faint">
          Showing the {data.limit} most recent. Filter by project to narrow it.
        </p>
      )}
    </>
  )
}
