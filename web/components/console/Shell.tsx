'use client'

/**
 * Console chrome, on the encyclopedia stylesheet.
 *
 * The console keeps its own nav rather than borrowing the wiki rail, because it
 * is a different job — the wiki is for reading, this is for oversight — but it is
 * now the same SURFACE: same tokens, same type, same links. A founder moving
 * between a project article and the spend page should not feel they changed
 * product.
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
import { Loading } from '@/components/Loader'
import { ApiError, consoleFetcher } from '@/lib/api'

export type Me = {
  role: string
  org_id: string | null
  can_switch_org: boolean
  orgs: Array<{ id: string; name: string }>
}

const NAV = [
  { href: '/console', label: 'Overview' },
  { href: '/console/ask', label: 'Ask' },
  { href: '/console/digest', label: 'Digest' },
  { href: '/console/sessions', label: 'Sessions' },
  { href: '/console/topics', label: 'Topics' },
  { href: '/console/cost', label: 'Cost' },
  { href: '/console/team', label: 'Team' },
  { href: '/console/mining', label: 'Mining' },
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

  const link = (href: string) => href + (org ? `?org=${org}` : '')

  return (
    <div className="signup-shell">
      <header className="console-header">
        <Link className="brand" href="/console">
          Manthana
        </Link>
        <nav className="console-nav">
          {NAV.map((item) => {
            const active =
              item.href === '/console' ? pathname === '/console' : pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                className="nav-link"
                href={link(item.href)}
                aria-current={active ? 'page' : undefined}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>
        <div className="console-actions">
          {me && <OrgSwitcher me={me} />}
          <Link href="/home">Wiki</Link>
        </div>
      </header>
      <main className="console-main">{isLoading ? <Loading /> : children}</main>
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
    <>
      <h1 className="firstHeading">{children}</h1>
      {aside && <p className="tagline">{aside}</p>}
    </>
  )
}
