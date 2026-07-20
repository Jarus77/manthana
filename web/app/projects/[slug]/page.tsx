'use client'

/** A project, as an article: lead + infobox of live facts, then its knowledge. */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  CatLinks,
  Empty,
  Infobox,
  NoteRow,
  PersonList,
  ProjectLink,
  Section,
  SessionRow,
  Title,
  Toc,
  when,
} from '@/components/primitives'
import { KIND_LABEL, type ProjectPage } from '@/lib/types'

export default function ProjectArticle({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params)
  const decoded = decodeURIComponent(slug)

  return (
    <Wiki<ProjectPage> path={`/projects/${encodeURIComponent(decoded)}`}>
      {(data) => {
        const r = data.rollup
        const outcomes = r ? Object.entries(r.outcome_mix) : []
        const sections = [
          ...data.sections.map((s) => ({ id: s.kind, label: KIND_LABEL[s.kind] })),
          { id: 'sessions', label: 'Sessions' },
          ...(data.neighbors.length ? [{ id: 'see-also', label: 'See also' }] : []),
        ]

        return (
          <>
            <Title>{data.project}</Title>

            <Infobox
              title={data.project}
              subtitle="Project"
              rows={[
                ['Sessions', r ? r.sessions : '—'],
                ['Contributors', r ? <PersonList actors={r.actors} /> : '—'],
                [
                  'Outcomes',
                  outcomes.length
                    ? outcomes.map(([k, v]) => `${v} ${k}`).join(', ')
                    : '—',
                ],
                ['Entries', data.note_count],
                ['Last active', r ? when(r.last_active) : '—'],
                ['Estimated cost', r ? `$${r.est_cost_usd.toFixed(2)}` : '—'],
              ]}
            />

            <p className="lead">
              <b>{data.project}</b> is a project in the {data.org_id} organisation.{' '}
              {r ? (
                <>
                  It has seen <b>{r.sessions}</b> released session
                  {r.sessions === 1 ? '' : 's'} in the last fortnight, worked on by{' '}
                  <PersonList actors={r.actors} />. The most recent was{' '}
                  <i>{r.top_intent}</i>, {when(r.last_active)}.
                </>
              ) : (
                <>Nothing has been released against it in the last fortnight.</>
              )}{' '}
              {data.note_count > 0 && (
                <>
                  The team has recorded <b>{data.note_count}</b> durable entr
                  {data.note_count === 1 ? 'y' : 'ies'} from this work.
                </>
              )}
            </p>

            <Toc sections={sections} />
            <div className="clear" />

            {data.sections.length ? (
              data.sections.map((section) => (
                <Section
                  key={section.kind}
                  id={section.kind}
                  title={KIND_LABEL[section.kind]}
                  action={<Link href={`/knowledge/${section.kind}`}>all</Link>}
                >
                  <ul>
                    {section.notes.map((n) => (
                      <NoteRow key={n.id} note={n} />
                    ))}
                  </ul>
                </Section>
              ))
            ) : (
              <Section title="Knowledge">
                <Empty>
                  Nothing durable has been consolidated from this project yet. Entries appear
                  once its sessions have been read.
                </Empty>
              </Section>
            )}

            <Section id="sessions" title="Sessions">
              {data.sessions.length ? (
                <ul>
                  {data.sessions.map((s) => (
                    <SessionRow key={s.id} session={s} />
                  ))}
                </ul>
              ) : (
                <Empty>No released sessions.</Empty>
              )}
            </Section>

            {data.neighbors.length > 0 && (
              <Section id="see-also" title="See also">
                <p className="subtle">
                  Projects worked on by the same people — how work actually flows across the
                  org.
                </p>
                <ul>
                  {data.neighbors.map((n) => (
                    <li key={n.project}>
                      <ProjectLink project={n.project} /> — shared with{' '}
                      <PersonList actors={n.via_actors} />
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <CatLinks
              categories={[
                { label: 'Projects', href: '/projects' },
                ...(r ? [{ label: `${r.sessions} sessions` }] : []),
              ]}
            />
          </>
        )
      }}
    </Wiki>
  )
}
