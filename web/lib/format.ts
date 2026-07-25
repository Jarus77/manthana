/**
 * Formatting and naming rules — the wording decisions, with no JSX attached.
 *
 * Split out of `components/primitives.tsx` so that restyling the interface cannot
 * accidentally reword it. Everything here encodes a decision that was made for a
 * reason and is easy to "tidy" back into a bug:
 *
 *   - `clip` cuts at a word boundary because cutting at a character count
 *     produced the "…at the architec" endings that made the wiki look broken.
 *   - `sessionTitle` refuses to show a pending digest's raw first prompt as a
 *     title, because that text is the engineer's literal typing and reads as
 *     gibberish everywhere except the session's own page.
 *   - `shortName` shows the local part of an email because that is what
 *     colleagues actually call each other.
 *
 * No React import here on purpose: this file is pure and directly testable.
 */

import type { Note, NoteStatus, Session } from '@/lib/types'

/** Local part of an org email — what colleagues actually call each other. */
export function shortName(actor: string): string {
  return actor.split('@')[0] || actor
}

export function when(iso: string): string {
  const then = new Date(iso)
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} minutes ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} hours ago`
  const days = Math.round(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return then.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Clip text at a WORD boundary.
 *
 * An unenriched digest's `task_intent` is the engineer's raw first prompt, which
 * can be a paragraph. Cutting it at a character count produced the "…at the
 * architec" endings that made the wiki look broken; cutting at a space at least
 * ends on a word. Enrichment replaces these with real summaries, so this is the
 * floor, not the goal.
 */
export function clip(text: string, max = 120): string {
  const t = (text ?? '').trim().replace(/\s+/g, ' ')
  if (t.length <= max) return t
  const cut = t.slice(0, max)
  const space = cut.lastIndexOf(' ')
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[,;:]$/, '')}…`
}

/** First paragraph of a markdown body — the overview prompt requires it to be a
 *  self-contained sentence, so it can stand alone as an article lead. */
export function leadParagraph(body: string): string {
  return (body ?? '').trim().split(/\n\s*\n/)[0] ?? ''
}

export function restOfBody(body: string): string {
  const parts = (body ?? '').trim().split(/\n\s*\n/)
  return parts.slice(1).join('\n\n').trim()
}

export function onDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * A `pending` digest has no summary yet — its `task_intent` is the engineer's
 * literal first prompt. Rendered as a title that reads as gibberish and breaks
 * mid-word, so everywhere EXCEPT the session's own page and the verbatim page it
 * is replaced by this. The raw text is not hidden, just moved somewhere it is
 * labelled for what it is.
 */
export const PENDING_TITLE = 'Untitled session — awaiting summary'

export function isPending(session: Session): boolean {
  return session.source === 'pending'
}

export function sessionTitle(session: Session): string {
  if (isPending(session)) return PENDING_TITLE
  return clip(session.task_intent) || session.session_id
}

/**
 * Human wording for an editorial state.
 *
 * `tone` names a product state rather than a colour, so the palette can change
 * without the meaning moving with it: `distilled` is knowledge a human confirmed,
 * `warning` is an unreviewed AI claim, `danger` is a contradicted one.
 */
export type StatusTone = 'distilled' | 'warning' | 'danger' | 'muted'

export function statusWord(note: Note): { text: string; tone: StatusTone } | null {
  const map: Partial<Record<NoteStatus, { text: string; tone: StatusTone }>> = {
    candidate: { text: 'unreviewed', tone: 'warning' },
    disputed: { text: 'disputed', tone: 'danger' },
    stale: { text: 'stale', tone: 'muted' },
  }
  const s = map[note.status]
  if (s) return s
  if (note.confirmed_by) return { text: 'confirmed', tone: 'distilled' }
  return null
}
