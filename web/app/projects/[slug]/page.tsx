'use client'

import { use } from 'react'
import { ProjectConnections } from '@/components/ConnectionsPanel'
import { Wiki } from '@/components/Loader'
import {
  Crumbs,
  Empty,
  NoteCard,
  PersonChip,
  Section,
  SessionCard,
  when,
} from '@/components/primitives'
import { KIND_LABEL, type ProjectPage } from '@/lib/types'

export default function ProjectDetail({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params)
  const decoded = decodeURIComponent(slug)

  return (
    <Wiki<ProjectPage> path={`/projects/${encodeURIComponent(decoded)}`}>
      {(data) => (
        <>
          <Crumbs trail={[{ label: 'Projects', href: '/projects' }, { label: data.project }]} />
          <h1>{data.project}</h1>

          {data.rollup ? (
            <div className="row muted" style={{ marginBottom: 20 }}>
              <span>
                {data.rollup.sessions} session{data.rollup.sessions === 1 ? '' : 's'} ·{' '}
                {data.note_count} note{data.note_count === 1 ? '' : 's'} · last active{' '}
                {when(data.rollup.last_active)}
              </span>
              {data.rollup.actors.map((a) => (
                <PersonChip key={a} actor={a} />
              ))}
            </div>
          ) : (
            <p className="muted">Nothing released in the last fortnight.</p>
          )}

          <div className="split">
            <div>
              {data.sections.length ? (
                data.sections.map((section) => (
                  <Section key={section.kind} title={KIND_LABEL[section.kind]}>
                    {section.notes.map((note) => (
                      <NoteCard key={note.id} note={note} />
                    ))}
                  </Section>
                ))
              ) : (
                <Section title="Knowledge">
                  <Empty>
                    No durable knowledge from this project yet — it appears once sessions
                    have been consolidated.
                  </Empty>
                </Section>
              )}

              <Section title="Sessions">
                {data.sessions.length ? (
                  data.sessions.map((s) => <SessionCard key={s.id} session={s} />)
                ) : (
                  <Empty>No released sessions.</Empty>
                )}
              </Section>
            </div>

            <ProjectConnections edges={data.neighbors} />
          </div>
        </>
      )}
    </Wiki>
  )
}
