'use client'

/**
 * Console overview.
 *
 * Budget first, deliberately. A hit AI cap has no other symptom — enrichment stops
 * and the wiki fills with unsummarised sessions that read as a product bug — so it
 * is on the first screen rather than behind a click.
 */

import Link from 'next/link'
import useSWR from 'swr'
import { PageTitle, useMe, useOrgId } from '@/components/console/Shell'
import { usd } from '@/components/console/charts'
import { Loading } from '@/components/Loader'
import { ApiError, consoleFetcher, qs } from '@/lib/api'

type Budget = {
  month: string
  spent_usd: number
  cap_usd: number
  cap_is_override: boolean
  blocked: boolean
}
type Org = {
  id: string
  name: string
  teams: number
  compactions: number
  pending_skills: number
  budget: Budget
}
type AuditEntry = {
  id: string
  query: string
  insufficient: boolean
  citation_count: number
  created_at: string
}
type Member = { id: string; email: string; display_name: string | null; role: string }

function BudgetCell({ b }: { b: Budget }) {
  if (b.cap_usd <= 0) {
    return (
      <span className="tabular">
        {usd(b.spent_usd)} <span className="faint">/ no cap</span>
      </span>
    )
  }
  return (
    <span className="tabular">
      {usd(b.spent_usd)} <span className="faint">/ {usd(b.cap_usd)}</span>
      {b.blocked && <span className="status-disputed"> cap reached</span>}
    </span>
  )
}

export default function ConsoleOverview() {
  const org = useOrgId()
  const { data: me } = useMe()
  const { data: orgs, isLoading } = useSWR<{ orgs: Org[] }, ApiError>(
    '/orgs',
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  const { data: audit } = useSWR<{ entries: AuditEntry[] }, ApiError>(
    org ? `/audit${qs({ org_id: org, limit: 10 })}` : null,
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  const { data: members } = useSWR<{ members: Member[] }, ApiError>(
    org ? `/members${qs({ org_id: org })}` : null,
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (isLoading || !orgs) return <Loading />

  return (
    <>
      <PageTitle aside={me?.role === 'admin' ? 'operator — every org' : undefined}>
        {me?.can_switch_org ? 'Organizations' : (orgs.orgs[0]?.name ?? 'Console')}
      </PageTitle>

      {orgs.orgs.length === 0 ? (
        <div className="empty">No organizations yet.</div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>Org</th>
                <th>Teams</th>
                <th>Sessions</th>
                <th>Pending skills</th>
                <th>AI spend this month</th>
              </tr>
            </thead>
            <tbody>
              {orgs.orgs.map((o) => (
                <tr key={o.id}>
                  <td>
                    <Link href={`/console/sessions?org=${encodeURIComponent(o.id)}`}>
                      {o.name}
                    </Link>
                    <div className="mono faint">{o.id}</div>
                  </td>
                  <td className="tabular">{o.teams}</td>
                  <td className="tabular">{o.compactions}</td>
                  <td className="tabular">{o.pending_skills}</td>
                  <td>
                    <BudgetCell b={o.budget} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── members ─────────────────────────────────────────────────── */}
      <h2>Members</h2>
      <p>
        People who can sign in. Everyone who joins by email domain or an invite link lands as
        an engineer — they read and correct the wiki, but the oversight pages are not theirs.
      </p>
      {!members ? (
        <Loading />
      ) : members.members.length === 0 ? (
        <div className="empty">Nobody has signed in with Google yet.</div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
              </tr>
            </thead>
            <tbody>
              {members.members.map((m) => (
                <tr key={m.id}>
                  <td>{m.display_name ?? m.email.split('@')[0]}</td>
                  <td className="subtle">{m.email}</td>
                  <td>{m.role === 'founder' ? <b>founder</b> : 'engineer'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── audit ───────────────────────────────────────────────────── */}
      <h2>Recent questions</h2>
      <p>
        Every question asked of this org, answered or withheld. Withheld means the evidence
        did not clear the k-anonymity floor — a team pattern must never be one person&rsquo;s
        activity in disguise.
      </p>
      {!audit ? (
        <Loading />
      ) : audit.entries.length === 0 ? (
        <div className="empty">No questions asked yet.</div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>When</th>
                <th>Question</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {audit.entries.map((e) => (
                <tr key={e.id}>
                  <td style={{ whiteSpace: 'nowrap' }} className="subtle">
                    {e.created_at.slice(0, 16).replace('T', ' ')}
                  </td>
                  <td>{e.query}</td>
                  <td>
                    {e.insufficient ? (
                      <span className="status-unreviewed">withheld</span>
                    ) : (
                      <span className="tabular">{e.citation_count} citations</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
