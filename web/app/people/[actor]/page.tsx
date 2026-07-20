'use client'

/**
 * A person, as an encyclopedia article.
 *
 * Lead sentence states who they are and what they are working on right now,
 * with the subject bolded — Wikipedia's convention, and the reason its first
 * line is always readable. The live rollup goes in the lead and the infobox
 * (facts that change), the durable knowledge their work produced goes in
 * sections (facts that don't), and collaborators become a "See also".
 */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  CatLinks,
  clip,
  Empty,
  Hatnote,
  Infobox,
  NoteRow,
  PersonLink,
  ProjectLink,
  Section,
  SessionRow,
  Title,
  Toc,
  onDate,
  shortName,
  when,
} from '@/components/primitives'
import { KIND_LABEL, edgeReason, type PersonPage } from '@/lib/types'

export default function PersonArticle({ params }: { params: Promise<{ actor: string }> }) {
  const { actor } = use(params)
  const decoded = decodeURIComponent(actor)

  return (
    <Wiki<PersonPage> path={`/people/${encodeURIComponent(decoded)}`}>
      {(data) => {
        const name = shortName(data.actor)
        const act = data.activity
        const sections = [
          ...(act ? [{ id: 'current', label: 'Current work' }] : []),
          ...data.sections.map((s) => ({ id: s.kind, label: KIND_LABEL[s.kind] })),
          { id: 'sessions', label: 'Sessions' },
          ...(data.connections.length ? [{ id: 'see-also', label: 'See also' }] : []),
        ]

        return (
          <>
            <Title>{name}</Title>
            <Hatnote>
              This entry describes a person. For the work itself, see the projects listed below.
            </Hatnote>

            <Infobox
              title={name}
              rows={[
                ['Identifier', <span className="mono">{data.actor}</span>],
                ['Sessions', act ? act.sessions : '—'],
                [
                  'Projects',
                  act?.projects.length
                    ? act.projects.map((p, i) => (
                        <span key={p}>
                          {i > 0 && ', '}
                          <ProjectLink project={p} />
                        </span>
                      ))
                    : '—',
                ],
                ['Last active', act ? when(act.last_active) : '—'],
                ['Entries citing them', data.sections.reduce((n, s) => n + s.notes.length, 0)],
                [
                  'Works with',
                  data.connections.length ? (
                    <PersonLink actor={data.connections[0].actor} />
                  ) : (
                    '—'
                  ),
                ],
              ]}
            />

            <p className="lead">
              <b>{name}</b> ({<span className="mono">{data.actor}</span>}) is an engineer in the{' '}
              {data.org_id} organisation.{' '}
              {act ? (
                <>
                  They have released <b>{act.sessions}</b> session
                  {act.sessions === 1 ? '' : 's'} recently, working on{' '}
                  {act.projects.map((p, i) => (
                    <span key={p}>
                      {i > 0 && (i === act.projects.length - 1 ? ' and ' : ', ')}
                      <ProjectLink project={p} />
                    </span>
                  ))}
                  , most recently {when(act.last_active)}.
                </>
              ) : (
                <>They have not released a session in the last fortnight.</>
              )}
            </p>

            <Toc sections={sections} />
            <div className="clear" />

            {act && (
              <Section id="current" title="Current work">
                <p>What they have been doing, taken from their most recent sessions:</p>
                <ul>
                  {act.intents.map((intent, i) => (
                    <li key={i}>{clip(intent, 200)}</li>
                  ))}
                </ul>
              </Section>
            )}

            {data.sections.map((section) => (
              <Section
                key={section.kind}
                id={section.kind}
                title={KIND_LABEL[section.kind]}
                action={<Link href={`/knowledge/${section.kind}`}>all</Link>}
              >
                <p className="subtle">
                  Entries drawn from sessions {name} took part in.
                </p>
                <ul>
                  {section.notes.map((n) => (
                    <NoteRow key={n.id} note={n} />
                  ))}
                </ul>
              </Section>
            ))}

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

            {data.connections.length > 0 && (
              <Section id="see-also" title="See also">
                <p className="subtle">
                  People {name} shares work with, and what connects them.
                </p>
                <ul>
                  {data.connections.map((edge) => (
                    <li key={edge.actor}>
                      <PersonLink actor={edge.actor} /> — {edgeReason(edge)}
                      {edge.via_notes.length > 0 && (
                        <>
                          {'; both cited in '}
                          {edge.via_notes.slice(0, 2).map((ref, i) => (
                            <span key={ref.id}>
                              {i > 0 && ', '}
                              <Link href={`/notes/${ref.id}`}>{ref.title}</Link>
                            </span>
                          ))}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <CatLinks
              categories={[
                { label: 'People', href: '/people' },
                ...(act?.projects ?? []).map((p) => ({
                  label: p,
                  href: `/projects/${encodeURIComponent(p)}`,
                })),
              ]}
            />
            <p className="faint" style={{ marginTop: '0.6em' }}>
              Live activity is computed from released sessions, not stored — it cannot go stale.
              {act && ` Last session ${onDate(act.last_active)}.`}
            </p>
          </>
        )
      }}
    </Wiki>
  )
}
