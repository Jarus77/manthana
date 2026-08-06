'use client'

/**
 * A person, as their projects.
 *
 * The question a visitor brings is "which projects is this engineer on, what is
 * each one, and how is it going" — so each project block carries its status,
 * the article's one-line description, and the last few READABLE sessions.
 * The note-kind sections ("Decisions from their work", 458 gotchas…) are gone:
 * the taxonomy is a retrieval substrate, not reading material. Collaborators
 * stay at the bottom.
 */

import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  CatLinks,
  Empty,
  Hatnote,
  Infobox,
  PersonLink,
  ProjectLink,
  Section,
  SessionRow,
  StatusWord,
  Title,
  Toc,
  clip,
  onDate,
  shortName,
  when,
} from '@/components/primitives'
import { edgeReason, type PersonPage } from '@/lib/types'

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
          ...(data.projects.length ? [{ id: 'projects', label: 'Projects' }] : []),
          ...(data.unfiled.length ? [{ id: 'unfiled', label: 'Unfiled sessions' }] : []),
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
                  data.projects.length
                    ? data.projects.map((p, i) => (
                        <span key={p.rollup.project}>
                          {i > 0 && ', '}
                          <ProjectLink project={p.rollup.project} />
                        </span>
                      ))
                    : '—',
                ],
                ['Last active', act ? when(act.last_active) : '—'],
              ]}
            />

            <p className="lead">
              <b>{name}</b> ({<span className="mono">{data.actor}</span>}) is an engineer in
              the {data.org_id} organisation.{' '}
              {act ? (
                <>
                  They have released <b>{act.sessions}</b> session
                  {act.sessions === 1 ? '' : 's'} recently, most recently{' '}
                  {when(act.last_active)}.
                </>
              ) : (
                <>They have not released a session in the last fortnight.</>
              )}
            </p>

            <Toc sections={sections} />
            <div className="clear" />

            {act && act.intents.length > 0 && (
              <Section id="current" title="Current work">
                <ul>
                  {act.intents.map((intent, i) => (
                    <li key={i}>{clip(intent, 200)}</li>
                  ))}
                </ul>
              </Section>
            )}

            <Section id="projects" title="Projects">
              {data.projects.length ? (
                data.projects.map(({ rollup, status, what_this_is, sessions, pending_count }) => (
                  <div key={rollup.project} style={{ marginBottom: '1.4em' }}>
                    <h3 id={`project-${rollup.project}`}>
                      <ProjectLink project={rollup.project} />{' '}
                      <span className="editsection">
                        <StatusWord status={status} />
                      </span>
                    </h3>
                    {what_this_is ? (
                      <p className="subtle">{what_this_is}</p>
                    ) : (
                      <p className="faint">
                        {pending_count > 0
                          ? `No article yet — ${pending_count} session${pending_count === 1 ? '' : 's'} here still awaiting a summary.`
                          : 'No article yet — one is written on the next pass.'}
                      </p>
                    )}
                    {sessions.length > 0 && (
                      <ul>
                        {sessions.map((s) => (
                          <SessionRow key={s.id} session={s} />
                        ))}
                      </ul>
                    )}
                    {pending_count > 0 && (
                      <p className="faint">
                        {pending_count} session{pending_count === 1 ? '' : 's'} awaiting summary
                      </p>
                    )}
                  </div>
                ))
              ) : (
                <Empty>No released sessions attributed to a project.</Empty>
              )}
            </Section>

            {data.unfiled.length > 0 && (
              <Section id="unfiled" title="Unfiled sessions">
                <p className="subtle">
                  Sessions that ran outside a git repository, so Manthana could not attribute
                  them to a project. Listed here so they stay reachable.
                </p>
                <ul>
                  {data.unfiled.map((s) => (
                    <SessionRow key={s.id} session={s} />
                  ))}
                </ul>
              </Section>
            )}

            {data.connections.length > 0 && (
              <Section id="see-also" title="See also">
                <p className="subtle">People {name} shares work with, and what connects them.</p>
                <ul>
                  {data.connections.map((edge) => (
                    <li key={edge.actor}>
                      <PersonLink actor={edge.actor} /> — {edgeReason(edge)}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <CatLinks
              categories={[
                { label: 'People', href: '/people' },
                ...data.projects.map((p) => ({
                  label: p.rollup.project,
                  href: `/projects/${encodeURIComponent(p.rollup.project)}`,
                })),
              ]}
            />
            <p className="faint" style={{ marginTop: '0.6em' }}>
              Live activity is computed from released sessions, not stored — it cannot go
              stale.{act && ` Last session ${onDate(act.last_active)}.`}
            </p>
          </>
        )
      }}
    </Wiki>
  )
}
