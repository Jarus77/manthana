'use client'

import { use } from 'react'
import { PeopleConnections } from '@/components/ConnectionsPanel'
import { Wiki } from '@/components/Loader'
import {
  Crumbs,
  Empty,
  NoteCard,
  ProjectChip,
  Section,
  SessionCard,
  shortName,
  when,
} from '@/components/primitives'
import { KIND_LABEL, type PersonPage } from '@/lib/types'

export default function PersonDetail({ params }: { params: Promise<{ actor: string }> }) {
  const { actor } = use(params)
  const decoded = decodeURIComponent(actor)

  return (
    <Wiki<PersonPage> path={`/people/${encodeURIComponent(decoded)}`}>
      {(data) => (
        <>
          <Crumbs trail={[{ label: 'People', href: '/people' }, { label: shortName(data.actor) }]} />
          <h1>{shortName(data.actor)}</h1>
          <p className="muted">{data.actor}</p>

          <div className="split">
            <div>
              {/* "What are they working on" is always live activity, never a
                  note — a stored answer to this question goes stale by design. */}
              <Section title="Working on">
                {data.activity ? (
                  <div className="card">
                    <div className="row" style={{ marginBottom: 8 }}>
                      {data.activity.projects.map((p) => (
                        <ProjectChip key={p} project={p} />
                      ))}
                      <span className="faint">
                        {data.activity.sessions} session
                        {data.activity.sessions === 1 ? '' : 's'} · last active{' '}
                        {when(data.activity.last_active)}
                      </span>
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 20 }}>
                      {data.activity.intents.map((intent, i) => (
                        <li key={i} className="muted">
                          {intent}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <Empty>No sessions in the last fortnight.</Empty>
                )}
              </Section>

              {data.sections.map((section) => (
                <Section
                  key={section.kind}
                  title={`${KIND_LABEL[section.kind]} from their work`}
                >
                  {section.notes.map((note) => (
                    <NoteCard key={note.id} note={note} />
                  ))}
                </Section>
              ))}

              <Section title="Sessions">
                {data.sessions.length ? (
                  data.sessions.map((s) => <SessionCard key={s.id} session={s} />)
                ) : (
                  <Empty>No released sessions yet.</Empty>
                )}
              </Section>
            </div>

            <PeopleConnections edges={data.connections} />
          </div>
        </>
      )}
    </Wiki>
  )
}
