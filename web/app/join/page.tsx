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
import { Notice } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
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
        <Notice tone="disputed" title="That invitation link is not valid.">
          It may have expired, been used up, or been copied incompletely. Ask whoever sent it
          for a fresh one.
        </Notice>
        <p className="mt-6 text-sm text-muted-foreground">
          Setting up a team of your own instead?{' '}
          <a className="text-primary underline-offset-4 hover:underline" href="/signup">
            Create an organization
          </a>
          .
        </p>
      </>
    )
  }

  if (isLoading || !data) return <Skeleton className="h-24 w-full" />

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">
        Join {data.org_name} on Manthana
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        You&rsquo;ll get their team wiki — what everyone is working on, and what the team has
        learned. Sign in to accept.
      </p>
      <div className="mt-8">
        <GoogleButton invite={code} />
      </div>
      <Muted>We only ever read your name and email address.</Muted>
    </>
  )
}

export default function JoinPage() {
  return (
    <SignupShell>
      <Suspense fallback={<Skeleton className="h-24 w-full" />}>
        <JoinBody />
      </Suspense>
    </SignupShell>
  )
}
