'use client'

/**
 * "You are already in a team."
 *
 * Someone followed an invite into a different org than the one their account
 * belongs to. One account belongs to one org, so the invite cannot be honoured —
 * but silently landing them back in their own org looks exactly like a broken
 * link, which is why this page exists rather than a redirect.
 *
 * Both org names are resolved server-side from the session and the invite, never
 * passed through the URL, so the page cannot be crafted to claim someone belongs
 * somewhere they do not. The invite itself is left untouched and still works.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import useSWR from 'swr'
import { SignupShell } from '@/components/signup/Shell'
import { Loading } from '@/components/Loader'
import { ApiError, signupFetcher, qs } from '@/lib/api'

type Conflict = {
  your_org_name: string
  invited_org_name: string
  continue_to: string
}

function ConflictBody() {
  const code = useSearchParams().get('code') ?? ''
  const { data, error, isLoading } = useSWR<Conflict, ApiError>(
    code ? `/conflict${qs({ code })}` : null,
    signupFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (error) {
    return (
      <div className="ambox ambox-serious">
        <b>We could not read that invitation.</b>
        <p>It may have expired. Ask whoever sent it for a fresh one.</p>
      </div>
    )
  }
  if (isLoading || !data) return <Loading />

  return (
    <>
      <h2 className="first">You&rsquo;re already in a team</h2>
      <p>
        Your account belongs to <b>{data.your_org_name}</b>, so it can&rsquo;t also join{' '}
        <b>{data.invited_org_name}</b> — one account belongs to one organization.
      </p>
      <p>
        To join with a separate identity, sign in with a different Google account. The
        invitation is untouched and still works.
      </p>
      <a className="button button-progressive" href={data.continue_to}>
        Continue to {data.your_org_name}
      </a>
    </>
  )
}

export default function ConflictPage() {
  return (
    <SignupShell>
      <Suspense fallback={<Loading />}>
        <ConflictBody />
      </Suspense>
    </SignupShell>
  )
}
