'use client'

/**
 * Console chrome.
 *
 * Its own nav, distinct from the wiki rail and the marketing header, because the
 * console is a different job: the wiki is for reading, this is for oversight.
 *
 * THE ORG LIVES IN THE URL. An admin can see every tenant, and which one they are
 * looking at is part of the address rather than component state — so a view can be
 * linked, bookmarked, and reached with the back button. A founder has exactly one
 * org and never sees the switcher; the server ignores their `?org=` anyway
 * (`scope_org`), so the URL is a convenience for admins and cannot become a
 * privilege for anyone else.
 */

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import useSWR from 'swr'
import { Logo } from '@/components/Logo'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError, consoleFetcher } from '@/lib/api'
import { cn } from '@/lib/utils'

export type Me = {
  role: string
  org_id: string | null
  can_switch_org: boolean
  orgs: Array<{ id: string; name: string }>
}

const NAV = [
  { href: '/console', label: 'Overview' },
  { href: '/console/sessions', label: 'Sessions' },
  { href: '/console/topics', label: 'Topics' },
  { href: '/console/cost', label: 'Cost' },
]

export function useMe() {
  return useSWR<Me, ApiError>('/me', consoleFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
}

/**
 * The org the current view is about.
 *
 * `?org=` when an admin has chosen one, else the caller's own. Returns '' while
 * `/me` is still loading, which callers use to hold their fetch — asking for an
 * org-scoped endpoint with no org would 400.
 */
export function useOrgId(): string {
  const params = useSearchParams()
  const { data: me } = useMe()
  return params.get('org') || me?.org_id || me?.orgs[0]?.id || ''
}

function OrgSwitcher({ me }: { me: Me }) {
  const router = useRouter()
  const pathname = usePathname()
  const current = useOrgId()
  if (!me.can_switch_org) return null

  return (
    <select
      aria-label="Organization"
      className="h-8 rounded-md border bg-background px-2 text-sm"
      value={current}
      onChange={(e) => router.push(`${pathname}?org=${encodeURIComponent(e.target.value)}`)}
    >
      {me.orgs.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  )
}

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const org = useOrgId()
  const { data: me, error, isLoading } = useMe()

  if (error) {
    // 401 means no session, 403 means an engineer wandered in. Neither is an error
    // worth a page — send them where they belong.
    if (typeof window !== 'undefined') {
      window.location.href = error.status === 403 ? '/home' : '/login'
    }
    return null
  }

  const link = (href: string) => (href === '/console' ? href : href) + (org ? `?org=${org}` : '')

  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-3">
          <Link href="/console">
            <Logo size={20} />
          </Link>
          <nav className="flex items-center gap-1">
            {NAV.map((item) => {
              const active =
                item.href === '/console' ? pathname === '/console' : pathname.startsWith(item.href)
              return (
                <Link
                  key={item.href}
                  href={link(item.href)}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'rounded-md px-2.5 py-1.5 text-sm transition-colors',
                    active
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {item.label}
                </Link>
              )
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {me && <OrgSwitcher me={me} />}
            <Link
              href="/home"
              className="rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              Wiki
            </Link>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        {isLoading ? <Skeleton className="h-40 w-full" /> : children}
      </main>
    </div>
  )
}

/** Page heading plus optional aside, used by every console page. */
export function PageTitle({
  children,
  aside,
}: {
  children: React.ReactNode
  aside?: React.ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
      <h1 className="text-xl font-semibold tracking-tight">{children}</h1>
      {aside && <div className="text-sm text-muted-foreground">{aside}</div>}
    </div>
  )
}
