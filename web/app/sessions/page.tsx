'use client'

/**
 * Org-wide session index.
 *
 * New to engineers when the client shipped: the old wiki linked session cards
 * at founder-gated routes, which bounced any engineer straight back to home. A
 * released digest was already published to the team; this is where the team
 * reads it.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import useSWR from 'swr'
import { fetcher, qs } from '@/lib/api'
import { Loading, Wiki } from '@/components/Loader'
import { Empty, SessionRow, Title } from '@/components/primitives'
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
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '0 0 1em' }}>
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

function Chunk({
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
            <ul>
              {data.items.map((s) => (
                <SessionRow key={s.id} session={s} />
              ))}
            </ul>
          )}
          {isLast && data.next_cursor && (
            <button onClick={() => onMore(data.next_cursor!)}>Load more</button>
          )}
        </>
      )}
    </Wiki>
  )
}

function Browser() {
  const search = useSearchParams()
  const [actor, setActor] = useState(search.get('actor') ?? '')
  const [project, setProject] = useState(search.get('project') ?? '')
  const [cursors, setCursors] = useState<string[]>([''])

  return (
    <>
      <Title>Recent sessions</Title>
      <p className="lead">
        Every session digest released to this wiki, newest first. Digests record what an
        engineer set out to do, how they approached it and how it went — the raw transcripts
        are not part of the wiki.
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
        <Chunk
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

export default function SessionsIndexPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Browser />
    </Suspense>
  )
}
