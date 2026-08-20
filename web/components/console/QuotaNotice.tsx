'use client'

/**
 * What a spent budget looks like.
 *
 * The server sends cap and spend as numbers, so this renders a state rather than
 * echoing a sentence. That matters because a hit cap has no other symptom — the
 * product just quietly stops producing — and "you have used $103.12 of $100.00"
 * is a different, more actionable thing to read than "quota exceeded".
 */

import { usd } from '@/components/console/charts'
import { ApiError } from '@/lib/api'

export type QuotaDetail = {
  error: 'quota_exceeded'
  org_id: string
  cap_usd: number
  spent_usd: number
  message: string
}

/** The structured quota body, or null if this is some other failure. */
export function quotaFrom(error: unknown): QuotaDetail | null {
  if (!(error instanceof ApiError) || error.status !== 429) return null
  const d = error.detail as QuotaDetail | undefined
  return d?.error === 'quota_exceeded' ? d : null
}

export function QuotaNotice({ quota }: { quota: QuotaDetail }) {
  return (
    <div className="ambox ambox-serious">
      <b>This org has used its monthly AI budget.</b>
      <p>
        <span className="tabular">
          {usd(quota.spent_usd)} of {usd(quota.cap_usd)}
        </span>{' '}
        spent. It resets at the start of next month; until then, ask your Manthana operator to
        raise it. Nothing is lost — new sessions still arrive, they just stay unsummarised.
      </p>
    </div>
  )
}
