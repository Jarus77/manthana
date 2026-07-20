'use client'

/**
 * The small vocabulary every page is built from.
 *
 * The rule these enforce: an entity is never plain text. A person, a project, a
 * session and a note each render as something clickable, everywhere they
 * appear — that is what makes the wiki traversable rather than a set of reports
 * that happen to mention the same names.
 */

import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Note, NoteStatus, Session } from '@/lib/types'

/** Local part of an org email — the name colleagues actually call each other. */
export function shortName(actor: string): string {
  return actor.split('@')[0] || actor
}

export function when(iso: string): string {
  const then = new Date(iso)
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d ago`
  if (days < 365) return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return then.toLocaleDateString(undefined, { year: 'numeric', month: 'short' })
}

export function PersonChip({ actor }: { actor: string }) {
  return (
    <Link className="chip chip-person" href={`/people/${encodeURIComponent(actor)}`}>
      {shortName(actor)}
    </Link>
  )
}

export function ProjectChip({ project }: { project: string }) {
  if (!project) return null
  return (
    <Link className="chip" href={`/projects/${encodeURIComponent(project)}`}>
      {project}
    </Link>
  )
}

export function OutcomePill({ outcome }: { outcome: string }) {
  const known = ['success', 'partial', 'abandoned'].includes(outcome)
  return (
    <span className={`outcome ${known ? `outcome-${outcome}` : ''}`}>{outcome}</span>
  )
}

/**
 * Editorial-trust badges. Reading order matters: the status a reader most needs
 * (disputed, unreviewed) comes first, provenance second.
 */
export function StatusBadge({ note }: { note: Note }) {
  const label: Record<NoteStatus, [string, string]> = {
    candidate: ['unreviewed', 'badge-unreviewed'],
    established: ['established', 'badge-neutral'],
    disputed: ['disputed', 'badge-disputed'],
    stale: ['stale', 'badge-neutral'],
    superseded: ['superseded', 'badge-neutral'],
  }
  const [text, cls] = label[note.status] ?? ['', 'badge-neutral']
  return (
    <span className="row row-tight">
      <span className={`badge ${cls}`}>{text}</span>
      {note.source === 'human' && <span className="badge badge-human">human</span>}
      {note.confirmed_by && <span className="badge badge-confirmed">confirmed</span>}
    </span>
  )
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

/**
 * A note as it appears in any list. Title links; body is a two-line preview.
 *
 * `movedFrom` renders a benchmark's previous value beside its current one. That
 * delta is the whole reading of a benchmark — "64%" alone doesn't say whether
 * the week was good — so it sits inline with the value rather than as a
 * footnote under the card.
 */
export function NoteCard({ note, movedFrom }: { note: Note; movedFrom?: string }) {
  const preview = note.body.length > 220 ? `${note.body.slice(0, 220).trimEnd()}…` : note.body
  return (
    <div className="card card-tight">
      <div className="row" style={{ marginBottom: 4 }}>
        <Link href={`/notes/${note.id}`} style={{ fontWeight: 550 }}>
          {note.title}
        </Link>
        <StatusBadge note={note} />
        {note.kind === 'benchmark' && note.value && (
          <span className="mono muted">
            {movedFrom && (
              <>
                <span style={{ textDecoration: 'line-through' }}>{movedFrom}</span> →{' '}
              </>
            )}
            <b>{note.value}</b>
          </span>
        )}
      </div>
      <div className="muted">{preview}</div>
      <div className="row row-tight faint" style={{ marginTop: 7 }}>
        {note.project && <ProjectChip project={note.project} />}
        {note.actors.slice(0, 3).map((a) => (
          <PersonChip key={a} actor={a} />
        ))}
        <span>updated {when(note.updated_at)}</span>
      </div>
    </div>
  )
}

/** One line of the discovery stream: who, what, how it went, where. */
export function StreamItem({ session }: { session: Session }) {
  return (
    <div className="stream-item">
      <div className="stream-when">
        <PersonChip actor={session.actor} />
        <div style={{ marginTop: 3 }}>{when(session.started_at)}</div>
      </div>
      <div>
        <Link className="stream-intent" href={`/sessions/${session.id}`}>
          {session.task_intent || 'untitled session'}
        </Link>
        <div className="row row-tight faint" style={{ marginTop: 5 }}>
          <OutcomePill outcome={session.outcome} />
          <ProjectChip project={session.project} />
          {session.prs_opened.length > 0 && <span>{session.prs_opened.length} PR</span>}
          {session.files_touched.length > 0 && (
            <span>{session.files_touched.length} files</span>
          )}
        </div>
      </div>
    </div>
  )
}

/** A session in a list context — same content, boxed rather than streamed. */
export function SessionCard({ session }: { session: Session }) {
  return (
    <div className="card card-tight">
      <div className="row" style={{ marginBottom: 5 }}>
        <Link href={`/sessions/${session.id}`} style={{ fontWeight: 550 }}>
          {session.task_intent || 'untitled session'}
        </Link>
        <OutcomePill outcome={session.outcome} />
      </div>
      <div className="row row-tight faint">
        <PersonChip actor={session.actor} />
        <ProjectChip project={session.project} />
        <span>{when(session.started_at)}</span>
      </div>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>
}

export function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Crumbs({ trail }: { trail: Array<{ label: string; href?: string }> }) {
  return (
    <nav className="crumbs">
      {trail.map((c) => (
        <span key={c.label}>{c.href ? <Link href={c.href}>{c.label}</Link> : c.label}</span>
      ))}
    </nav>
  )
}
