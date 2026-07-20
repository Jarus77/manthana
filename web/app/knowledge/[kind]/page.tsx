'use client'

/**
 * Browse entries by kind, all-time.
 *
 * The gap this closes: the feed only ever showed the last week, so a convention
 * agreed two months ago was unreachable unless you remembered its project.
 * Durable knowledge that expires from view is not durable.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, use, useState } from 'react'
import { qs } from '@/lib/api'
import { Loading, Wiki } from '@/components/Loader'
import { Empty, NoteRow, Title } from '@/components/primitives'
import { KIND_LABEL, type Note, type NoteKind, type Page } from '@/lib/types'

const STATUSES = [
  { value: '', label: 'Any status' },
  { value: 'candidate', label: 'Unreviewed' },
  { value: 'established', label: 'Established' },
  { value: 'disputed', label: 'Disputed' },
  { value: 'stale', label: 'Stale' },
]

function Chunk({
  kind,
  status,
  cursor,
  isLast,
  onMore,
}: {
  kind: string
  status: string
  cursor: string
  isLast: boolean
  onMore: (cursor: string) => void
}) {
  const path = `/notes${qs({ kind, status, until: cursor || undefined })}`
  return (
    <Wiki<Page<Note>> path={path}>
      {(data) => (
        <>
          {data.items.length === 0 && !cursor ? (
            <Empty>Nothing here yet.</Empty>
          ) : (
            <ul>
              {data.items.map((n) => (
                <NoteRow key={n.id} note={n} />
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

function Browser({ kind }: { kind: string }) {
  const search = useSearchParams()
  const [status, setStatus] = useState(search.get('status') ?? '')
  const [cursors, setCursors] = useState<string[]>([''])
  const isAll = kind === 'all'
  const title = isAll ? 'All entries' : (KIND_LABEL[kind as NoteKind] ?? kind)

  return (
    <>
      <Title>{title}</Title>
      <p className="lead">
        Everything the team knows{isAll ? '' : ` of this kind`}, oldest entries included — not
        just this week. Entries marked <span className="status-unreviewed">unreviewed</span>{' '}
        were written by Manthana and have not been checked by anyone.
      </p>

      <div style={{ margin: '0 0 1em' }}>
        <select
          style={{ width: 'auto' }}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setCursors([''])
          }}
        >
          {STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {cursors.map((cursor, i) => (
        <Chunk
          key={`${kind}|${status}|${cursor}`}
          kind={isAll ? '' : kind}
          status={status}
          cursor={cursor}
          isLast={i === cursors.length - 1}
          onMore={(next) => setCursors((prev) => [...prev, next])}
        />
      ))}
    </>
  )
}

export default function KnowledgePage({ params }: { params: Promise<{ kind: string }> }) {
  const { kind } = use(params)
  return (
    <Suspense fallback={<Loading />}>
      <Browser kind={kind} />
    </Suspense>
  )
}
