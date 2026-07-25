'use client'

/**
 * Encyclopedia furniture — TRANSITIONAL.
 *
 * These are the parts a Wikipedia article is assembled from — hatnote, infobox,
 * maintenance banner, table of contents, reference list, category footer — plus
 * the small inline pieces (links to people, projects, sessions).
 *
 * The 13 wiki pages still import from here and still render against
 * `app/legacy-wiki.css`. Phase 5 moves them onto `components/manthana/*`, which
 * is the same vocabulary rebuilt on shadcn, and deletes this file.
 *
 * The formatting rules that used to live here now live in `lib/format.ts` and are
 * re-exported below, so nothing importing them had to change. That split is the
 * point: the wording decisions are pure functions and must survive the restyle
 * untouched, while everything visual below is scheduled for replacement.
 */

import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  PENDING_TITLE,
  clip,
  isPending,
  leadParagraph,
  onDate,
  restOfBody,
  sessionTitle,
  shortName,
  when,
} from '@/lib/format'
import type { Note, NoteStatus, Session } from '@/lib/types'

export { PENDING_TITLE, clip, isPending, leadParagraph, onDate, restOfBody, sessionTitle, shortName, when }

/* ── inline links ───────────────────────────────────────────────────────── */

export function PersonLink({ actor }: { actor: string }) {
  return <Link href={`/people/${encodeURIComponent(actor)}`}>{shortName(actor)}</Link>
}

export function ProjectLink({ project }: { project: string }) {
  if (!project) return <span className="faint">none</span>
  return <Link href={`/projects/${encodeURIComponent(project)}`}>{project}</Link>
}

export function SessionLink({ session }: { session: Session }) {
  return <Link href={`/sessions/${session.id}`}>{sessionTitle(session)}</Link>
}

/** Comma-separated links, with "and" before the last — reads as a sentence. */
export function PersonList({ actors }: { actors: string[] }) {
  if (!actors.length) return <span className="faint">nobody</span>
  return (
    <>
      {actors.map((a, i) => (
        <span key={a}>
          {i > 0 && (i === actors.length - 1 ? ' and ' : ', ')}
          <PersonLink actor={a} />
        </span>
      ))}
    </>
  )
}

/* ── article furniture ──────────────────────────────────────────────────── */

export function Title({
  children,
  tagline,
}: {
  children: React.ReactNode
  tagline?: React.ReactNode
}) {
  return (
    <>
      <h1 className="firstHeading">{children}</h1>
      <p className="tagline">{tagline ?? 'From the Manthana wiki'}</p>
    </>
  )
}

export function Hatnote({ children }: { children: React.ReactNode }) {
  return <div className="hatnote">{children}</div>
}

/**
 * Maintenance banner. Manthana's editorial states map onto MediaWiki's ambox
 * almost exactly — an unreviewed AI claim is an unsourced article, and a
 * disputed one is a contradiction notice — so the notice says what is wrong and
 * what would fix it, instead of leaving a reader to decode a coloured dot.
 */
export function Ambox({
  kind = 'content',
  children,
}: {
  kind?: 'content' | 'style' | 'serious'
  children: React.ReactNode
}) {
  return <div className={`ambox ambox-${kind}`}>{children}</div>
}

export function NoteBanners({ note }: { note: Note }) {
  return (
    <>
      {note.status === 'disputed' && (
        <Ambox kind="serious">
          <b>The accuracy of this entry is disputed.</b> Later sessions contradict it. The
          conflicting evidence is listed under <a href="#disputed">Conflicting evidence</a>;
          correcting the text resolves the dispute.
        </Ambox>
      )}
      {note.status === 'candidate' && (
        <Ambox kind="content">
          <b>This entry has not been reviewed by a human.</b> Manthana wrote it from the
          sessions cited below. Read it against that evidence, then correct or confirm it.
        </Ambox>
      )}
      {note.status === 'stale' && (
        <Ambox>
          <b>The evidence behind this entry has been purged.</b> It is kept because nobody has
          disputed it, but the sessions it came from can no longer be read.
        </Ambox>
      )}
    </>
  )
}

