'use client'

/**
 * Home — a discovery feed.
 *
 * What changed from the old home page, and why:
 *
 *   * The stream leads. Opening the wiki should answer "what is everyone
 *     actually doing" before you have thought of a question. Previously the
 *     first thing on screen was a projects table — an aggregate, which tells
 *     you a project is busy but never what anyone did.
 *   * Sections follow the data. The old page had a hardcoded <h3>Benchmarks</h3>
 *     between decisions and projects, which made one org's use case part of
 *     everyone's information architecture. Here the server emits a section per
 *     note kind that actually has fresh content, and benchmark is simply one of
 *     them — rendered with its prev→new delta because that is how a benchmark
 *     reads, not because it is privileged.
 */

import Link from 'next/link'
import { AskBar } from '@/components/AskBar'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  NoteCard,
  PersonChip,
  Section,
  StreamItem,
  when,
} from '@/components/primitives'
import { KIND_LABEL, type HomeFeed } from '@/lib/types'

export default function HomePage() {
  return (
    <Wiki<HomeFeed> path="/home">
      {(feed) => (
        <>
          <div style={{ marginBottom: 22 }}>
            <h1 style={{ marginBottom: 12 }}>What&rsquo;s happening</h1>
            <AskBar hero />
          </div>

          {feed.unreviewed > 0 && (
            <Link href="/knowledge/all?status=candidate" className="panel" style={{ display: 'block' }}>
              <span className="badge badge-unreviewed">unreviewed</span>{' '}
              <b>{feed.unreviewed}</b> note{feed.unreviewed === 1 ? '' : 's'} written by the
              AI that nobody has checked yet.
            </Link>
          )}

          <Section
            title="Recent work"
            action={<Link className="muted" href="/sessions">browse all sessions →</Link>}
          >
            {feed.stream.length ? (
              feed.stream.map((s) => <StreamItem key={s.id} session={s} />)
            ) : (
              <Empty>No sessions released this week.</Empty>
            )}
          </Section>

          {feed.sections.map((section) => (
            <Section
              key={section.kind}
              title={`New ${KIND_LABEL[section.kind].toLowerCase()}`}
              action={
                <Link className="muted" href={`/knowledge/${section.kind}`}>
                  all {KIND_LABEL[section.kind].toLowerCase()} →
                </Link>
              }
            >
              {section.notes.map((note) => {
                const delta = feed.benchmarks[note.id]
                const moved =
                  delta?.previous_value && delta.previous_value !== note.value
                    ? delta.previous_value
                    : undefined
                return <NoteCard key={note.id} note={note} movedFrom={moved} />
              })}
            </Section>
          ))}

          <div className="grid-2">
            <Section title="Active projects">
              {feed.projects.length ? (
                <table className="list">
                  <tbody>
                    {feed.projects.map((p) => (
                      <tr key={p.project}>
                        <td>
                          <Link href={`/projects/${encodeURIComponent(p.project)}`}>
                            {p.project}
                          </Link>
                          <div className="faint">{p.top_intent}</div>
                        </td>
                        <td className="faint" style={{ whiteSpace: 'nowrap' }}>
                          {p.sessions} session{p.sessions === 1 ? '' : 's'}
                          <div>{when(p.last_active)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty>Nothing this week.</Empty>
              )}
            </Section>

            <Section title="Who&rsquo;s active">
              {feed.people.length ? (
                <table className="list">
                  <tbody>
                    {feed.people.map((a) => (
                      <tr key={a.actor}>
                        <td>
                          <PersonChip actor={a.actor} />
                          <div className="faint">{a.intents[0] ?? '—'}</div>
                        </td>
                        <td className="faint" style={{ whiteSpace: 'nowrap' }}>
                          {a.projects.slice(0, 2).join(', ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty>Nobody released a session this week.</Empty>
              )}
            </Section>
          </div>
        </>
      )}
    </Wiki>
  )
}
