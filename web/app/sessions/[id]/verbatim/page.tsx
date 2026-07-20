'use client'

/**
 * The released compaction, verbatim.
 *
 * Deliberately a separate page reached by a link, not another block on the
 * session article. The session page is the *readable* account — intent,
 * approach, friction, what it touched — and that is what a colleague wants
 * ninety-nine times out of a hundred. This page is for the hundredth: checking
 * what was actually released, unedited, when the readable account looks wrong or
 * you are auditing what left your laptop.
 *
 * What is shown is the digest as stored, plus `native_summary` — the coding
 * agent's own compaction summary, which the structured fields were derived from.
 * Everything here was redacted on the way off the laptop.
 *
 * What is NOT shown, and never will be: the raw transcript. That stays behind
 * the audited, founder-only drill-down.
 */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import {
  Empty,
  Hatnote,
  PersonLink,
  ProjectLink,
  Title,
  onDate,
} from '@/components/primitives'
import type { SessionPage } from '@/lib/types'

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <tr>
      <th scope="row" style={{ whiteSpace: 'nowrap', verticalAlign: 'top' }}>
        {label}
      </th>
      <td>{value}</td>
    </tr>
  )
}

function ListCell({ items }: { items: string[] }) {
  if (!items.length) return <span className="faint">—</span>
  return (
    <ul style={{ margin: 0, paddingLeft: '1.2em' }} className="mono">
      {items.map((v, i) => (
        <li key={i}>{v}</li>
      ))}
    </ul>
  )
}

export default function VerbatimCompaction({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <Wiki<SessionPage> path={`/sessions/${encodeURIComponent(id)}`}>
      {(data) => {
        const s = data.session
        return (
          <>
            <Title tagline="The released compaction, verbatim">{s.session_id}</Title>

            <Hatnote>
              For the readable account of this session, see{' '}
              <Link href={`/sessions/${s.id}`}>{s.task_intent || s.session_id}</Link>.
            </Hatnote>

            <p className="lead">
              This is the digest exactly as <PersonLink actor={s.actor} /> released it on{' '}
              {onDate(s.started_at)} — no summarising, no rewriting. Free text was redacted
              before it left their laptop. The raw transcript is not part of the wiki.
            </p>

            <h2>Agent summary</h2>
            {data.native_summary ? (
              <>
                <p className="subtle">
                  Written by the coding agent itself. The structured fields below were derived
                  from this.
                </p>
                <pre
                  style={{
                    whiteSpace: 'pre-wrap',
                    background: 'var(--neutral)',
                    border: '1px solid var(--border-subtle)',
                    padding: '1em',
                  }}
                >
                  {data.native_summary}
                </pre>
              </>
            ) : (
              <Empty>
                This session carried no agent summary
                {data.source === 'pending'
                  ? ' — its digest has not been enriched yet, so the fields below are the deterministic extraction.'
                  : '. The fields below were derived from the transcript instead.'}
              </Empty>
            )}

            <h2>Released fields</h2>
            <table className="wikitable">
              <tbody>
                <Field label="Compaction id" value={<span className="mono">{s.id}</span>} />
                <Field label="Session id" value={<span className="mono">{s.session_id}</span>} />
                <Field label="Engineer" value={<PersonLink actor={s.actor} />} />
                <Field label="Project" value={<ProjectLink project={s.project} />} />
                <Field label="Surface" value={s.surface} />
                <Field label="Started" value={new Date(s.started_at).toISOString()} />
                <Field
                  label="Duration"
                  value={`${Math.round(s.duration_seconds)} seconds`}
                />
                <Field label="Outcome" value={s.outcome} />
                <Field label="Task intent" value={s.task_intent || <span className="faint">—</span>} />
                <Field label="Approach" value={s.approach || <span className="faint">—</span>} />
                <Field label="Friction" value={<ListCell items={s.friction} />} />
                <Field label="Artifacts" value={<ListCell items={s.artifacts} />} />
                <Field label="Files touched" value={<ListCell items={s.files_touched} />} />
                <Field label="Pull requests" value={<ListCell items={s.prs_opened} />} />
                <Field label="Tests added" value={<ListCell items={s.tests_added} />} />
                <Field label="Languages" value={<ListCell items={s.languages} />} />
                <Field label="Model tier" value={s.tier_used ?? <span className="faint">—</span>} />
                <Field
                  label="Estimated cost"
                  value={s.est_cost_usd != null ? `$${s.est_cost_usd.toFixed(4)}` : '—'}
                />
                <Field label="Total tokens" value={s.total_tokens ?? '—'} />
                <Field
                  label="Digest source"
                  value={
                    <>
                      <span className="mono">{data.source ?? s.source}</span>{' '}
                      <span className="faint">
                        {(data.source ?? s.source) === 'pending'
                          ? '(deterministic extraction; not yet enriched)'
                          : (data.source ?? s.source) === 'claude_summary'
                            ? "(from the agent's own summary)"
                            : '(enriched from the transcript)'}
                      </span>
                    </>
                  }
                />
                <Field label="Released" value={s.released ? 'yes' : 'no'} />
              </tbody>
            </table>

            <p className="faint">
              Anything missing here was missing from the release. Nothing on this page has been
              rewritten by Manthana.
            </p>
          </>
        )
      }}
    </Wiki>
  )
}
