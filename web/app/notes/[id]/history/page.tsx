'use client'

/**
 * Revision history, in MediaWiki's idiom: newest first, one line per revision,
 * with the action to restore an old one beside it.
 *
 * Append-only — restoring publishes the old text as a NEW revision rather than
 * rewinding, so what was once published (including a bad AI claim someone had
 * to correct) stays on the record.
 */

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { use, useState } from 'react'
import { post } from '@/lib/api'
import { Wiki } from '@/components/Loader'
import { Hatnote, Markdown, Title, onDate, statusWord } from '@/components/primitives'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { Note } from '@/lib/types'

export default function NoteHistory({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const [busy, setBusy] = useState('')

  return (
    <Wiki<{ versions: Note[] }> path={`/notes/${encodeURIComponent(id)}/history`}>
      {(data) => {
        const versions = [...data.versions].sort((a, b) => b.version - a.version)
        const current = versions[0]
        return (
          <>
            <Title tagline="Revision history">{current.title}</Title>
            <Hatnote>
              Back to <Link href={`/notes/${current.id}`}>the entry</Link>.
            </Hatnote>

            <p className="mb-4">
              This entry has <b>{versions.length}</b> revision
              {versions.length === 1 ? '' : 's'}. Restoring an earlier one publishes its text as
              a new revision — nothing is erased.
            </p>

            <div className="overflow-x-auto rounded-lg border"><Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Revision</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Author</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((v, i) => {
                  const status = statusWord(v)
                  return (
                    <TableRow key={v.id}>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>
                        v{v.version} {i === 0 && <b>(current)</b>}
                      </TableCell>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>{onDate(v.updated_at)}</TableCell>
                      <TableCell>{v.author ?? 'Manthana'}</TableCell>
                      <TableCell className={status?.cls}>{status?.text ?? 'established'}</TableCell>
                      <TableCell>
                        {i !== 0 && (
                          <button
                            disabled={!!busy}
                            onClick={async () => {
                              setBusy(v.id)
                              const res = await post<{ note: Note }>(
                                `/notes/${current.id}/revert`,
                                { to_version_id: v.id },
                              )
                              router.push(`/notes/${res.note.id}`)
                            }}
                          >
                            {busy === v.id ? 'Restoring…' : 'restore'}
                          </button>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table></div>

            {versions.map((v) => (
              <section key={v.id}>
                <h2>
                  Revision {v.version}
                  <span className="ml-2 text-xs font-normal">
                    {v.author ?? 'Manthana'}, {onDate(v.updated_at)}
                  </span>
                </h2>
                <p>
                  <b>{v.title}</b>
                </p>
                <Markdown>{v.body}</Markdown>
              </section>
            ))}
          </>
        )
      }}
    </Wiki>
  )
}