export function Infobox({
  title,
  subtitle,
  rows,
}: {
  title: string
  subtitle?: string
  rows: Array<[string, React.ReactNode]>
}) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '')
  return (
    <div className="infobox">
      <div className="infobox-title">{title}</div>
      {subtitle && <div className="infobox-sub">{subtitle}</div>}
      <table>
        <tbody>
          {shown.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Contents box. Rendered only with enough sections to be worth navigating. */
export function Toc({ sections }: { sections: Array<{ id: string; label: string }> }) {
  if (sections.length < 3) return null
  return (
    <nav className="toc">
      <div className="toc-title">Contents</div>
      <ol>
        {sections.map((s) => (
          <li key={s.id}>
            <a href={`#${s.id}`}>{s.label}</a>
          </li>
        ))}
      </ol>
    </nav>
  )
}

export function Section({
  id,
  title,
  action,
  children,
}: {
  id?: string
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section>
      <h2 id={id}>
        {title}
        {action && <span className="editsection">{action}</span>}
      </h2>
      {children}
    </section>
  )
}

/** Sources, as a numbered reference list. */
export function Reflist({ sessions }: { sessions: Session[] }) {
  if (!sessions.length) return null
  return (
    <ol className="reflist">
      {sessions.map((s) => (
        <li key={s.id} id={`ref-${s.id}`}>
          <SessionLink session={s} /> — <PersonLink actor={s.actor} />,{' '}
          <ProjectLink project={s.project} />, {onDate(s.started_at)} ({s.outcome})
        </li>
      ))}
    </ol>
  )
}

export function Citation({ n, id }: { n: number; id: string }) {
  return (
    <sup className="reference">
      <a href={`#ref-${id}`}>[{n}]</a>
    </sup>
  )
}

export function CatLinks({ categories }: { categories: Array<{ label: string; href?: string }> }) {
  if (!categories.length) return null
  return (
    <div className="catlinks">
      <b>Categories</b>:{' '}
      <ul>
        {categories.map((c) => (
          <li key={c.label}>{c.href ? <Link href={c.href}>{c.label}</Link> : c.label}</li>
        ))}
      </ul>
    </div>
  )
}

/** Status as text with the wiki's editorial colours — active green, stale amber.
 *  Computed from timestamps server-side; this only words it. */
export function StatusWord({ status }: { status: 'active' | 'stale' }) {
  return (
    <span className={status === 'active' ? 'status-confirmed' : 'status-unreviewed'}>
      {status}
    </span>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

/* ── list rows ──────────────────────────────────────────────────────────── */

/**
 * Legacy wording + legacy CSS class, kept so the unported pages keep rendering.
 * `lib/format.ts`'s `statusWord` is the replacement: it returns a product TONE
 * (`distilled`, `warning`, `danger`) rather than a stylesheet class name, so the
 * meaning no longer travels as a colour.
 */
export function statusWord(note: Note): { text: string; cls: string } | null {
  const map: Partial<Record<NoteStatus, { text: string; cls: string }>> = {
    candidate: { text: 'unreviewed', cls: 'status-unreviewed' },
    disputed: { text: 'disputed', cls: 'status-disputed' },
    stale: { text: 'stale', cls: 'faint' },
  }
  const s = map[note.status]
  if (s) return s
  if (note.confirmed_by) return { text: 'confirmed', cls: 'status-confirmed' }
  return null
}

/** One note in a list: title link, one-line gloss, provenance. */
export function NoteRow({ note, movedFrom }: { note: Note; movedFrom?: string }) {
  const status = statusWord(note)
  const gloss = note.body.length > 200 ? `${note.body.slice(0, 200).trimEnd()}…` : note.body
  return (
    <li style={{ marginBottom: '0.6em' }}>
      <Link href={`/notes/${note.id}`}>{note.title}</Link>
      {note.kind === 'benchmark' && note.value && (
        <>
          {' '}
          —{' '}
          <span className="mono">
            {movedFrom && `${movedFrom} → `}
            <b>{note.value}</b>
          </span>
        </>
      )}
      {status && <> ({<span className={status.cls}>{status.text}</span>})</>}
      <div className="subtle">{gloss}</div>
    </li>
  )
}

export function SessionRow({ session }: { session: Session }) {
  return (
    <li style={{ marginBottom: '0.35em' }}>
      <SessionLink session={session} /> — <PersonLink actor={session.actor} />,{' '}
      <ProjectLink project={session.project} />, {when(session.started_at)}{' '}
      <span className="faint">
        ({isPending(session) ? 'not yet summarised' : session.outcome})
      </span>
    </li>
  )
}
