'use client'

/**
 * Create a new organization, or join the one that already owns your work domain.
 *
 * The join option only appears when the server says a work domain already matches.
 * A personal address never matches — a gmail.com claim would put every personal
 * signup into one enormous shared org — so those people always create their own
 * and join others through an invite link instead.
 *
 * Joining lands you as an ENGINEER, and the page says so rather than letting it
 * be a surprise. Controlling an email domain is not authorisation to read the
 * company's costs and every engineer's activity.
 */

import { useState } from 'react'
import useSWR from 'swr'
import { SignupShell } from '@/components/signup/Shell'
import { Notice } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, signupFetcher, signupPost } from '@/lib/api'

type Pending = {
  email: string
  display_name: string | null
  suggested_org_name: string
  join_org_id: string | null
  join_org_name: string | null
}

export default function ChoosePage() {
  const { data, error, isLoading } = useSWR<Pending, ApiError>('/pending', signupFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  const [orgName, setOrgName] = useState<string | null>(null)
  const [busy, setBusy] = useState<'create' | 'join' | null>(null)
  const [failure, setFailure] = useState('')

  // The pending cookie is short-lived by design; expiring mid-decision is
  // ordinary, so it sends them back to start rather than showing a failure.
  if (error?.status === 401) {
    if (typeof window !== 'undefined') window.location.href = '/signup?error=expired'
    return null
  }

  async function submit(kind: 'create' | 'join') {
    setBusy(kind)
    setFailure('')
    try {
      const body =
        kind === 'create'
          ? { org_name: orgName ?? data?.suggested_org_name ?? '' }
          : { org_id: data?.join_org_id ?? '' }
      const result = await signupPost<{ next: string }>(`/${kind}`, body)
      // A full navigation, not router.push: the session cookie was just set and
      // every cached SWR key was fetched as a signed-out user.
      window.location.href = result.next
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : 'Something went wrong.')
      setBusy(null)
    }
  }

  if (isLoading || !data) {
    return (
      <SignupShell wide>
        <Skeleton className="h-5 w-56" />
        <Skeleton className="mt-6 h-40 w-full" />
      </SignupShell>
    )
  }

  return (
    <SignupShell wide>
      <p className="text-sm text-muted-foreground">
        Signed in as <span className="font-medium text-foreground">{data.display_name ?? data.email}</span>
      </p>

      {failure && (
        <div className="mt-4">
          <Notice tone="disputed">{failure}</Notice>
        </div>
      )}

      {data.join_org_id && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="text-base">Join {data.join_org_name}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Someone from your email domain is already using Manthana. Join them and
              you&rsquo;ll get the team wiki — a founder can give you the full console
              afterwards.
            </p>
            <Button
              className="mt-4"
              disabled={busy !== null}
              onClick={() => submit('join')}
            >
              {busy === 'join' ? 'Joining…' : `Join ${data.join_org_name}`}
            </Button>
          </CardContent>
        </Card>
      )}

      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-base">Create a new organization</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void submit('create')
            }}
          >
            <div className="grid gap-2">
              <Label htmlFor="org">Organization name</Label>
              <Input
                id="org"
                value={orgName ?? data.suggested_org_name}
                onChange={(e) => setOrgName(e.target.value)}
                required
              />
            </div>
            <Button type="submit" className="mt-4" disabled={busy !== null}>
              {busy === 'create' ? 'Creating…' : 'Create organization'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </SignupShell>
  )
}
