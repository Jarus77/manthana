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
  Ambox,
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
  isPending,
  onDate,
  sessionTitle,
  shortName,
} from '@/components/primitives'
import type { SessionPage } from '@/lib/types'

/**
 * A named list of things the session produced.
 *
 * Deliberately NOT monospaced any more. These are sentences the model wrote
 * ("gaol.md: project goal statement"), not identifiers, and setting prose in a
 * code face was most of what made this block look like a machine dump.
 */
function ItemList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <>
      <h3>{title}</h3>
      <ul>
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
        // What a colleague can read, not what a machine recorded. File paths are
        // deliberately excluded from this count: they are absolute, laptop-specific
        // and unreadable, so they no longer justify a section on their own — they
        // collapse to a count that links to the verbatim digest.
        const produced =
          s.artifacts.length + s.prs_opened.length + s.tests_added.length + s.files_touched.length
        const sections = [
          ...(isPending(s) && s.task_intent
            ? [{ id: 'prompt', label: 'Opening prompt' }]
            : []),
          { id: 'approach', label: 'Approach' },
          ...(s.friction.length ? [{ id: 'friction', label: 'Friction' }] : []),
          ...(produced ? [{ id: 'produced', label: 'What came out of it' }] : []),
          ...(s.dead_end_branches.length ? [{ id: 'dead-ends', label: 'Dead ends' }] : []),
          ...(data.notes.length ? [{ id: 'knowledge', label: 'Knowledge produced' }] : []),
          { id: 'see-also', label: 'See also' },
        ]

        return (
          <>
            <Title tagline="A work session recorded in the Manthana wiki">
              {sessionTitle(s)}
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
                // A fixed row rather than only the hatnote sentence: this is the
                // page's provenance link, and it was previously findable only by
                // reading a paragraph of prose.
                [
                  'Released digest',
                  <Link href={`/sessions/${s.id}/verbatim`}>view verbatim</Link>,
                ],
                ...(s.est_cost_usd
                  ? ([['Cost', `$${s.est_cost_usd.toFixed(2)}`]] as Array<[string, React.ReactNode]>)
                  : []),
              ]}
            />

            {isPending(s) && (
              <Ambox kind="content">
                <b>This session has not been summarised yet.</b> Manthana recorded what it
                could measure — files, duration, cost — but the account of what was done is
                written later on the server. The engineer&rsquo;s opening prompt is shown
                under <a href="#prompt">Opening prompt</a> below.
              </Ambox>
            )}

            <p className="lead">
              On {onDate(s.started_at)}, <b>{shortName(s.actor)}</b> worked on{' '}
              <ProjectLink project={s.project} />
              {!isPending(s) && (
                <>
                  {' '}
                  to <b>{s.task_intent.toLowerCase()}</b>
                </>
              )}
              . The session ran about {Math.max(1, Math.round(s.duration_seconds / 60))} minutes
              {!isPending(s) && (
                <>
                  {' '}
                  and ended <b>{s.outcome}</b>
                </>
              )}
              .
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

            {isPending(s) && s.task_intent && (
              <Section id="prompt" title="Opening prompt">
                <p className="subtle">
                  The first thing the engineer typed, verbatim. It is not a summary of the
                  session and is shown here only because no summary exists yet.
                </p>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{s.task_intent}</pre>
              </Section>
            )}

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

            {produced > 0 && (
              <Section id="produced" title="What came out of it">
                <ItemList title="Artifacts" items={s.artifacts} />
                <ItemList title="Pull requests" items={s.prs_opened} />
                <ItemList title="Tests added" items={s.tests_added} />
                {s.files_touched.length > 0 && (
                  <p className="faint">
                    Touched {s.files_touched.length} file
                    {s.files_touched.length === 1 ? '' : 's'} —{' '}
                    <Link href={`/sessions/${s.id}/verbatim`}>listed in the released digest</Link>.
                  </p>
                )}
              </Section>
            )}

            {s.dead_end_branches.length > 0 && (
              <Section id="dead-ends" title="Dead ends">
                <p className="subtle">
                  Approaches tried here and abandoned. Recorded so the next person does not
                  spend the same hours finding out.
                </p>
                <ul>
                  {s.dead_end_branches.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
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
