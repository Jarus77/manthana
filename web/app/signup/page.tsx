'use client'

/**
 * Get started.
 *
 * Also the place every failed sign-in lands. The server hands back a reason slug
 * rather than an error page — it knows what went wrong, this owns how to say it —
 * and anything unrecognised falls through to a generic line rather than showing
 * the raw slug to a stranger.
 */

import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { GoogleButton, Muted, SignupShell } from '@/components/signup/Shell'

const ERRORS: Record<string, string> = {
  google: 'We could not complete sign-in with Google. Please try again.',
  incomplete: 'That sign-in link was incomplete. Please start again.',
  state: 'This sign-in could not be verified. Please try again.',
  expired: 'This sign-in expired. Please try again.',
  unverified: 'That Google account has no verified email address.',
  invite: 'That invitation has expired or been used up.',
}

function SignupBody() {
  const error = useSearchParams().get('error')
  const message = error ? (ERRORS[error] ?? 'Something went wrong. Please try again.') : null

  return (
    <>
      {message && <div className="error-box">{message}</div>}
      <h2 className="first">Create your organization</h2>
      <p>
        About a minute. You&rsquo;ll get a command to send your engineers — nothing to
        configure.
      </p>
      <GoogleButton />
      <Muted>We only ever read your name and email address.</Muted>
    </>
  )
}

export default function SignupPage() {
  return (
    <SignupShell>
      {/* useSearchParams needs a Suspense boundary to keep this route static. */}
      <Suspense fallback={null}>
        <SignupBody />
      </Suspense>
    </SignupShell>
  )
}
