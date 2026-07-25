'use client'

/**
 * Chrome for the onboarding pages.
 *
 * Deliberately not the wiki rail and not the marketing header: someone in the
 * middle of signing up has exactly one thing to do, and every extra link is an
 * invitation to leave. Just the mark and the theme toggle.
 */

import Link from 'next/link'
import { Logo } from '@/components/Logo'
import { ThemeToggle } from '@/components/ThemeToggle'

export function SignupShell({
  children,
  wide = false,
}: {
  children: React.ReactNode
  wide?: boolean
}) {
  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <header className="border-b">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <Link href="/">
            <Logo />
          </Link>
          <ThemeToggle />
        </div>
      </header>
      <main className={`mx-auto px-6 py-12 ${wide ? 'max-w-3xl' : 'max-w-md'}`}>
        {children}
      </main>
    </div>
  )
}

/** Google's own wordmark colours, so the button is recognisable at a glance. */
export function GoogleButton({ invite }: { invite?: string }) {
  const href = invite ? `/ui/auth/google?invite=${encodeURIComponent(invite)}` : '/ui/auth/google'
  return (
    <a
      href={href}
      className="inline-flex h-10 w-full items-center justify-center gap-3 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
    >
      <svg viewBox="0 0 18 18" className="size-4" aria-hidden>
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
  return <p className="mt-4 text-center text-xs text-muted-foreground">{children}</p>
}
