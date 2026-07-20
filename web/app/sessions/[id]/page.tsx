'use client'

/**
 * One session digest, as an article.
 *
 * The digest is shown as its author released it. What is deliberately absent is
 * the raw transcript: that stays behind the audited, founder-only drill-down,
 * and nothing here hints at a route to it.
 */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  CatLinks,
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
} from '@/components/primitives'
import type { SessionPage } from '@/lib/types'

function ItemList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <>
      <h3>{title}</h3>
      <ul className="mono">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </>
  )
}

export default function SessionArticle({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <Wiki<SessionPage> path={`/sessions/${encodeURIComponent(id)}`}>
      {(data) => {
        const s = data.session
        const touched =
          s.files_touched.length + s.prs_opened.length + s.tests_added.length + s.artifacts.length
        const sections = [
          { id: 'approach', label: 'Approach' },
          ...(s.friction.length ? [{ id: 'friction', label: 'Friction' }] : []),
          ...(touched ? [{ id: 'touched', label: 'What it touched' }] : []),
          ...(data.notes.length ? [{ id: 'knowledge', label: 'Knowledge produced' }] : []),
          { id: 'see-also', label: 'See also' },
        ]

        return (
          <>
            <Title tagline="A work session recorded in the Manthana wiki">
              {s.task_intent || 'Untitled session'}
            </Title>

            <Hatnote>
              This is the digest its author released to the team. The full transcript is not
              part of the wiki. To check what was released without any of this page&rsquo;s
              framing, read the{' '}
              <Link href={`/sessions/${s.id}/verbatim`}>released compaction verbatim</Link>.
            </Hatnote>

            <Infobox
              title={s.session_id}
              subtitle="Session"
              rows={[
                ['Engineer', <PersonLink actor={s.actor} />],
                ['Project', <ProjectLink project={s.project} />],
                ['Outcome', s.outcome],
                ['Date', onDate(s.started_at)],
                ['Duration', `${Math.max(1, Math.round(s.duration_seconds / 60))} minutes`],
                ['Tool', s.surface],
                ['Files touched', s.files_touched.length || '—'],
                ['Pull requests', s.prs_opened.length || '—'],
                ...(s.est_cost_usd
                  ? ([['Cost', `$${s.est_cost_usd.toFixed(2)}`]] as Array<[string, React.ReactNode]>)
                  : []),
              ]}
            />

            <p className="lead">
              On {onDate(s.started_at)}, <b>{shortName(s.actor)}</b> worked on{' '}
              <ProjectLink project={s.project} /> to <b>{s.task_intent.toLowerCase()}</b>. The
              session ran about {Math.max(1, Math.round(s.duration_seconds / 60))} minutes and
              ended <b>{s.outcome}</b>.
              {data.notes.length > 0 && (
                <>
                  {' '}
                  It contributed to {data.notes.length} durable entr
                  {data.notes.length === 1 ? 'y' : 'ies'} in this wiki.
                </>
              )}
            </p>

            <Toc sections={sections} />
            <div className="clear" />

            <Section id="approach" title="Approach">
              {s.approach ? <p>{s.approach}</p> : <Empty>Not recorded.</Empty>}
            </Section>

            {s.friction.length > 0 && (
              <Section id="friction" title="Friction">
                <p className="subtle">What got in the way, as the engineer recorded it.</p>
                <ul>
                  {s.friction.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              </Section>
            )}

            {touched > 0 && (
              <Section id="touched" title="What it touched">
                <ItemList title="Files" items={s.files_touched} />
                <ItemList title="Pull requests" items={s.prs_opened} />
                <ItemList title="Tests added" items={s.tests_added} />
                <ItemList title="Artifacts" items={s.artifacts} />
              </Section>
            )}

            {data.notes.length > 0 && (
              <Section id="knowledge" title="Knowledge produced">
                <p className="subtle">Entries that cite this session as evidence.</p>
                <ul>
                  {data.notes.map((n) => (
                    <NoteRow key={n.id} note={n} />
                  ))}
                </ul>
              </Section>
            )}

            {data.disputes.length > 0 && (
              <Section title="Claims this session contradicts">
                <ul>
                  {data.disputes.map((n) => (
                    <NoteRow key={n.id} note={n} />
                  ))}
                </ul>
              </Section>
            )}

            <Section id="see-also" title="See also">
              {data.same_actor.length > 0 && (
                <>
                  <h3>More from {shortName(s.actor)}</h3>
                  <ul>
                    {data.same_actor.map((n) => (
                      <SessionRow key={n.id} session={n} />
                    ))}
                  </ul>
                </>
              )}
              {data.same_project.length > 0 && (
                <>
                  <h3>Others on {s.project}</h3>
                  <ul>
                    {data.same_project.map((n) => (
                      <SessionRow key={n.id} session={n} />
                    ))}
                  </ul>
                </>
              )}
              {!data.same_actor.length && !data.same_project.length && (
                <Empty>No neighbouring sessions.</Empty>
              )}
            </Section>

            <CatLinks
              categories={[
                { label: 'Sessions', href: '/sessions' },
                { label: s.project, href: `/projects/${encodeURIComponent(s.project)}` },
                { label: `Outcome: ${s.outcome}` },
              ]}
            />
          </>
        )
      }}
    </Wiki>
  )
}
