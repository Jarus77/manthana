'use client'

/**
 * A project, as a LIVING ARTICLE.
 *
 * The page follows the two-layer model: the article is the small, curated
 * surface a fresh reader absorbs in ten seconds; the sessions below it are the
 * primary sources it cites. Structure per the product spec:
 *
 *   Status (computed from timestamps, no LLM) · What this is · Current state
 *   (rewritten each update, never appended) · Open questions · Related ·
 *   Changelog (append-only — projected from the article's version chain).
 *
 * Note-kind sections are gone: notes are a retrieval substrate, reachable via
 * search and citations, not page furniture.
 */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  CatLinks,
  Empty,
  Infobox,
  Markdown,
  NoteBanners,
  PersonList,
  ProjectLink,
  Section,
  SessionRow,
  StatusWord,
  Title,
  Toc,
  onDate,
  when,
} from '@/components/primitives'
import type { ProjectPage } from '@/lib/types'

export default function ProjectArticle({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params)
  const decoded = decodeURIComponent(slug)

  return (
    <Wiki<ProjectPage> path={`/projects/${encodeURIComponent(decoded)}`}>
      {(data) => {
        const r = data.rollup
        const sections = [
          ...(data.overview ? [{ id: 'article', label: 'Article' }] : []),
          { id: 'sessions', label: 'Sessions' },
          ...(data.neighbors.length ? [{ id: 'related', label: 'Related' }] : []),
          ...(data.changelog.length ? [{ id: 'changelog', label: 'Changelog' }] : []),
        ]

        return (
          <>
            <Title
              tagline={
                <>
                  A project in the {data.org_id} organisation ·{' '}
                  <StatusWord status={data.status} />
                </>
              }
            >
              {data.project}
            </Title>

            <Infobox
              title={data.project}
              subtitle="Project"
              rows={[
                ['Status', <StatusWord status={data.status} />],
                ['Sessions', r ? r.sessions : '—'],
                ['Contributors', r ? <PersonList actors={r.actors} /> : '—'],
                ['Last active', r ? when(r.last_active) : '—'],
                [
                  'Article revisions',
                  data.changelog.length ? data.changelog.length : '—',
                ],
              ]}
            />

            {data.overview ? (
              <div id="article">
                <NoteBanners note={data.overview} />
                <Markdown>{data.overview.body}</Markdown>
                <p className="faint">
                  This article is written by Manthana from the sessions below and rewritten as
                  the work changes.{' '}
                  <Link href={`/notes/${data.overview.id}`}>Correct it</Link> — a human edit
                  becomes the permanent version.
                </p>
              </div>
            ) : (
              <p className="lead">
                <b>{data.project}</b> is a project in the {data.org_id} organisation. No
                article has been written yet.{' '}
                {data.sessions.length > 0 ? (
                  <>
                    Manthana writes one from the {data.sessions.length} summarised session
                    {data.sessions.length === 1 ? '' : 's'} below; it appears on the next pass.
                  </>
                ) : data.pending_count > 0 ? (
                  <>
                    Articles are written from summarised sessions, and{' '}
                    {data.pending_count === 1
                      ? 'the one session here is'
                      : `all ${data.pending_count} sessions here are`}{' '}
                    still awaiting a summary.
                  </>
                ) : (
                  <>An article appears once the first session here is summarised.</>
                )}
              </p>
            )}

            <Toc sections={sections} />
            <div className="clear" />

            <Section id="sessions" title="Sessions">
              <p className="subtle">
                The primary sources: what actually happened, session by session.
              </p>
              {data.sessions.length ? (
                <ul>
                  {data.sessions.map((s) => (
                    <SessionRow key={s.id} session={s} />
                  ))}
                </ul>
              ) : (
                <Empty>
                  {data.pending_count > 0
                    ? 'Nothing readable yet — every session here is still awaiting its summary.'
                    : 'No sessions have been released to this project.'}
                </Empty>
              )}
              {data.pending_count > 0 && (
                <p className="faint">
                  {data.pending_count} session{data.pending_count === 1 ? '' : 's'} awaiting
                  summary
                </p>
              )}
            </Section>

            {data.neighbors.length > 0 && (
              <Section id="related" title="Related">
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

            {data.changelog.length > 0 && (
              <Section id="changelog" title="Changelog">
                <p className="subtle">
                  One line per article revision — the growing part of the article lives here,
                  not in the body.
                </p>
                <ul>
                  {data.changelog.map((entry) => (
                    <li key={entry.note_id}>
                      {onDate(entry.date)} — {entry.change_summary}{' '}
                      <span className="faint">
                        (<Link href={`/notes/${entry.note_id}`}>v{entry.version}</Link>
                        {entry.source === 'human' && ', human'})
                      </span>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <CatLinks categories={[{ label: 'Projects', href: '/projects' }]} />
          </>
        )
      }}
    </Wiki>
  )
}
