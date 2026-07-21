'use client'

/**
 * Main page — three things and nothing else.
 *
 * Who is active, what the projects are (with status), and the last few sessions
 * a reader can actually read. The endless scroll of note-kind sections and
 * untitled sessions is gone: the taxonomy became a retrieval substrate
 * (ask/search + citations), and unsummarised sessions collapse to one count
 * line per project — a list of raw prompts answered no question anyone asked.
 */

import Link from 'next/link'
import { AskBar } from '@/components/AskBar'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  PersonLink,
  ProjectLink,
  Section,
  SessionRow,
  StatusWord,
  clip,
  when,
} from '@/components/primitives'
import type { HomeFeed } from '@/lib/types'

export default function MainPage() {
  return (
    <Wiki<HomeFeed> path="/home">
      {(feed) => (
        <>
          <div className="portal-lead">
            <h1>Welcome to the {feed.org_id} wiki</h1>
            <p style={{ margin: '0.2em 0 0.8em' }}>
              The shared context behind what this team builds — one living article per
              project, written from everyone&rsquo;s work sessions.
            </p>
            <div style={{ maxWidth: 520, margin: '0 auto' }}>
              <AskBar />
            </div>
          </div>

          <div className="portal-grid">
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
                          {a.intents[0] ? clip(a.intents[0]) : <span className="faint">—</span>}
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

            <div className="portal-box">
              <h2>Projects</h2>
              {feed.projects.length ? (
                <table className="wikitable">
                  <thead>
                    <tr>
                      <th>Project</th>
                      <th>Status</th>
                      <th>Last active</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feed.projects.map((p) => (
                      <tr key={p.project}>
                        <td>
                          <ProjectLink project={p.project} />
                        </td>
                        <td>
                          <StatusWord status={p.status} />
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{when(p.last_active)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty>Nothing this week.</Empty>
              )}
            </div>
          </div>

          <Section title="Recent work" action={<Link href="/sessions">all sessions</Link>}>
            {feed.stream.length ? (
              <ul>
                {feed.stream.map((s) => (
                  <SessionRow key={s.id} session={s} />
                ))}
              </ul>
            ) : (
              <Empty>No summarised sessions this week.</Empty>
            )}
            {feed.pending_counts.length > 0 && (
              <p className="faint">
                Awaiting summary:{' '}
                {feed.pending_counts.map(([project, n], i) => (
                  <span key={project || 'unfiled'}>
                    {i > 0 && ' · '}
                    {project ? <ProjectLink project={project} /> : 'unfiled'} ({n})
                  </span>
                ))}
              </p>
            )}
          </Section>
        </>
      )}
    </Wiki>
  )
}
