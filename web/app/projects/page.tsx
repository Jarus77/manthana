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
import type { ProjectIndexRow } from '@/lib/types'

interface ProjectIndex {
  active: ProjectIndexRow[]
  /** Objects, not strings — the server sends {project, status, description}.
   *  Typing this as string[] passed the whole object to ProjectLink, which
   *  renders it as a child, and React threw on every org that had a dormant
   *  project. */
  quiet: Array<{ project: string; description: string }>
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
                    {/* What the project IS, from its article — not one session's
                        intent. An intent is a sentence about a morning's work,
                        and a directory of those names places without describing
                        any of them. */}
                    <th>What this is</th>
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
                      <td>
                        {p.description || <span className="faint">not yet described</span>}
                      </td>
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
                  <li key={p.project}>
                    <ProjectLink project={p.project} />
                    {p.description && <div className="subtle">{p.description}</div>}
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
