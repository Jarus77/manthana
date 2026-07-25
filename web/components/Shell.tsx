'use client'

/**
 * Wiki chrome: a quiet left rail and a reading surface.
 *
 * The restraint is doing work. Navigation stays recessive so the article is the
 * loudest thing on screen, which is the opposite of the console, where the chrome
 * has jobs of its own. The rail deliberately does NOT enumerate note kinds — a
 * taxonomy-shaped sidebar made the reader choose a category before they could read
 * anything, which is backwards for an article surface.
 *
 * That principle survived the move onto the design system; what changed is that
 * the styling now comes from the same tokens as everything else, so the wiki
 * follows the theme instead of being permanently light.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import useSWR from 'swr'
import { Logo } from '@/components/Logo'
import { ThemeToggle } from '@/components/ThemeToggle'
import { fetcher, post } from '@/lib/api'
import type { Me } from '@/lib/types'
import { cn } from '@/lib/utils'

const NAV = [
  { href: '/home', label: 'Main page' },
  { href: '/sessions', label: 'Recent sessions' },
  { href: '/people', label: 'People' },
  { href: '/projects', label: 'Projects' },
]

function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname()
  const active = href === '/home' ? pathname === '/home' : pathname.startsWith(href)
  return (
    <Link
      className={cn(
        'block rounded-md px-2 py-1 text-sm transition-colors',
        active
          ? 'bg-muted font-medium text-foreground'
          : 'text-muted-foreground hover:text-foreground',
      )}
      href={href}
      aria-current={active ? 'page' : undefined}
    >
      {label}
    </Link>
  )
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  // Routes that draw their own full-page chrome, and so must not be framed by
  // this rail: /login has no session yet, / is the marketing page, /design is the
  // design system itself, the onboarding routes give someone mid-signup exactly
  // one thing to do, and the console has its own nav. Asking /me on any of them
  // would produce a 401 the page has no use for.
  const bare =
    pathname === '/login' ||
    pathname === '/design' ||
    pathname === '/' ||
    pathname === '/welcome' ||
    pathname === '/join' ||
    pathname.startsWith('/signup') ||
    pathname.startsWith('/console')
  const { data: me } = useSWR<Me>(bare ? null : '/me', fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  if (bare) return <>{children}</>

  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      {/* Mobile: the rail collapses to a header rather than stacking a column of
          links above every article. Outside the flex row on purpose — a full-width
          child inside it would sit beside the content, not above it. */}
      <header className="flex items-center justify-between gap-2 border-b px-4 py-3 md:hidden">
        <Link href="/home">
          <Logo size={18} />
        </Link>
        <div className="flex items-center gap-0.5">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-md px-1.5 py-1 text-xs text-muted-foreground"
            >
              {item.label.replace('Recent ', '')}
            </Link>
          ))}
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto flex max-w-6xl items-start gap-8 px-4 md:px-6">
        <nav className="sticky top-0 hidden w-44 shrink-0 py-6 md:block">
          <Link href="/home" className="mb-1 block">
            <Logo size={20} />
          </Link>
          <div className="mb-6 pl-1 text-xs text-muted-foreground">
            {me?.org_id ?? 'team wiki'}
          </div>

          <div className="mb-6 space-y-0.5">
            <div className="mb-1 border-b pb-1 pl-2 text-xs text-muted-foreground">
              Navigation
            </div>
            {NAV.map((item) => (
              <NavLink key={item.href} {...item} />
            ))}
          </div>

          {me && (
            <div className="space-y-0.5">
              <div className="mb-1 border-b pb-1 pl-2 text-xs text-muted-foreground">
                {me.actor ?? me.role}
              </div>
              {me.role !== 'engineer' && (
                <Link
                  className="block rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  href="/console"
                >
                  Console
                </Link>
              )}
              <button
                type="button"
                className="block w-full rounded-md px-2 py-1 text-left text-sm text-muted-foreground transition-colors hover:text-foreground"
                onClick={async () => {
                  await post('/logout')
                  router.replace('/login')
                }}
              >
                Log out
              </button>
              <div className="pt-2 pl-1">
                <ThemeToggle />
              </div>
            </div>
          )}
        </nav>

        <main className="min-w-0 flex-1 py-6 md:border-l md:pl-8">{children}</main>
      </div>
    </div>
  )
}
