'use client'

/**
 * Chrome for the onboarding pages, on the encyclopedia stylesheet.
 *
 * Deliberately not the wiki rail and not a marketing header: someone in the
 * middle of signing up has exactly one thing to do, and every extra link is an
 * invitation to leave. Just the wordmark over a hairline rule.
 *
 * No theme toggle. The encyclopedia sheet is light only — a white page is part
 * of what makes the product read as a reference work rather than a dashboard —
 * so there is nothing for a toggle to switch between.
 */

import Link from 'next/link'

export function SignupShell({
  children,
  wide = false,
}: {
  children: React.ReactNode
  wide?: boolean
}) {
  return (
    <div className="signup-shell">
      <header className="signup-header">
        <Link className="brand" href="/">
          Manthana
        </Link>
      </header>
      {/* Narrow pages get the bordered card the old sign-in page used; the wide
          ones (choosing an org, the welcome hand-off) carry too much for a
          380px column and read as a page instead. */}
      <div className={wide ? 'signup-wide' : 'login-wrap'}>
        {wide ? children : <div className="login-card">{children}</div>}
      </div>
    </div>
  )
}

/**
 * Google's button, as a progressive action.
 *
 * Vector has exactly one emphasised button style and this is it — the same
 * treatment "Create organization" and "Sign in" get, because they are the same
 * kind of act. The mark keeps its own geometry so the button is still
 * recognisable at a glance.
 */
export function GoogleButton({ invite }: { invite?: string }) {
  const href = invite ? `/ui/auth/google?invite=${encodeURIComponent(invite)}` : '/ui/auth/google'
  return (
    <a href={href} className="button button-progressive button-wide">
      <svg viewBox="0 0 18 18" width="14" height="14" aria-hidden>
        <path
          fill="currentColor"
          d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z"
        />
        <path
          fill="currentColor"
          d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z"
          opacity=".85"
        />
        <path
          fill="currentColor"
          d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z"
          opacity=".7"
        />
        <path
          fill="currentColor"
          d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z"
          opacity=".9"
        />
      </svg>
      Sign in with Google
    </a>
  )
}

export function Muted({ children }: { children: React.ReactNode }) {
  return <p className="signup-muted">{children}</p>
}
