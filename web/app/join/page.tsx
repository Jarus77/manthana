'use client'

/**
 * The browser twin of `manthana setup mia_…`.
 *
 * The link a founder shares with someone whose email domain cannot identify their
 * org — anyone on a personal address. The code is only checked for shape here; it
 * is consumed atomically on the other side of Google, once there is a verified
 * identity to attach it to.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import useSWR from 'swr'
import { GoogleButton, Muted, SignupShell } from '@/components/signup/Shell'
import { Loading } from '@/components/Loader'
import { ApiError, signupFetcher, qs } from '@/lib/api'

function JoinBody() {
  const code = useSearchParams().get('code') ?? ''
  const { data, error, isLoading } = useSWR<{ org_name: string }, ApiError>(
    code ? `/invite${qs({ code })}` : null,
    signupFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (!code || error) {
    return (
      <>
        <div className="ambox ambox-serious">
          <b>That invitation link is not valid.</b>
          <p>
            It may have expired, been used up, or been copied incompletely. Ask whoever sent
            it for a fresh one.
          </p>
        </div>
        <p>
          Setting up a team of your own instead? <a href="/signup">Create an organization</a>.
        </p>
      </>
    )
  }

  if (isLoading || !data) return <Loading />

  return (
    <>
      <h2 className="first">Join {data.org_name} on Manthana</h2>
      <p>
        You&rsquo;ll get their team wiki — what everyone is working on, and what the team has
        learned. Sign in to accept.
      </p>
      <GoogleButton invite={code} />
      <Muted>We only ever read your name and email address.</Muted>
    </>
  )
}

export default function JoinPage() {
  return (
    <SignupShell>
      <Suspense fallback={<Loading />}>
        <JoinBody />
      </Suspense>
    </SignupShell>
  )
}
