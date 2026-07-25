'use client'

/**
 * The team's home feed — three things and nothing else.
 *
 * Lives at /home rather than / because / is now the marketing page. The move was
 * forced rather than cosmetic: `useWiki` treats a 401 as "session expired" and
 * redirects to /login, so a stranger landing on an authenticated / would be
 * bounced to a sign-in form and never see what the product is.
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { HomeFeed } from '@/lib/types'

export default function MainPage() {
  return (
    <Wiki<HomeFeed> path="/home">
      {(feed) => (
        <>
          <div className="mb-8 rounded-xl border bg-muted/30 px-6 py-8 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">
              The {feed.org_id} wiki
            </h1>
            <p className="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">
              The shared context behind what this team builds — one living article per
              project, written from everyone&rsquo;s work sessions.
            </p>
            <div className="mx-auto mt-5 max-w-lg text-left">
              <AskBar />
            </div>
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <h2 className="mb-3 text-base font-semibold">Who&rsquo;s active</h2>
              {feed.people.length ? (
                <div className="overflow-x-auto rounded-lg border"><Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Person</TableHead>
                      <TableHead>Working on</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {feed.people.map((a) => (
                      <TableRow key={a.actor}>
                        <TableCell style={{ whiteSpace: 'nowrap' }}>
                          <PersonLink actor={a.actor} />
                        </TableCell>
                        <TableCell>
                          {a.intents[0] ? clip(a.intents[0]) : <span className="text-sm text-muted-foreground">—</span>}
                          <div className="text-sm text-muted-foreground">
                            {a.projects.map((p, i) => (
                              <span key={p}>
                                {i > 0 && ', '}
                                <ProjectLink project={p} />
                              </span>
                            ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table></div>
              ) : (
                <Empty>Nobody released a session this week.</Empty>
              )}
            </div>

            <div>
              <h2 className="mb-3 text-base font-semibold">Projects</h2>
              {feed.projects.length ? (
                <div className="overflow-x-auto rounded-lg border"><Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Project</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Last active</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {feed.projects.map((p) => (
                      <TableRow key={p.project}>
                        <TableCell>
                          <ProjectLink project={p.project} />
                        </TableCell>
                        <TableCell>
                          <StatusWord status={p.status} />
                        </TableCell>
                        <TableCell style={{ whiteSpace: 'nowrap' }}>{when(p.last_active)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table></div>
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
              <p className="text-sm text-muted-foreground">
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
