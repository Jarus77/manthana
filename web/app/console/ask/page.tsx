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
import { Loading } from '@/components/Loader'
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

      <form onSubmit={ask} className="form-row">
        <div className="grow">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What has the team been working on?"
            aria-label="Question"
          />
        </div>
        <select
          aria-label="Sources"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">All sources</option>
          <option value="full">Full digests only</option>
          <option value="claude_summary">Claude summaries only</option>
        </select>
        <button
          type="submit"
          className="button button-progressive"
          disabled={busy || !query.trim()}
        >
          {busy ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {quota && <QuotaNotice quota={quota} />}
      {failure && <div className="error-box">{failure}</div>}
      {busy && <Loading />}

      {answer && (
        <>
          {answer.insufficient ? (
            <div className="ambox ambox-content">
              <b>Not enough evidence to answer this.</b>
              <p>
                Too few people contributed to what this question asks about, so answering it
                would describe one person&rsquo;s work as a team pattern. Nothing was
                returned, and the question is on the record either way.
              </p>
            </div>
          ) : (
            <>
              <h2>Answer</h2>
              <p style={{ whiteSpace: 'pre-wrap' }}>{answer.narrative}</p>

              {answer.coverage && <p className="faint">{answer.coverage}</p>}

              {answer.citations.length > 0 && (
                <>
                  <h2>Sources</h2>
                  <ul className="reflist">
                    {answer.citations.map((c) => (
                      <li key={c} className="mono">
                        {c}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {answer.rollup && (
                <>
                  <h2>What it looked at</h2>
                  <div className="stat-row">
                    {[
                      ['Sessions', String(answer.rollup.sessions)],
                      ['Contributors', String(answer.rollup.contributors)],
                      ['Tokens', answer.rollup.tokens.toLocaleString()],
                      ['Cost', usd(answer.rollup.cost_usd)],
                    ].map(([label, value]) => (
                      <div key={label} className="stat">
                        <span className="stat-value">{value}</span>
                        <span className="stat-label">{label}</span>
                      </div>
                    ))}
                  </div>
                  <p className="faint">
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
        <div className="empty">
          Ask in plain language. Answers come back grounded in real sessions, with the
          evidence attached — and say so when there is not enough.
        </div>
      )}
    </>
  )
}
