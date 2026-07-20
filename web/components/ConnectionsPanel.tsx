'use client'

/**
 * The "computer brain" panel: who and what this page connects to.
 *
 * Every edge shows its reason. An unexplained "related to Mira" is a claim the
 * reader cannot check and will learn to ignore; "bench, 2 shared notes" is one
 * they can act on. Edges come precomputed in the page payload (server/graph.py),
 * so this component never fetches.
 */

import Link from 'next/link'
import { edgeReason, type PersonEdge, type ProjectEdge } from '@/lib/types'
import { Empty, shortName } from './primitives'

export function PeopleConnections({ edges }: { edges: PersonEdge[] }) {
  if (!edges.length) {
    return (
      <div className="panel">
        <div className="nav-label" style={{ padding: '0 0 6px' }}>
          Works with
        </div>
        <Empty>No shared projects, notes, or files yet.</Empty>
      </div>
    )
  }
  return (
    <div className="panel">
      <div className="nav-label" style={{ padding: '0 0 6px' }}>
        Works with
      </div>
      {/* Top few only. At a ten-person startup nearly everyone shares a project
          with everyone, so a complete list ranks as noise — the value is in the
          strongest links, which the weighting already sorts to the top. */}
      <div className="stack">
        {edges.slice(0, 5).map((edge) => (
          <div key={edge.actor}>
            <Link
              href={`/people/${encodeURIComponent(edge.actor)}`}
              style={{ fontWeight: 550, fontSize: 14 }}
            >
              {shortName(edge.actor)}
            </Link>
            <div className="faint">{edgeReason(edge)}</div>
            {/* Titles, not ids: the point of the panel is to say what the two
                of them worked on, and an id says nothing a reader can use. */}
            {edge.via_notes.length > 0 && (
              <ul className="edge-notes">
                {edge.via_notes.slice(0, 2).map((ref) => (
                  <li key={ref.id}>
                    <Link href={`/notes/${ref.id}`}>{ref.title}</Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export function ProjectConnections({ edges }: { edges: ProjectEdge[] }) {
  if (!edges.length) return null
  return (
    <div className="panel">
      <div className="nav-label" style={{ padding: '0 0 6px' }}>
        Related projects
      </div>
      <div className="stack">
        {edges.map((edge) => (
          <div key={edge.project}>
            <Link
              href={`/projects/${encodeURIComponent(edge.project)}`}
              style={{ fontWeight: 550, fontSize: 14 }}
            >
              {edge.project}
            </Link>
            <div className="faint">
              shared with {edge.via_actors.map(shortName).join(', ')}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
