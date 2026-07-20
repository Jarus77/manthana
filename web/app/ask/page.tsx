'use client'

/**
 * Ask results.
 *
 * Citations arrive as full objects, so every source is a card you can read and
 * click rather than an id you have to go look up. The coverage line stays
 * visible: an answer drawn from two notes and an answer drawn from twenty
 * should not look identical.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { AskBar } from '@/components/AskBar'
import { ErrorBox, Loading } from '@/components/Loader'
import { Empty, NoteCard, Section, SessionCard } from '@/components/primitives'
import { ApiError, post } from '@/lib/api'
import type { AskResult } from '@/lib/types'

function Results() {
  const search = useSearchParams()
  const query = search.get('q') ?? ''
  const [result, setResult] = useState<AskResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!query) return
    let live = true
    setLoading(true)
    setError(null)
    setResult(null)
    post<AskResult>('/ask', { query })
      .then((r) => live && setResult(r))
      .catch((e) => live && setError(e instanceof ApiError ? e : new ApiError(500, 'failed')))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [query])

  return (
    <>
      <h1 style={{ marginBottom: 14 }}>Ask</h1>
      <AskBar hero initial={query} />

      {!query && <Empty>Ask a question to search the team&rsquo;s knowledge.</Empty>}
      {loading && <Loading />}
      {error && <ErrorBox error={error} />}

      {result && (
        <div style={{ marginTop: 24 }}>
          <div className="card prose" style={{ whiteSpace: 'pre-wrap' }}>
            {result.narrative}
          </div>
          <p className="faint" style={{ marginTop: 8 }}>
            {result.coverage}
            {result.drilled && ' · drilled into sessions because the notes were thin'}
          </p>

          {result.notes.length > 0 && (
            <Section title="From the team&rsquo;s knowledge">
              {result.notes.map((n) => (
                <NoteCard key={n.id} note={n} />
              ))}
            </Section>
          )}

          {result.sessions.length > 0 && (
            <Section title="From sessions">
              {result.sessions.map((s) => (
                <SessionCard key={s.id} session={s} />
              ))}
            </Section>
          )}

          {result.insufficient_data && (
            <Empty>
              Nothing in the wiki covers this yet — try a broader question, or add what you
              know from a note page.
            </Empty>
          )}
        </div>
      )}
    </>
  )
}

export default function AskPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Results />
    </Suspense>
  )
}
