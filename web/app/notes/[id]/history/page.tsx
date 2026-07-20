'use client'

/**
 * Version history. Append-only: a revert publishes the old text as a NEW
 * version rather than rewinding, so what was once published — including a bad
 * AI claim someone had to correct — stays on the record.
 */

import { useRouter } from 'next/navigation'
import { use, useState } from 'react'
import { post } from '@/lib/api'
import { Wiki } from '@/components/Loader'
import { Crumbs, Markdown, StatusBadge, when } from '@/components/primitives'
import type { Note } from '@/lib/types'

export default function NoteHistory({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const [busy, setBusy] = useState('')

  return (
    <Wiki<{ versions: Note[] }> path={`/notes/${encodeURIComponent(id)}/history`}>
      {(data) => {
        // Newest first — the current version is the one the reader arrived from.
        const versions = [...data.versions].sort((a, b) => b.version - a.version)
        const current = versions[0]
        return (
          <>
            <Crumbs
              trail={[
                { label: 'Knowledge', href: '/knowledge/all' },
                { label: current.title, href: `/notes/${current.id}` },
                { label: 'History' },
              ]}
            />
            <h1>History</h1>
            <p className="muted">
              {versions.length} version{versions.length === 1 ? '' : 's'}. Reverting restores
              earlier text as a new version — nothing is erased.
            </p>

            <div className="stack" style={{ marginTop: 20 }}>
              {versions.map((v, i) => (
                <div className="card" key={v.id}>
                  <div className="row" style={{ marginBottom: 8 }}>
                    <b>v{v.version}</b>
                    {i === 0 && <span className="badge badge-confirmed">current</span>}
                    <StatusBadge note={v} />
                    <span className="faint">
                      {v.author ? `by ${v.author}` : 'by Manthana'} · {when(v.updated_at)}
                    </span>
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
                        {busy === v.id ? 'Restoring…' : 'Restore this'}
                      </button>
                    )}
                  </div>
                  <div style={{ fontWeight: 550, marginBottom: 4 }}>{v.title}</div>
                  <div className="muted">
                    <Markdown>{v.body}</Markdown>
                  </div>
                </div>
              ))}
            </div>
          </>
        )
      }}
    </Wiki>
  )
}
