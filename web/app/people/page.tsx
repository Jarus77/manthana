'use client'

/** Index of people — a wikitable, the way Wikipedia lists anything enumerable. */

import Link from 'next/link'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  PersonLink,
  ProjectLink,
  Section,
  Title,
  shortName,
  when,
} from '@/components/primitives'
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
          <p className="lead">
            Everyone in the <b>{data.org_id}</b> organisation who has released work to the wiki.
            Activity is computed live from released sessions; the quiet list keeps everyone else
            reachable.
          </p>

          <Section title="Active recently">
            {data.active.length ? (
              <table className="wikitable">
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Working on</th>
                    <th>Projects</th>
                    <th>Sessions</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {data.active.map((a) => (
                    <tr key={a.actor}>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <PersonLink actor={a.actor} />
                      </td>
                      <td>{a.intents[0] ?? '—'}</td>
                      <td>
                        {a.projects.map((p, i) => (
                          <span key={p}>
                            {i > 0 && ', '}
                            <ProjectLink project={p} />
                          </span>
                        ))}
                      </td>
                      <td>{a.sessions}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{when(a.last_active)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>Nobody has released a session recently.</Empty>
            )}
          </Section>

          {data.quiet.length > 0 && (
            <Section title="Quiet lately">
              <p className="subtle">
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
