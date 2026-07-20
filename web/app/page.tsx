'use client'

/**
 * Main page — modelled on Wikipedia's, which is a portal rather than an article:
 * a welcome banner stating what the wiki is and how big it is, then boxed
 * sections of links into the content.
 *
 * Sections still follow the data (a section per note kind that has fresh
 * content), which was the point of `discovery_feed`. What changed is the
 * rendering: lists of linked titles with a one-line gloss, not a wall of cards.
 */

import Link from 'next/link'
import useSWR from 'swr'
import { AskBar } from '@/components/AskBar'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  clip,
  NoteRow,
  PersonLink,
  ProjectLink,
  SessionRow,
  when,
} from '@/components/primitives'
import { fetcher } from '@/lib/api'
import { KIND_LABEL, type HomeFeed, type Me } from '@/lib/types'

export default function MainPage() {
  const { data: me } = useSWR<Me>('/me', fetcher, { revalidateOnFocus: false })

  return (
    <Wiki<HomeFeed> path="/home">
      {(feed) => (
        <>
          <div className="portal-lead">
            <h1>Welcome to the {feed.org_id} wiki</h1>
            <p style={{ margin: '0.2em 0 0.8em' }}>
              The shared context behind what this team builds — written from{' '}
              {feed.stream.length > 0 && <>everyone&rsquo;s work sessions</>}
              {me?.total_notes ? (
                <>
                  , currently <b>{me.total_notes}</b> entr{me.total_notes === 1 ? 'y' : 'ies'}
                </>
              ) : null}
              .
            </p>
            <div style={{ maxWidth: 520, margin: '0 auto' }}>
              <AskBar />
            </div>
          </div>

          {feed.unreviewed > 0 && (
            <div className="ambox ambox-content">
              <b>
                {feed.unreviewed} entr{feed.unreviewed === 1 ? 'y has' : 'ies have'} not been
                reviewed.
              </b>{' '}
              Manthana wrote {feed.unreviewed === 1 ? 'it' : 'them'} from session evidence and
              nobody has checked {feed.unreviewed === 1 ? 'it' : 'them'} yet —{' '}
              <Link href="/knowledge/all?status=candidate">review the unchecked entries</Link>.
            </div>
          )}

          <div className="portal-grid">
            <div className="portal-box">
              <h2>Recent work</h2>
              {feed.stream.length ? (
                <>
                  <ul>
                    {feed.stream.slice(0, 12).map((s) => (
                      <SessionRow key={s.id} session={s} />
                    ))}
                  </ul>
                  <p className="faint" style={{ margin: 0 }}>
                    <Link href="/sessions">Browse all sessions →</Link>
                  </p>
                </>
              ) : (
                <Empty>No sessions released this week.</Empty>
              )}
            </div>

            <div className="portal-box">
              <h2>Who&rsquo;s active</h2>
              {feed.people.length ? (
                <table className="wikitable">
                  <thead>
                    <tr>
                      <th>Person</th>
                      <th>Working on</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feed.people.map((a) => (
                      <tr key={a.actor}>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <PersonLink actor={a.actor} />
                        </td>
                        <td>
                          {a.intents[0] ? clip(a.intents[0]) : '—'}
                          <div className="faint">
                            {a.projects.map((p, i) => (
                              <span key={p}>
                                {i > 0 && ', '}
                                <ProjectLink project={p} />
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty>Nobody released a session this week.</Empty>
              )}
            </div>
          </div>

          {feed.sections.map((section) => (
            <div className="portal-box" key={section.kind} style={{ marginTop: '1em' }}>
              <h2>
                New {KIND_LABEL[section.kind].toLowerCase()}
                <span className="editsection">
                  <Link href={`/knowledge/${section.kind}`}>see all</Link>
                </span>
              </h2>
              <ul>
                {section.notes.map((note) => {
                  const delta = feed.benchmarks[note.id]
                  const moved =
                    delta?.previous_value && delta.previous_value !== note.value
                      ? delta.previous_value
                      : undefined
                  return <NoteRow key={note.id} note={note} movedFrom={moved} />
                })}
              </ul>
            </div>
          ))}

          <div className="portal-box" style={{ marginTop: '1em' }}>
            <h2>Active projects</h2>
            {feed.projects.length ? (
              <table className="wikitable">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Latest work</th>
                    <th>Sessions</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {feed.projects.map((p) => (
                    <tr key={p.project}>
                      <td>
                        <ProjectLink project={p.project} />
                      </td>
                      <td>{clip(p.top_intent)}</td>
                      <td>{p.sessions}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{when(p.last_active)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>Nothing this week.</Empty>
            )}
          </div>
        </>
      )}
    </Wiki>
  )
}
