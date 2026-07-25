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
import { Notice } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
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
      <Notice tone="disputed" title="We could not read that invitation.">
        It may have expired. Ask whoever sent it for a fresh one.
      </Notice>
    )
  }
  if (isLoading || !data) return <Skeleton className="h-32 w-full" />

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">You&rsquo;re already in a team</h1>
      <p className="mt-3 text-sm text-muted-foreground">
        Your account belongs to{' '}
        <span className="font-medium text-foreground">{data.your_org_name}</span>, so it
        can&rsquo;t also join{' '}
        <span className="font-medium text-foreground">{data.invited_org_name}</span> — one
        account belongs to one organization.
      </p>
      <p className="mt-3 text-sm text-muted-foreground">
        To join with a separate identity, sign in with a different Google account. The
        invitation is untouched and still works.
      </p>
      <Button asChild className="mt-6">
        <a href={data.continue_to}>Continue to {data.your_org_name}</a>
      </Button>
    </>
  )
}

export default function ConflictPage() {
  return (
    <SignupShell>
      <Suspense fallback={<Skeleton className="h-32 w-full" />}>
        <ConflictBody />
      </Suspense>
    </SignupShell>
  )
}
