'use client'

/** Index of people — everyone who has released work, and what they touched. */

import Link from 'next/link'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  clip,
  PersonLink,
  ProjectLink,
  Section,
  Title,
  shortName,
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
import type { ActorActivity } from '@/lib/types'

interface PeopleIndex {
  active: ActorActivity[]
  quiet: Array<{ actor: string; display_name: string | null }>
  org_id: string
}

export default function PeopleIndexPage() {
  return (
    <Wiki<PeopleIndex> path="/people">
      {(data) => (
        <>
          <Title>People</Title>
          <p className="mb-4">
            Everyone in the <b>{data.org_id}</b> organisation who has released work to the wiki.
            Activity is computed live from released sessions; the quiet list keeps everyone else
            reachable.
          </p>

          <Section title="Active recently">
            {data.active.length ? (
              <div className="overflow-x-auto rounded-lg border"><Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Person</TableHead>
                    <TableHead>Working on</TableHead>
                    <TableHead>Projects</TableHead>
                    <TableHead>Sessions</TableHead>
                    <TableHead>Last active</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.active.map((a) => (
                    <TableRow key={a.actor}>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>
                        <PersonLink actor={a.actor} />
                      </TableCell>
                      <TableCell>{a.intents[0] ? clip(a.intents[0]) : <span className="text-sm text-muted-foreground">—</span>}</TableCell>
                      <TableCell>
                        {a.projects.map((p, i) => (
                          <span key={p}>
                            {i > 0 && ', '}
                            <ProjectLink project={p} />
                          </span>
                        ))}
                      </TableCell>
                      <TableCell>{a.sessions}</TableCell>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>{when(a.last_active)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table></div>
            ) : (
              <Empty>Nobody has released a session recently.</Empty>
            )}
          </Section>

          {data.quiet.length > 0 && (
            <Section title="Quiet lately">
              <p className="text-muted-foreground">
                No sessions in the window. Their past work and the knowledge it produced are
                still here.
              </p>
              <ul>
                {data.quiet.map((q) => (
                  <li key={q.actor}>
                    <Link href={`/people/${encodeURIComponent(q.actor)}`}>
                      {q.display_name || shortName(q.actor)}
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}
    </Wiki>
  )
}
