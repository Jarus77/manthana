'use client'

import Link from 'next/link'
import { Wiki } from '@/components/Loader'
import { Empty, PersonChip, Section, when } from '@/components/primitives'
import type { ProjectRollup } from '@/lib/types'

interface ProjectIndex {
  active: ProjectRollup[]
  quiet: string[]
  org_id: string
}

export default function ProjectsPage() {
  return (
    <Wiki<ProjectIndex> path="/projects">
      {(data) => (
        <>
          <h1 style={{ marginBottom: 18 }}>Projects</h1>

          <Section title="Active">
            {data.active.length ? (
              <table className="list">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Latest work</th>
                    <th>Who</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {data.active.map((p) => (
                    <tr key={p.project}>
                      <td>
                        <Link href={`/projects/${encodeURIComponent(p.project)}`}>
                          {p.project}
                        </Link>
                        <div className="faint">
                          {p.sessions} session{p.sessions === 1 ? '' : 's'}
                        </div>
                      </td>
                      <td className="muted">{p.top_intent}</td>
                      <td>
                        <div className="row row-tight">
                          {p.actors.map((a) => (
                            <PersonChip key={a} actor={a} />
                          ))}
                        </div>
                      </td>
                      <td className="faint">{when(p.last_active)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No project has seen a released session recently.</Empty>
            )}
          </Section>

          {data.quiet.length > 0 && (
            <Section title="Dormant">
              <div className="row">
                {data.quiet.map((p) => (
                  <Link key={p} className="chip" href={`/projects/${encodeURIComponent(p)}`}>
                    {p}
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
