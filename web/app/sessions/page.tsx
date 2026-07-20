'use client'

/**
 * Org-wide session browser.
 *
 * New to engineers: the old wiki linked session cards at the founder console's
 * routes, which bounced any engineer straight back to home — so a colleague's
 * work was visible as an aggregate and never as itself. A released digest was
 * already published to the team; this is where the team reads it.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import useSWR from 'swr'
import { fetcher, qs } from '@/lib/api'
import { Loading, Wiki } from '@/components/Loader'
import { Empty, StreamItem } from '@/components/primitives'
import type { ActorActivity, Page, Session } from '@/lib/types'

function Filters({
  actor,
  project,
  onChange,
}: {
  actor: string
  project: string
  onChange: (next: { actor?: string; project?: string }) => void
}) {
  const { data: people } = useSWR<{ active: ActorActivity[] }>('/people', fetcher, {
    revalidateOnFocus: false,
  })
  const { data: projects } = useSWR<{ active: Array<{ project: string }>; quiet: string[] }>(
    '/projects',
    fetcher,
    { revalidateOnFocus: false },
  )
  const projectNames = [
    ...(projects?.active ?? []).map((p) => p.project),
    ...(projects?.quiet ?? []),
  ]

  return (
    <div className="row" style={{ marginBottom: 16 }}>
      <select
        style={{ width: 'auto' }}
        value={actor}
        onChange={(e) => onChange({ actor: e.target.value })}
      >
        <option value="">Everyone</option>
        {(people?.active ?? []).map((p) => (
          <option key={p.actor} value={p.actor}>
            {p.actor}
          </option>
        ))}
      </select>
      <select
        style={{ width: 'auto' }}
        value={project}
        onChange={(e) => onChange({ project: e.target.value })}
      >
        <option value="">All projects</option>
        {projectNames.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      {(actor || project) && (
        <button onClick={() => onChange({ actor: '', project: '' })}>Clear</button>
      )}
    </div>
  )
}

function SessionsBrowser() {
  const search = useSearchParams()
  const [actor, setActor] = useState(search.get('actor') ?? '')
  const [project, setProject] = useState(search.get('project') ?? '')
  // Pages accumulate rather than replace: "load more" should extend the list
  // you are reading, not scroll it out from under you.
  const [cursors, setCursors] = useState<string[]>([''])

  return (
    <>
      <h1 style={{ marginBottom: 6 }}>Sessions</h1>
      <p className="muted">
        Every released session digest in the org. Raw transcripts are never shown here.
      </p>

      <Filters
        actor={actor}
        project={project}
        onChange={(next) => {
          if (next.actor !== undefined) setActor(next.actor)
          if (next.project !== undefined) setProject(next.project)
          setCursors([''])
        }}
      />

      {cursors.map((cursor, i) => (
        <SessionPageChunk
          key={`${actor}|${project}|${cursor}`}
          actor={actor}
          project={project}
          cursor={cursor}
          isLast={i === cursors.length - 1}
          onMore={(next) => setCursors((prev) => [...prev, next])}
        />
      ))}
    </>
  )
}

function SessionPageChunk({
  actor,
  project,
  cursor,
  isLast,
  onMore,
}: {
  actor: string
  project: string
  cursor: string
  isLast: boolean
  onMore: (cursor: string) => void
}) {
  const path = `/sessions${qs({ actor, project, until: cursor || undefined })}`
  return (
    <Wiki<Page<Session>> path={path}>
      {(data) => (
        <>
          {data.items.length === 0 && !cursor ? (
            <Empty>No sessions match.</Empty>
          ) : (
            data.items.map((s) => <StreamItem key={s.id} session={s} />)
          )}
          {isLast && data.next_cursor && (
            <button style={{ marginTop: 16 }} onClick={() => onMore(data.next_cursor!)}>
              Load more
            </button>
          )}
        </>
      )}
    </Wiki>
  )
}

export default function SessionsPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <Suspense fallback={<Loading />}>
      <SessionsBrowser />
    </Suspense>
  )
}
