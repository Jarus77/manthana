'use client'

/**
 * The wiki's vocabulary, on the design system.
 *
 * These are the parts an article is assembled from — a maintenance notice, a facts
 * card, a contents list, a reference list, a category footer — plus the small
 * inline pieces (links to people, projects, sessions).
 *
 * The names are kept from the encyclopedia version because they carry domain
 * meaning that generic component names lose: `Ambox` is a notice about an ENTRY's
 * trustworthiness, `Reflist` is the sessions a claim stands on, `Citation` is the
 * superscript that points at one. What changed is the styling underneath, not what
 * any of them means.
 *
 * The formatting rules live in `lib/format.ts` and are re-exported here, so the
 * wording survives the restyle untouched — that split is exactly why this file
 * could be rewritten without re-deciding how a session gets titled.
 */

import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Citations, EmptyState, Mono, Notice, Tag } from '@/components/manthana'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import { cn } from '@/lib/utils'

export {
  PENDING_TITLE, clip, isPending, leadParagraph, onDate, restOfBody, sessionTitle,
  shortName, when,
}

/* ── inline links ───────────────────────────────────────────────────────── */

const LINK = 'text-primary underline-offset-4 hover:underline'

export function PersonLink({ actor }: { actor: string }) {
  return (
    <Link className={LINK} href={`/people/${encodeURIComponent(actor)}`}>
      {shortName(actor)}
    </Link>
  )
}

export function ProjectLink({ project }: { project: string }) {
  if (!project) return <span className="text-muted-foreground">none</span>
  return (
    <Link className={LINK} href={`/projects/${encodeURIComponent(project)}`}>
      {project}
    </Link>
  )
}

export function SessionLink({ session }: { session: Session }) {
  return (
    <Link className={LINK} href={`/sessions/${session.id}`}>
      {sessionTitle(session)}
    </Link>
  )
}

/** Comma-separated links, with "and" before the last — reads as a sentence. */
export function PersonList({ actors }: { actors: string[] }) {
  if (!actors.length) return <span className="text-muted-foreground">nobody</span>
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
    <div className="mb-6 border-b pb-3">
      <h1 className="text-2xl font-semibold tracking-tight">{children}</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {tagline ?? 'From the Manthana wiki'}
      </p>
    </div>
  )
}

/** Context above the article proper. */
export function Hatnote({ children }: { children: React.ReactNode }) {
  return <p className="mb-4 pl-6 text-sm italic text-muted-foreground">{children}</p>
}

/**
 * A notice about the ENTRY's trustworthiness — not about the reader's last action.
 * Kept as a left-edge rule rather than a filled panel: these sit above article
 * text, and a saturated block at the top of every unreviewed note would make the
 * common case look like an error.
 */
export function Ambox({
  kind = 'content',
  children,
}: {
  kind?: 'content' | 'style' | 'serious'
  children: React.ReactNode
}) {
  const tone = kind === 'serious' ? 'disputed' : kind === 'content' ? 'unreviewed' : 'info'
  return <Notice tone={tone}>{children}</Notice>
}

export function NoteBanners({ note }: { note: Note }) {
  return (
    <div className="mb-4 space-y-3">
      {note.status === 'disputed' && (
        <Notice tone="disputed" title="The accuracy of this entry is disputed.">
          Later sessions contradict it. The conflicting evidence is listed under{' '}
          <a className={LINK} href="#disputed">
            Conflicting evidence
          </a>
          ; correcting the text resolves the dispute.
        </Notice>
      )}
      {note.status === 'candidate' && (
        <Notice tone="unreviewed" title="This entry has not been reviewed by a human.">
          Manthana wrote it from the sessions cited below. Read it against that evidence,
          then correct or confirm it.
        </Notice>
      )}
      {note.status === 'stale' && (
        <Notice title="The evidence behind this entry has been purged.">
          It is kept because nobody has disputed it, but the sessions it came from can no
          longer be read.
        </Notice>
      )}
    </div>
  )
}

