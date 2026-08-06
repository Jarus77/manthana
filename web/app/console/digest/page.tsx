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
import { Loading } from '@/components/Loader'
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
        <div className="error-box">{error.message}</div>
      ) : isLoading || !data ? (
        <Loading />
      ) : (
        <>
          <p className="subtle">
            Aggregate, with the k-anonymity floor enforced — every section here is drawn from
            enough people that it describes the team rather than an individual.
          </p>

          {data.sections.length === 0 ? (
            <div className="empty">
              Nothing cleared the floor for this window. Either the week was quiet, or the
              work was concentrated in too few hands to report as a team pattern.
            </div>
          ) : (
            data.sections.map((s) => (
              <section key={s.title}>
                <h2>{s.title}</h2>
                <p style={{ whiteSpace: 'pre-wrap' }}>{s.narrative}</p>
                {s.citations.length > 0 && (
                  <p className="faint">
                    Sources:{' '}
                    {s.citations.map((c, i) => (
                      <span key={c} className="mono">
                        {i > 0 && ', '}
                        {c}
                      </span>
                    ))}
                  </p>
                )}
              </section>
            ))
          )}

          {data.omitted.length > 0 && (
            <div className="ambox ambox-content">
              <b>Some sections were left out.</b>
              <p>
                {data.omitted.join(', ')} — either no data for the window, or too few
                contributors to report without naming someone.
              </p>
            </div>
          )}
        </>
      )}
    </>
  )
}
