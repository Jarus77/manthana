'use client'

/**
 * The one sign-in page.
 *
 * There used to be two — a server-rendered `/ui/login` with the Google button and
 * this one with the token box — set against the same cookie and drifting apart.
 * Now: Google first, because that is the path almost everyone should take, and the
 * token box below it for the operator's admin token and the engineer wiki logins
 * minted before self-serve existed.
 *
 * WHERE YOU LAND depends on who you are. The login response carries the role, so
 * founders and the operator go to the console and engineers go to the wiki —
 * rather than everyone landing somewhere generic and being bounced.
 *
 * The Google button is drawn only when the deployment actually has an OAuth
 * client. A self-hosted server has none, and a button that 404s is worse than no
 * button.
 */

import { useState } from 'react'
import useSWR from 'swr'
import { GoogleButton, Muted, SignupShell } from '@/components/signup/Shell'
import { Notice } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, post } from '@/lib/api'

type PublicConfig = { signup_enabled: boolean }

const publicConfigFetcher = async (path: string): Promise<PublicConfig> => {
  const resp = await fetch(path, { headers: { accept: 'application/json' } })
  if (!resp.ok) throw new ApiError(resp.status, resp.statusText)
  return resp.json()
}

/** Engineers hold a wiki login; everyone else manages the org. */
function landingFor(role: string): string {
  return role === 'engineer' ? '/home' : '/console'
}

export default function LoginPage() {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // Failure here is not worth surfacing: it only decides whether one button is
  // drawn, and the token box works regardless.
  const { data: config } = useSWR<PublicConfig>('/ui/api/config', publicConfigFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { role } = await post<{ role: string }>('/login', { token })
      // A full navigation, not router.push: the session cookie was just set and
      // every cached SWR key was fetched as a signed-out user.
      window.location.href = landingFor(role)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in')
      setBusy(false)
    }
  }

  return (
    <SignupShell>
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>

      {config?.signup_enabled && (
        <>
          <div className="mt-6">
            <GoogleButton />
          </div>
          <Muted>We only ever read your name and email address.</Muted>
          <div className="my-8 flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">or use a token</span>
            <div className="h-px flex-1 bg-border" />
          </div>
        </>
      )}

      {error && (
        <div className="mb-4">
          <Notice tone="disputed">{error}</Notice>
        </div>
      )}

      <form onSubmit={submit} className={config?.signup_enabled ? '' : 'mt-6'}>
        <div className="grid gap-2">
          <Label htmlFor="token">Your Manthana token</Label>
          <Input
            id="token"
            type="password"
            autoFocus={!config?.signup_enabled}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="eyJhbGciOi…"
          />
        </div>
        <Button type="submit" className="mt-4 w-full" disabled={busy || !token}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>

      <p className="mt-6 text-xs text-muted-foreground">
        Founders and the operator land on the console; engineers land on the team wiki.
      </p>
    </SignupShell>
  )
}
