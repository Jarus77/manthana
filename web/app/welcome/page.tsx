'use client'

/**
 * What a founder sees the moment their org exists.
 *
 * This page IS the onboarding email that used to be hand-written and sent by the
 * operator, which is the whole point of the self-serve work: the two lines an
 * engineer runs, a browser link for people who would rather not install anything,
 * and — only when asked for — the long-lived token for MCP and scripts.
 *
 * Ordered by what most founders need first. The API token is last and behind a
 * click because most people never need it, and a credential shown unprompted is a
 * credential someone pastes somewhere.
 */

import Link from 'next/link'
import { useState } from 'react'
import useSWR from 'swr'
import { CopyBlock } from '@/components/signup/CopyBlock'
import { SignupShell } from '@/components/signup/Shell'
import { Notice, SectionHeading } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, signupFetcher, signupPost } from '@/lib/api'

type Welcome = {
  org_id: string
  org_name: string
  install_line: string
  setup_line: string
  join_url: string
  invite_days: number
  session_days: number
}

function ApiTokenSection({ sessionDays }: { sessionDays: number }) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function mint() {
    setBusy(true)
    setFailed(false)
    try {
      const { token: t } = await signupPost<{ token: string }>('/api-token')
      setToken(t)
    } catch {
      setFailed(true)
    }
    setBusy(false)
  }

  return (
    <>
      <p className="text-sm text-muted-foreground">
        Your browser session lasts {sessionDays} days. For the MCP gateway or the API you need
        a long-lived token — generate one when you actually need it.
      </p>
      {!token && (
        <Button className="mt-4" variant="outline" onClick={mint} disabled={busy}>
          {busy ? 'Generating…' : 'Generate API token'}
        </Button>
      )}
      {failed && (
        <div className="mt-4">
          <Notice tone="disputed">Could not generate a token. Please try again.</Notice>
        </div>
      )}
      {token && (
        <div className="mt-4 space-y-3">
          <CopyBlock label="API token" value={token} />
          <Notice tone="unreviewed" title="Shown once.">
            It is not stored anywhere. If you lose it, generate another — both keep working
            until revoked.
          </Notice>
        </div>
      )}
    </>
  )
}

export default function WelcomePage() {
  const { data, error, isLoading } = useSWR<Welcome, ApiError>('/welcome', signupFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  if (error) {
    // 401 means no session, 403 means an engineer landed here — neither is an
    // error worth explaining, so send them where they belong.
    if (typeof window !== 'undefined') {
      window.location.href = error.status === 403 ? '/home' : '/ui/login'
    }
    return null
  }
  if (isLoading || !data) {
    return (
      <SignupShell wide>
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-6 h-32 w-full" />
      </SignupShell>
    )
  }

  return (
    <SignupShell wide>
      <h1 className="text-2xl font-semibold tracking-tight">{data.org_name} is ready</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Nothing else to configure. Send your engineers the two lines below and their work
        starts arriving.
      </p>

      <SectionHeading>1 · Send this to your engineers</SectionHeading>
      <p className="mb-4 text-sm text-muted-foreground">
        Two lines on their laptop — no account to create, nothing to set up. This invite is
        good for {data.invite_days} days.
      </p>
      <CopyBlock value={`${data.install_line}\n${data.setup_line}`} />

      <SectionHeading>2 · Or invite them in a browser</SectionHeading>
      <p className="mb-4 text-sm text-muted-foreground">
        The same invite, for anyone who wants to read and correct the team&rsquo;s shared
        context without installing anything.
      </p>
      <CopyBlock value={data.join_url} />

      <SectionHeading>3 · Connect Claude Code or scripts</SectionHeading>
      <ApiTokenSection sessionDays={data.session_days} />

      <div className="mt-12 flex flex-wrap gap-3 border-t pt-6">
        <Button asChild>
          <a href="/ui">Go to the console</a>
        </Button>
        <Button asChild variant="outline">
          <Link href="/home">Open the team wiki</Link>
        </Button>
      </div>
    </SignupShell>
  )
}
