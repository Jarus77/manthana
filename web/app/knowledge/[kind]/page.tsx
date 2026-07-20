'use client'

/**
 * Browse knowledge by kind, all-time.
 *
 * The gap this closes: the old wiki showed notes only through a seven-day home
 * feed and per-project pages, so a convention agreed two months ago was
 * effectively unreachable unless you remembered which project it belonged to.
 * Durable knowledge that expires from view is not durable.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { qs } from '@/lib/api'
import { Loading, Wiki } from '@/components/Loader'
import { Empty, NoteCard } from '@/components/primitives'
import { KIND_LABEL, type Note, type NoteKind, type Page } from '@/lib/types'
import { use } from 'react'

const STATUSES = [
  { value: '', label: 'Any status' },
  { value: 'candidate', label: 'Unreviewed' },
  { value: 'established', label: 'Established' },
  { value: 'disputed', label: 'Disputed' },
  { value: 'stale', label: 'Stale' },
]

function Browser({ kind }: { kind: string }) {
  const search = useSearchParams()
  const [status, setStatus] = useState(search.get('status') ?? '')
  const [cursors, setCursors] = useState<string[]>([''])
  const isAll = kind === 'all'
  const title = isAll ? 'All knowledge' : (KIND_LABEL[kind as NoteKind] ?? kind)

  return (
    <>
      <h1 style={{ marginBottom: 6 }}>{title}</h1>
      <p className="muted">
        Everything the team knows, oldest entries included — not just this week.
      </p>

      <div className="row" style={{ margin: '16px 0' }}>
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
            data.items.map((n) => <NoteCard key={n.id} note={n} />)
          )}
          {isLast && data.next_cursor && (
            <button style={{ marginTop: 12 }} onClick={() => onMore(data.next_cursor!)}>
              Load more
            </button>
          )}
        </>
      )}
    </Wiki>
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
