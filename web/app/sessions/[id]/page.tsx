'use client'

/**
 * One session digest, and everywhere it leads.
 *
 * The digest is shown as released — intent, approach, outcome, friction, files,
 * PRs. What is deliberately absent is the raw transcript: that stays behind the
 * audited founder drill-down, and nothing on this page hints at a way to it.
 */

import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  Crumbs,
  Empty,
  NoteCard,
  OutcomePill,
  PersonChip,
  ProjectChip,
  Section,
  SessionCard,
  when,
} from '@/components/primitives'
import type { SessionPage } from '@/lib/types'

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="nav-label" style={{ padding: '0 0 4px' }}>
        {title}
      </div>
      <ul style={{ margin: 0, paddingLeft: 20 }}>
        {items.map((item, i) => (
          <li key={i} className="muted mono">
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function SessionDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <Wiki<SessionPage> path={`/sessions/${encodeURIComponent(id)}`}>
      {(data) => {
        const s = data.session
        return (
          <>
            <Crumbs
              trail={[{ label: 'Sessions', href: '/sessions' }, { label: s.session_id }]}
            />
            <h1>{s.task_intent || 'untitled session'}</h1>
            <div className="row muted" style={{ margin: '8px 0 20px' }}>
              <PersonChip actor={s.actor} />
              <ProjectChip project={s.project} />
              <OutcomePill outcome={s.outcome} />
              <span>{when(s.started_at)}</span>
              <span className="faint">
                {Math.round(s.duration_seconds / 60)}m · {s.surface}
              </span>
            </div>

            <div className="split">
              <div>
                <Section title="Approach">
                  {s.approach ? (
                    <div className="card prose">{s.approach}</div>
                  ) : (
                    <Empty>Not recorded.</Empty>
                  )}
                </Section>

                {s.friction.length > 0 && (
                  <Section title="Friction">
                    <div className="card">
                      <ul style={{ margin: 0, paddingLeft: 20 }}>
                        {s.friction.map((f, i) => (
                          <li key={i} className="muted">
                            {f}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </Section>
                )}

                <Section title="What it touched">
                  <div className="card">
                    <List title="Files" items={s.files_touched} />
                    <List title="Pull requests" items={s.prs_opened} />
                    <List title="Tests added" items={s.tests_added} />
                    <List title="Artifacts" items={s.artifacts} />
                    {!s.files_touched.length &&
                      !s.prs_opened.length &&
                      !s.tests_added.length &&
                      !s.artifacts.length && <Empty>Nothing recorded.</Empty>}
                  </div>
                </Section>

                {data.notes.length > 0 && (
                  <Section title="Knowledge from this session">
                    {data.notes.map((n) => (
                      <NoteCard key={n.id} note={n} />
                    ))}
                  </Section>
                )}

                {data.disputes.length > 0 && (
                  <Section title="Claims this session contradicts">
                    {data.disputes.map((n) => (
                      <NoteCard key={n.id} note={n} />
                    ))}
                  </Section>
                )}
              </div>

              <div>
                {data.same_actor.length > 0 && (
                  <div className="panel">
                    <div className="nav-label" style={{ padding: '0 0 6px' }}>
                      More from {s.actor.split('@')[0]}
                    </div>
                    {data.same_actor.map((n) => (
                      <SessionCard key={n.id} session={n} />
                    ))}
                  </div>
                )}
                {data.same_project.length > 0 && (
                  <div className="panel">
                    <div className="nav-label" style={{ padding: '0 0 6px' }}>
                      Others on {s.project}
                    </div>
                    {data.same_project.map((n) => (
                      <SessionCard key={n.id} session={n} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )
      }}
    </Wiki>
  )
}
