'use client'

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import { TeachControls } from '@/components/TeachControls'
import {
  Crumbs,
  Markdown,
  PersonChip,
  ProjectChip,
  Section,
  SessionCard,
  StatusBadge,
  when,
} from '@/components/primitives'
import { KIND_SINGULAR, type NotePage } from '@/lib/types'

export default function NoteDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <Wiki<NotePage> path={`/notes/${encodeURIComponent(id)}`}>
      {(data, mutate) => {
        const note = data.note
        return (
          <>
            <Crumbs
              trail={[
                { label: 'Knowledge', href: '/knowledge/all' },
                { label: KIND_SINGULAR[note.kind] ?? note.kind, href: `/knowledge/${note.kind}` },
                { label: note.title },
              ]}
            />
            <h1>{note.title}</h1>
            <div className="row" style={{ margin: '10px 0 20px' }}>
              <StatusBadge note={note} />
              {note.project && <ProjectChip project={note.project} />}
              <span className="faint">
                {note.source === 'human' ? `written by ${note.author}` : 'written by Manthana'} ·
                updated {when(note.updated_at)} · v{note.version}
              </span>
              <Link className="muted" href={`/notes/${note.id}/history`}>
                history →
              </Link>
            </div>

            {note.status === 'disputed' && (
              <div className="error" style={{ marginBottom: 16 }}>
                Later sessions contradict this claim — read the conflicting sessions below
                before relying on it.
              </div>
            )}

            <div className="split">
              <div>
                <div className="card prose">
                  {note.kind === 'benchmark' && note.value && (
                    <p className="mono">
                      {note.metric ?? 'value'}: <b>{note.value}</b>
                    </p>
                  )}
                  <Markdown>{note.body}</Markdown>
                </div>

                <div style={{ margin: '16px 0 28px' }}>
                  <TeachControls note={note} onChanged={mutate} />
                </div>

                <Section title="Evidence">
                  {data.evidence.length ? (
                    data.evidence.map((s) => <SessionCard key={s.id} session={s} />)
                  ) : (
                    <p className="empty">
                      {note.source === 'human'
                        ? 'Added by hand — this knowledge never came from a session.'
                        : 'The sessions behind this claim have since been purged.'}
                    </p>
                  )}
                </Section>

                {data.disputed_by.length > 0 && (
                  <Section title="Conflicting sessions">
                    {data.disputed_by.map((s) => (
                      <SessionCard key={s.id} session={s} />
                    ))}
                  </Section>
                )}
              </div>

              <div className="panel">
                <div className="nav-label" style={{ padding: '0 0 6px' }}>
                  People behind this
                </div>
                <div className="row">
                  {note.actors.length ? (
                    note.actors.map((a) => <PersonChip key={a} actor={a} />)
                  ) : (
                    <span className="faint">nobody recorded</span>
                  )}
                </div>
                {note.entities.files.length > 0 && (
                  <>
                    <div className="nav-label" style={{ padding: '14px 0 4px' }}>
                      Files
                    </div>
                    <div className="mono muted scroll-x">
                      {note.entities.files.map((f) => (
                        <div key={f}>{f}</div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          </>
        )
      }}
    </Wiki>
  )
}
