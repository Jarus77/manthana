'use client'

/**
 * The weekly digest — what actually happened, written from the work rather than
 * from standup.
 *
 * `omitted` is rendered rather than dropped. A digest that silently leaves a
 * section out reads as "nothing happened there", which is a different and usually
 * false claim: the real reason is almost always the k-anonymity floor.
 */

import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { QuotaNotice, quotaFrom } from '@/components/console/QuotaNotice'
import { EmptyState, Mono, Notice } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, consoleFetcher, qs } from '@/lib/api'

type Digest = {
  org_id: string
  since: string
  until: string
  sections: Array<{ title: string; narrative: string; citations: string[] }>
  omitted: string[]
}

export default function DigestPage() {
  const org = useOrgId()
  const { data, error, isLoading } = useSWR<Digest, ApiError>(
    org ? `/digest${qs({ org_id: org })}` : null,
    consoleFetcher,
    // Not revalidated on focus, and never retried: this GET spends money, so a
    // background refetch is a background bill.
    { revalidateOnFocus: false, shouldRetryOnError: false, revalidateOnReconnect: false },
  )

  const quota = quotaFrom(error)

  return (
    <>
      <PageTitle aside={data ? `${data.since} → ${data.until}` : undefined}>
        Weekly digest
      </PageTitle>

      {quota ? (
        <QuotaNotice quota={quota} />
      ) : error ? (
        <Notice tone="disputed">{error.message}</Notice>
      ) : isLoading || !data ? (
        <>
          <Skeleton className="h-6 w-64" />
          <Skeleton className="mt-4 h-32 w-full" />
        </>
      ) : (
        <>
          <p className="mb-6 text-sm text-muted-foreground">
            Aggregate, with the k-anonymity floor enforced — every section here is drawn from
            enough people that it describes the team rather than an individual.
          </p>

          {data.sections.length === 0 ? (
            <EmptyState>
              Nothing cleared the floor for this window. Either the week was quiet, or the
              work was concentrated in too few hands to report as a team pattern.
            </EmptyState>
          ) : (
            data.sections.map((s) => (
              <section key={s.title} className="mb-8">
                <h2 className="mb-2 text-base font-semibold">{s.title}</h2>
                <p className="whitespace-pre-wrap text-sm">{s.narrative}</p>
                {s.citations.length > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Sources:{' '}
                    {s.citations.map((c, i) => (
                      <span key={c}>
                        {i > 0 && ', '}
                        <Mono>{c}</Mono>
                      </span>
                    ))}
                  </p>
                )}
              </section>
            ))
          )}

          {data.omitted.length > 0 && (
            <Notice tone="unreviewed" title="Some sections were left out.">
              {data.omitted.join(', ')} — either no data for the window, or too few
              contributors to report without naming someone.
            </Notice>
          )}
        </>
      )}
    </>
  )
}
