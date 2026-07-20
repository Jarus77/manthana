'use client'

import Link from 'next/link'
import { Wiki } from '@/components/Loader'
import { Empty, PersonChip, Section, shortName, when } from '@/components/primitives'
import type { ActorActivity } from '@/lib/types'

interface PeopleIndex {
  active: ActorActivity[]
  quiet: Array<{ actor: string; display_name: string | null }>
  org_id: string
}

export default function PeoplePage() {
  return (
    <Wiki<PeopleIndex> path="/people">
      {(data) => (
        <>
          <h1 style={{ marginBottom: 18 }}>People</h1>

          <Section title="Active recently">
            {data.active.length ? (
              <table className="list">
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Working on</th>
                    <th>Projects</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {data.active.map((a) => (
                    <tr key={a.actor}>
                      <td>
                        <PersonChip actor={a.actor} />
                      </td>
                      <td className="muted">{a.intents[0] ?? '—'}</td>
                      <td className="faint">{a.projects.join(', ') || '—'}</td>
                      <td className="faint">{when(a.last_active)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>Nobody has released a session recently.</Empty>
            )}
          </Section>

          {/* Quiet people still have pages: their past work and the knowledge it
              produced doesn't stop being useful because they had a slow fortnight. */}
          {data.quiet.length > 0 && (
            <Section title="Quiet lately">
              <div className="row">
                {data.quiet.map((q) => (
                  <Link
                    key={q.actor}
                    className="chip"
                    href={`/people/${encodeURIComponent(q.actor)}`}
                  >
                    {q.display_name || shortName(q.actor)}
                  </Link>
                ))}
              </div>
            </Section>
          )}
        </>
      )}
    </Wiki>
  )
}
