'use client'

/**
 * Ask the team's own history a question.
 *
 * Two states this page must be honest about, because both are easy to dress up as
 * an answer:
 *
 *   WITHHELD. The evidence did not clear the k-anonymity floor, so nothing is
 *   returned. That is the privacy contract working, not a failure — and saying so
 *   plainly is the difference between a founder trusting the floor and thinking
 *   the product is broken.
 *
 *   COVERAGE. An answer drawn from a truncated set is a partial answer, and one
 *   that does not say so is a wrong one.
 */

import { useState } from 'react'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { QuotaNotice, quotaFrom, type QuotaDetail } from '@/components/console/QuotaNotice'
import { usd } from '@/components/console/charts'
import { EmptyState, Mono, Notice, SectionHeading } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, consolePost } from '@/lib/api'

type Answer = {
  org_id: string
  query: string
  insufficient: boolean
  narrative: string
  citations: string[]
  coverage: string | null
  rollup: {
    sessions: number
    contributors: number
    tokens: number
    cost_usd: number
    by_project: Record<string, number>
    by_outcome: Record<string, number>
    by_engineer?: Record<string, number>
  } | null
}

export default function AskPage() {
  const org = useOrgId()
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [quota, setQuota] = useState<QuotaDetail | null>(null)
  const [failure, setFailure] = useState('')

  async function ask(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setAnswer(null)
    setQuota(null)
    setFailure('')
    try {
      setAnswer(await consolePost<Answer>('/query', { org_id: org, query, source }))
    } catch (err) {
      const q = quotaFrom(err)
      if (q) setQuota(q)
      else setFailure(err instanceof ApiError ? err.message : 'Something went wrong.')
    }
    setBusy(false)
  }

  return (
    <>
      <PageTitle aside="answers cite the sessions they came from">Ask</PageTitle>

      <form onSubmit={ask} className="mb-6 flex flex-wrap gap-2">
        <Input
          className="min-w-64 flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What has the team been working on?"
          aria-label="Question"
        />
        <select
          aria-label="Sources"
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">All sources</option>
          <option value="full">Full digests only</option>
          <option value="claude_summary">Claude summaries only</option>
        </select>
        <Button type="submit" disabled={busy || !query.trim()}>
          {busy ? 'Asking…' : 'Ask'}
        </Button>
      </form>

      {quota && (
        <div className="mb-6">
          <QuotaNotice quota={quota} />
        </div>
      )}
      {failure && (
        <div className="mb-6">
          <Notice tone="disputed">{failure}</Notice>
        </div>
      )}
      {busy && <Skeleton className="h-40 w-full" />}

      {answer && (
        <>
          {answer.insufficient ? (
            <Notice tone="unreviewed" title="Not enough evidence to answer this.">
              Too few people contributed to what this question asks about, so answering it
              would describe one person&rsquo;s work as a team pattern. Nothing was returned,
              and the question is on the record either way.
            </Notice>
          ) : (
            <>
              <SectionHeading>Answer</SectionHeading>
              <p className="whitespace-pre-wrap text-sm">{answer.narrative}</p>

              {answer.coverage && (
                <p className="mt-3 text-xs text-muted-foreground">{answer.coverage}</p>
              )}

              {answer.citations.length > 0 && (
                <>
                  <SectionHeading>Sources</SectionHeading>
                  <ul className="space-y-1 text-sm">
                    {answer.citations.map((c) => (
                      <li key={c}>
                        <Mono>{c}</Mono>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {answer.rollup && (
                <>
                  <SectionHeading>What it looked at</SectionHeading>
                  <div className="grid gap-4 sm:grid-cols-4">
                    {[
                      ['Sessions', String(answer.rollup.sessions)],
                      ['Contributors', String(answer.rollup.contributors)],
                      ['Tokens', answer.rollup.tokens.toLocaleString()],
                      ['Cost', usd(answer.rollup.cost_usd)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border p-4">
                        <div className="text-xs text-muted-foreground">{label}</div>
                        <div className="mt-1 text-lg font-semibold tabular">{value}</div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-3 text-xs text-muted-foreground">
                    Cost is the API list-price equivalent for the sessions this answer drew
                    on — not a bill.
                  </p>
                </>
              )}
            </>
          )}
        </>
      )}

      {!answer && !busy && !quota && !failure && (
        <EmptyState>
          Ask in plain language. Answers come back grounded in real sessions, with the
          evidence attached — and say so when there is not enough.
        </EmptyState>
      )}
    </>
  )
}