/** Key facts about a subject, floated beside the article on wide screens. */
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
    <Card className="mb-6 w-full lg:float-right lg:clear-right lg:ml-6 lg:w-80">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[minmax(0,8rem)_1fr] gap-x-4 gap-y-2 text-sm">
          {shown.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

/** Contents. Rendered only with enough sections to be worth navigating. */
export function Toc({ sections }: { sections: Array<{ id: string; label: string }> }) {
  if (sections.length < 3) return null
  return (
    <nav className="mb-6 inline-block rounded-lg border bg-muted/40 px-5 py-3">
      <div className="mb-1 text-sm font-medium">Contents</div>
      <ol className="list-decimal space-y-0.5 pl-5 text-sm">
        {sections.map((s) => (
          <li key={s.id}>
            <a className={LINK} href={`#${s.id}`}>
              {s.label}
            </a>
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
    <section className="clear-right">
      <div className="mt-8 mb-3 flex items-baseline justify-between gap-4 border-b pb-2">
        <h2 id={id} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        {action && <div className="text-sm text-muted-foreground">{action}</div>}
      </div>
      {children}
    </section>
  )
}

/** Sources — the sessions a claim stands on. */
export function Reflist({ sessions }: { sessions: Session[] }) {
  if (!sessions.length) return null
  return (
    <Citations>
      {sessions.map((s) => (
        <li key={s.id} id={`ref-${s.id}`}>
          <SessionLink session={s} /> — <PersonLink actor={s.actor} />,{' '}
          <ProjectLink project={s.project} />, {onDate(s.started_at)} ({s.outcome})
        </li>
      ))}
    </Citations>
  )
}

export function Citation({ n, id }: { n: number; id: string }) {
  return (
    <sup className="ml-0.5 align-super text-[0.7em] leading-none">
      <a className="text-primary hover:underline" href={`#ref-${id}`}>
        [{n}]
      </a>
    </sup>
  )
}

export function CatLinks({ categories }: { categories: Array<{ label: string; href?: string }> }) {
  if (!categories.length) return null
  return (
    <div className="mt-10 flex flex-wrap items-center gap-2 border-t pt-4 text-sm">
      <span className="text-muted-foreground">Categories</span>
      {categories.map((c) => (
        <Tag key={c.label}>
          {c.href ? (
            <Link className="hover:underline" href={c.href}>
              {c.label}
            </Link>
          ) : (
            c.label
          )}
        </Tag>
      ))}
    </div>
  )
}

/** Project status as a word, computed server-side from timestamps. */
export function StatusWord({ status }: { status: 'active' | 'stale' }) {
  return (
    <span className={cn('font-medium', status === 'active' ? 'text-success' : 'text-warning')}>
      {status}
    </span>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <EmptyState>{children}</EmptyState>
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-manthana">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

/* ── list rows ──────────────────────────────────────────────────────────── */

/** Human wording for an editorial state. Tone names a product state, not a colour. */
export function statusWord(note: Note): { text: string; cls: string } | null {
  const map: Partial<Record<NoteStatus, { text: string; cls: string }>> = {
    candidate: { text: 'unreviewed', cls: 'text-warning' },
    disputed: { text: 'disputed', cls: 'text-destructive' },
    stale: { text: 'stale', cls: 'text-muted-foreground' },
  }
  const s = map[note.status]
  if (s) return s
  if (note.confirmed_by) return { text: 'confirmed', cls: 'text-distilled' }
  return null
}

/** One note in a list: title link, one-line gloss, provenance. */
export function NoteRow({ note, movedFrom }: { note: Note; movedFrom?: string }) {
  const status = statusWord(note)
  const gloss = note.body.length > 200 ? `${note.body.slice(0, 200).trimEnd()}…` : note.body
  return (
    <li className="mb-3">
      <Link className={LINK} href={`/notes/${note.id}`}>
        {note.title}
      </Link>
      {note.kind === 'benchmark' && note.value && (
        <>
          {' — '}
          <Mono>
            {movedFrom && `${movedFrom} → `}
            <b>{note.value}</b>
          </Mono>
        </>
      )}
      {status && (
        <>
          {' ('}
          <span className={cn('font-medium', status.cls)}>{status.text}</span>
          {')'}
        </>
      )}
      <div className="text-sm text-muted-foreground">{gloss}</div>
    </li>
  )
}

export function SessionRow({ session }: { session: Session }) {
  return (
    <li className="mb-1.5">
      <SessionLink session={session} /> — <PersonLink actor={session.actor} />,{' '}
      <ProjectLink project={session.project} />, {when(session.started_at)}{' '}
      <span className="text-muted-foreground">
        ({isPending(session) ? 'not yet summarised' : session.outcome})
      </span>
    </li>
  )
}
