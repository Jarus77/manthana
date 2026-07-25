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
import { EmptyState, StatusText } from '@/components/manthana'
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

  if (isLoading || !data) return <Skeleton className="h-64 w-full" />

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

      <div className="mb-5 flex flex-wrap gap-2">
        <select
          aria-label="Project"
          className="h-9 rounded-md border bg-background px-2 text-sm"
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
            className="h-9 rounded-md border bg-background px-2 text-sm"
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
        <EmptyState hint="Sessions appear once an engineer runs `manthana setup` and releases work.">
          No sessions match.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                {data.named && <TableHead>Engineer</TableHead>}
                <TableHead>Project</TableHead>
                <TableHead>What they set out to do</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.sessions.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {s.started_at.slice(0, 16).replace('T', ' ')}
                  </TableCell>
                  {data.named && (
                    <TableCell>{(s.actor ?? '').split('@')[0]}</TableCell>
                  )}
                  <TableCell className="text-muted-foreground">{s.project || '—'}</TableCell>
                  <TableCell className="max-w-md">
                    <Link
                      className="text-primary underline-offset-4 hover:underline"
                      href={`/console/sessions/${encodeURIComponent(s.id)}${qs({ org })}`}
                    >
                      <span className="line-clamp-1">{s.task_intent}</span>
                    </Link>
                  </TableCell>
                  <TableCell>
                    {s.outcome === 'success' ? (
                      <StatusText tone="distilled">success</StatusText>
                    ) : (
                      <StatusText tone="muted">{s.outcome}</StatusText>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {data.sessions.length >= data.limit && (
        <p className="mt-2 text-xs text-muted-foreground">
          Showing the {data.limit} most recent. Filter by project to narrow it.
        </p>
      )}
    </>
  )
}
