'use client'

/**
 * Search results.
 *
 * Citations come back as full objects, so every source is a readable, clickable
 * reference rather than an id to go look up. The coverage line stays visible:
 * an answer drawn from two entries and one drawn from twenty should not look
 * identical.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { AskBar } from '@/components/AskBar'
import { ErrorBox, Loading } from '@/components/Loader'
import {
  Empty,
  NoteRow,
  Reflist,
  Section,
  Title,
} from '@/components/primitives'
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
      <Title tagline="Search results">{query || 'Search'}</Title>
      <AskBar initial={query} />

      {!query && <Empty>Enter a question to search the team&rsquo;s knowledge.</Empty>}
      {loading && <Loading />}
      {error && <ErrorBox error={error} />}

      {result && (
        <>
          <div style={{ whiteSpace: 'pre-wrap', marginBottom: '0.6em' }}>{result.narrative}</div>
          <p className="faint">
            {result.coverage}
            {result.drilled && ' — sessions were read because the entries were thin'}
          </p>

          {result.notes.length > 0 && (
            <Section title="Entries cited">
              <ul>
                {result.notes.map((n) => (
                  <NoteRow key={n.id} note={n} />
                ))}
              </ul>
            </Section>
          )}

          {result.sessions.length > 0 && (
            <Section title="Sessions cited">
              <Reflist sessions={result.sessions} />
            </Section>
          )}

          {result.insufficient_data && (
            <Empty>
              Nothing in the wiki covers this yet — try a broader question, or add what you know
              from any entry page.
            </Empty>
          )}
        </>
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
