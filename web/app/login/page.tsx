'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { ApiError, post } from '@/lib/api'

export default function LoginPage() {
  const router = useRouter()
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await post('/login', { token })
      // A full navigation, not router.push: the session cookie was just set and
      // every cached SWR key was fetched as a signed-out user.
      window.location.href = '/'
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not sign in')
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1 style={{ marginBottom: 4 }}>Manthana</h1>
        <p className="subtle">Your team&rsquo;s shared context.</p>
        {error && <div className="error-box">{error}</div>}
        <div className="field">
          <label htmlFor="token">Access token</label>
          <input
            id="token"
            type="password"
            autoFocus
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="paste the token you were sent"
          />
        </div>
        <button className="button-progressive" type="submit" disabled={busy || !token.trim()}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="faint">
          Engineers and founders sign in the same way. Ask your founder for a token if you
          don&rsquo;t have one.
        </p>
      </form>
    </div>
  )
}
