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
import { EmptyState, Mono, SectionHeading, StatusText, Tag } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
        {usd(b.spent_usd)} <span className="text-muted-foreground">/ no cap</span>
      </span>
    )
  }
  return (
    <span className="tabular">
      {usd(b.spent_usd)} <span className="text-muted-foreground">/ {usd(b.cap_usd)}</span>
      {b.blocked && (
        <span className="ml-2">
          <StatusText tone="danger">cap reached</StatusText>
        </span>
      )}
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

  if (isLoading || !orgs) return <Skeleton className="h-64 w-full" />

  return (
    <>
      <PageTitle aside={me?.role === 'admin' ? 'operator — every org' : undefined}>
        {me?.can_switch_org ? 'Organizations' : (orgs.orgs[0]?.name ?? 'Console')}
      </PageTitle>

      {orgs.orgs.length === 0 ? (
        <EmptyState>No organizations yet.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Org</TableHead>
                <TableHead className="text-right">Teams</TableHead>
                <TableHead className="text-right">Sessions</TableHead>
                <TableHead className="text-right">Pending skills</TableHead>
                <TableHead>AI spend this month</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orgs.orgs.map((o) => (
                <TableRow key={o.id}>
                  <TableCell>
                    <Link
                      className="font-medium text-primary underline-offset-4 hover:underline"
                      href={`/console/sessions?org=${encodeURIComponent(o.id)}`}
                    >
                      {o.name}
                    </Link>
                    <div className="text-xs text-muted-foreground">
                      <Mono>{o.id}</Mono>
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular">{o.teams}</TableCell>
                  <TableCell className="text-right tabular">{o.compactions}</TableCell>
                  <TableCell className="text-right tabular">{o.pending_skills}</TableCell>
                  <TableCell>
                    <BudgetCell b={o.budget} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* ── members ─────────────────────────────────────────────────── */}
      <SectionHeading>Members</SectionHeading>
      <p className="mb-3 text-sm text-muted-foreground">
        People who can sign in. Everyone who joins by email domain or an invite link lands as
        an engineer — they read and correct the wiki, but the oversight pages are not theirs.
      </p>
      {!members ? (
        <Skeleton className="h-20 w-full" />
      ) : members.members.length === 0 ? (
        <EmptyState>Nobody has signed in with Google yet.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.members.map((m) => (
                <TableRow key={m.id}>
                  <TableCell>{m.display_name ?? m.email.split('@')[0]}</TableCell>
                  <TableCell className="text-muted-foreground">{m.email}</TableCell>
                  <TableCell>
                    {m.role === 'founder' ? (
                      <Tag tone="primary">founder</Tag>
                    ) : (
                      <Tag>engineer</Tag>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* ── audit ───────────────────────────────────────────────────── */}
      <SectionHeading>Recent questions</SectionHeading>
      <p className="mb-3 text-sm text-muted-foreground">
        Every question asked of this org, answered or withheld. Withheld means the evidence
        did not clear the k-anonymity floor — a team pattern must never be one person&rsquo;s
        activity in disguise.
      </p>
      {!audit ? (
        <Skeleton className="h-20 w-full" />
      ) : audit.entries.length === 0 ? (
        <EmptyState>No questions asked yet.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Question</TableHead>
                <TableHead>Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.entries.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {e.created_at.slice(0, 16).replace('T', ' ')}
                  </TableCell>
                  <TableCell className="max-w-md truncate">{e.query}</TableCell>
                  <TableCell>
                    {e.insufficient ? (
                      <StatusText tone="warning">withheld</StatusText>
                    ) : (
                      <span className="tabular">{e.citation_count} citations</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}
