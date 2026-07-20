'use client'

/**
 * The four teaching verbs, as the reader meets them.
 *
 * The editorial contract this UI has to keep legible: a human write always
 * outranks the AI, and nothing is ever destroyed. So "Edit" says it publishes a
 * new version rather than overwriting, "Confirm" is offered only while a claim
 * is unvouched, and a revert is presented as restoring — not undoing — because
 * the bad version stays on the record either way.
 */

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { ApiError, post } from '@/lib/api'
import type { Note } from '@/lib/types'

function useAction(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

export function TeachControls({ note, onChanged }: { note: Note; onChanged: () => void }) {
  const router = useRouter()
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(note.title)
  const [body, setBody] = useState(note.body)
  const { busy, error, run } = useAction(onChanged)

  // Confirming is only meaningful for an unvouched AI claim; a human note is
  // already authoritative and a second endorsement would say nothing.
  const canConfirm = !note.confirmed_by && note.source !== 'human'

  if (editing) {
    return (
      <div className="panel">
        {error && <div className="error">{error}</div>}
        <div className="field">
          <label htmlFor="title">Title</label>
          <input id="title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="body">Body (markdown)</label>
          <textarea id="body" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div className="row">
          <button
            className="btn-primary"
            disabled={busy || !title.trim() || !body.trim()}
            onClick={() =>
              run(async () => {
                const res = await post<{ note: Note }>(`/notes/${note.id}/edit`, { title, body })
                setEditing(false)
                // An edit supersedes: the corrected claim lives at a new id, so
                // stay with the reader's content rather than a superseded page.
                if (res.note.id !== note.id) router.replace(`/notes/${res.note.id}`)
              })
            }
          >
            {busy ? 'Publishing…' : 'Publish correction'}
          </button>
          <button disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
        <p className="faint" style={{ marginTop: 10, marginBottom: 0 }}>
          Publishes a new version authored by you. The previous version stays in the
          history — nothing is overwritten.
        </p>
      </div>
    )
  }

  return (
    <div>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <button onClick={() => setEditing(true)}>Correct this</button>
        {canConfirm && (
          <button
            disabled={busy}
            onClick={() => run(() => post(`/notes/${note.id}/confirm`))}
          >
            {busy ? 'Confirming…' : 'Confirm as correct'}
          </button>
        )}
      </div>
    </div>
  )
}

/** Add knowledge that no session produced — what was only in someone's head. */
export function NewNoteForm({
  kinds,
  project = '',
  onCreated,
}: {
  kinds: string[]
  project?: string
  onCreated: () => void
}) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState(kinds[0] ?? 'decision')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const { busy, error, run } = useAction(onCreated)

  if (!open) return <button onClick={() => setOpen(true)}>Add knowledge</button>

  return (
    <div className="panel">
      {error && <div className="error">{error}</div>}
      <div className="field">
        <label htmlFor="kind">Kind</label>
        <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k.replace('_', ' ')}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="new-title">Title</label>
        <input
          id="new-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="the claim, in one line"
        />
      </div>
      <div className="field">
        <label htmlFor="new-body">Body (markdown)</label>
        <textarea
          id="new-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="why it's true, and what it means for anyone who hits this"
        />
      </div>
      <div className="row">
        <button
          className="btn-primary"
          disabled={busy || !title.trim() || !body.trim()}
          onClick={() =>
            run(async () => {
              const res = await post<{ note: Note }>('/notes', { kind, title, body, project })
              setOpen(false)
              router.push(`/notes/${res.note.id}`)
            })
          }
        >
          {busy ? 'Adding…' : 'Add'}
        </button>
        <button disabled={busy} onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  )
}
