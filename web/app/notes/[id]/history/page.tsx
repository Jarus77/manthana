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

            <p className="lead">
              This entry has <b>{versions.length}</b> revision
              {versions.length === 1 ? '' : 's'}. Restoring an earlier one publishes its text as
              a new revision — nothing is erased.
            </p>

            <table className="wikitable">
              <thead>
                <tr>
                  <th>Revision</th>
                  <th>Date</th>
                  <th>Author</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {versions.map((v, i) => {
                  const status = statusWord(v)
                  return (
                    <tr key={v.id}>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        v{v.version} {i === 0 && <b>(current)</b>}
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>{onDate(v.updated_at)}</td>
                      <td>{v.author ?? 'Manthana'}</td>
                      <td className={status?.cls}>{status?.text ?? 'established'}</td>
                      <td>
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
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {versions.map((v) => (
              <section key={v.id}>
                <h2>
                  Revision {v.version}
                  <span className="editsection">
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
