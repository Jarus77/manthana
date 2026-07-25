'use client'

/**
 * Emergent topics — what the team keeps working on, discovered rather than declared.
 *
 * Two things this page must say out loud, because both change how the numbers
 * should be read:
 *
 *   COVERAGE. Clustering runs over a bounded window. A truncated clustering is a
 *   partial answer, and a partial answer that does not say so is a wrong one.
 *
 *   THE FLOOR. On a de-identified org a topic needs contributions from several
 *   people before it appears at all, so that a "team pattern" can never be one
 *   person's activity in disguise. An empty page usually means the floor, not the
 *   absence of work — and those are very different things to tell a founder.
 */

import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { EmptyState, Notice } from '@/components/manthana'
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

type Topics = {
  org_id: string
  named: boolean
  k_anon_floor: number
  coverage: { matched: number; used: number; truncated: boolean }
  topics: Array<{
    id: string
    label: string
    contributors: number
    sessions: number
    sample_intents: string[]
  }>
}

export default function TopicsPage() {
  const org = useOrgId()
  const { data, isLoading } = useSWR<Topics, ApiError>(
    org ? `/topics${qs({ org_id: org })}` : null,
    consoleFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (isLoading || !data) return <Skeleton className="h-64 w-full" />

  return (
    <>
      <PageTitle
        aside={
          data.named
            ? undefined
            : `de-identified · ≥${data.k_anon_floor} contributors per topic`
        }
      >
        Topics
      </PageTitle>

      {data.coverage.truncated && (
        <div className="mb-5">
          <Notice tone="unreviewed" title="This is a partial view.">
            Clustered over the {data.coverage.used} most recent of {data.coverage.matched}{' '}
            sessions — older work is not represented here.
          </Notice>
        </div>
      )}

      {data.topics.length === 0 ? (
        <EmptyState>
          {data.named
            ? 'No cross-cutting topics yet — they emerge once several sessions share ground.'
            : `No topics clear the floor of ${data.k_anon_floor} contributors yet. Work is
               happening; it just is not yet shared enough between people to show as a team
               pattern.`}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Topic</TableHead>
                <TableHead className="text-right">People</TableHead>
                <TableHead className="text-right">Sessions</TableHead>
                <TableHead>Sample work</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.topics.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="font-medium">{t.label}</TableCell>
                  <TableCell className="text-right tabular">{t.contributors}</TableCell>
                  <TableCell className="text-right tabular">{t.sessions}</TableCell>
                  <TableCell className="max-w-lg text-sm text-muted-foreground">
                    <span className="line-clamp-2">{t.sample_intents.join(' · ')}</span>
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
