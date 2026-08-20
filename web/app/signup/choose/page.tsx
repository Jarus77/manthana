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
import { Loading } from '@/components/Loader'
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
        <Loading />
      </SignupShell>
    )
  }

  return (
    <SignupShell wide>
      <p className="tagline">
        Signed in as <b>{data.display_name ?? data.email}</b>
      </p>

      {failure && <div className="error-box">{failure}</div>}

      {data.join_org_id && (
        <>
          <h2 className="first">Join {data.join_org_name}</h2>
          <p>
            Someone from your email domain is already using Manthana. Join them and
            you&rsquo;ll get the team wiki — a founder can give you the full console
            afterwards.
          </p>
          <button
            type="button"
            className="button button-progressive"
            disabled={busy !== null}
            onClick={() => submit('join')}
          >
            {busy === 'join' ? 'Joining…' : `Join ${data.join_org_name}`}
          </button>
        </>
      )}

      <h2 className={data.join_org_id ? undefined : 'first'}>Create a new organization</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit('create')
        }}
      >
        <div className="field">
          <label htmlFor="org">Organization name</label>
          <input
            id="org"
            type="text"
            value={orgName ?? data.suggested_org_name}
            onChange={(e) => setOrgName(e.target.value)}
            required
          />
        </div>
        <button type="submit" className="button button-progressive" disabled={busy !== null}>
          {busy === 'create' ? 'Creating…' : 'Create organization'}
        </button>
      </form>
    </SignupShell>
  )
}
