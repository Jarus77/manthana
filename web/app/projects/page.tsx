'use client'

/** Index of projects. */

import { Wiki } from '@/components/Loader'
import {
  Empty,
  PersonList,
  ProjectLink,
  Section,
  Title,
  when,
} from '@/components/primitives'
import type { ProjectRollup } from '@/lib/types'

interface ProjectIndex {
  active: ProjectRollup[]
  quiet: string[]
  org_id: string
}

export default function ProjectsIndexPage() {
  return (
    <Wiki<ProjectIndex> path="/projects">
      {(data) => (
        <>
          <Title>Projects</Title>
          <p className="lead">
            Every project in <b>{data.org_id}</b> that engineers have released sessions against.
          </p>

          <Section title="Active">
            {data.active.length ? (
              <table className="wikitable">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Latest work</th>
                    <th>Contributors</th>
                    <th>Sessions</th>
                    <th>Last active</th>
                  </tr>
                </thead>
                <tbody>
                  {data.active.map((p) => (
                    <tr key={p.project}>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <ProjectLink project={p.project} />
                      </td>
                      <td>{p.top_intent}</td>
                      <td>
                        <PersonList actors={p.actors} />
                      </td>
                      <td>{p.sessions}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{when(p.last_active)}</td>
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
              <p className="subtle">Old, but still reachable.</p>
              <ul>
                {data.quiet.map((p) => (
                  <li key={p}>
                    <ProjectLink project={p} />
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
