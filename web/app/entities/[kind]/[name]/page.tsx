'use client'

/**
 * Everything the wiki knows about one file, library or concept.
 *
 * Reads the `mentions` edges written when a note is consolidated or taught.
 * Worth stating plainly: the consolidator has been extracting `libraries` and
 * `concepts` from every note since v1, and until now nothing in the system read
 * them — they were written to storage and forgotten. This page is their first
 * consumer, which is why "what do we know about torch" was previously
 * unanswerable despite the data existing.
 */

import { use } from 'react'
import { Wiki } from '@/components/Loader'
import { Empty, NoteRow, Title } from '@/components/primitives'
import type { Note } from '@/lib/types'

interface EntityPage {
  kind: string
  name: string
  notes: Note[]
  org_id: string
}

const LABEL: Record<string, string> = {
  file: 'file',
  library: 'library',
  concept: 'concept',
  project: 'project',
}

export default function EntityArticle({
  params,
}: {
  params: Promise<{ kind: string; name: string }>
}) {
  const { kind, name } = use(params)
  const decoded = decodeURIComponent(name)

  return (
    <Wiki<EntityPage>
      path={`/entities/${encodeURIComponent(kind)}/${encodeURIComponent(decoded)}`}
    >
      {(data) => (
        <>
          <Title tagline={`A ${LABEL[data.kind] ?? data.kind} referenced in the Manthana wiki`}>
            {data.name}
          </Title>

          <p className="lead">
            <b className="mono">{data.name}</b> is a {LABEL[data.kind] ?? data.kind} named by{' '}
            <b>{data.notes.length}</b> entr{data.notes.length === 1 ? 'y' : 'ies'} in this wiki.
            Entries are linked here when Manthana extracts the {LABEL[data.kind] ?? data.kind}{' '}
            from the session that produced them.
          </p>

          <h2>Entries</h2>
          {data.notes.length ? (
            <ul>
              {data.notes.map((n) => (
                <NoteRow key={n.id} note={n} />
              ))}
            </ul>
          ) : (
            <Empty>
              Nothing references this yet. Entries appear once a session naming it has been
              consolidated.
            </Empty>
          )}
        </>
      )}
    </Wiki>
  )
}
