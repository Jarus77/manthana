'use client'

/** Index of projects. */

import { Wiki } from '@/components/Loader'
import {
  Empty,
  clip,
  PersonList,
  ProjectLink,
  Section,
  Title,
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
          <p className="mb-4">
            Every project in <b>{data.org_id}</b> that engineers have released sessions against.
          </p>

          <Section title="Active">
            {data.active.length ? (
              <div className="overflow-x-auto rounded-lg border"><Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Project</TableHead>
                    <TableHead>Latest work</TableHead>
                    <TableHead>Contributors</TableHead>
                    <TableHead>Sessions</TableHead>
                    <TableHead>Last active</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.active.map((p) => (
                    <TableRow key={p.project}>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>
                        <ProjectLink project={p.project} />
                      </TableCell>
                      <TableCell>{p.top_intent ? clip(p.top_intent) : <span className="text-sm text-muted-foreground">—</span>}</TableCell>
                      <TableCell>
                        <PersonList actors={p.actors} />
                      </TableCell>
                      <TableCell>{p.sessions}</TableCell>
                      <TableCell style={{ whiteSpace: 'nowrap' }}>{when(p.last_active)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table></div>
            ) : (
              <Empty>No project has seen a released session recently.</Empty>
            )}
          </Section>

          {data.quiet.length > 0 && (
            <Section title="Dormant">
              <p className="text-muted-foreground">Old, but still reachable.</p>
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
