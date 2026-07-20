'use client'

/**
 * The persistent wiki chrome: a left rail that never changes, and an ask box
 * that is always in reach.
 *
 * This is the piece the old server-rendered wiki lacked entirely — its only
 * navigation was a four-link bar reading "Home / Console / Log out / API",
 * identical on every page, which meant nothing on screen ever told you where
 * you were or what else existed. A wiki is mostly its navigation.
 *
 * The knowledge section is built from the org's own note kinds (served by
 * /me), not from a list hardcoded here, so a taxonomy change on the server does
 * not need a client release.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import useSWR from 'swr'
import { fetcher, post } from '@/lib/api'
import { KIND_LABEL, type Me } from '@/lib/types'
import { AskBar } from './AskBar'

const NAV = [
  { href: '/', label: 'Home' },
  { href: '/sessions', label: 'Sessions' },
  { href: '/people', label: 'People' },
  { href: '/projects', label: 'Projects' },
]

function NavLink({ href, label, count }: { href: string; label: string; count?: number }) {
  const pathname = usePathname()
  const active = href === '/' ? pathname === '/' : pathname.startsWith(href)
  // A kind with nothing in it is dimmed rather than hidden: the taxonomy is
  // fixed, so its absence is information ("no gotchas recorded yet"), but it
  // should not compete with sections that have something to read.
  const empty = count === 0
  return (
    <Link
      className={`nav-link${empty ? ' nav-link-empty' : ''}`}
      href={href}
      aria-current={active ? 'page' : undefined}
    >
      <span>{label}</span>
      {count !== undefined && count > 0 && <span className="nav-count">{count}</span>}
    </Link>
  )
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  // Skipped on /login: there is no session yet, so asking would only produce a
  // guaranteed 401 in the console before the user has done anything wrong.
  const { data: me } = useSWR<Me>(pathname === '/login' ? null : '/me', fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  // Login owns the whole viewport — no chrome to navigate before you are in.
  if (pathname === '/login') return <>{children}</>

  // Home and the ask page each lead with their own full-width ask box; a second
  // one in the top bar directly above it reads as a duplicated control.
  const heroAsk = pathname === '/' || pathname === '/ask'

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          Manthana
        </Link>
        <span className="brand-sub">{me?.org_id ?? 'team wiki'}</span>

        <div className="nav-group">
          {NAV.map((item) => (
            <NavLink key={item.href} {...item} />
          ))}
        </div>

        <div className="nav-group">
          <div className="nav-label">Knowledge</div>
          <NavLink href="/knowledge/all" label="Everything" count={me?.total_notes} />
          {(me?.kinds ?? []).map((kind) => (
            <NavLink
              key={kind}
              href={`/knowledge/${kind}`}
              label={KIND_LABEL[kind]}
              count={me?.kind_counts?.[kind]}
            />
          ))}
        </div>

        {me && (
          <div className="nav-group">
            <div className="nav-label">Signed in</div>
            <div style={{ padding: '0 10px' }}>
              <div className="muted">{me.actor ?? me.role}</div>
              <button
                style={{ marginTop: 8, width: '100%' }}
                onClick={async () => {
                  await post('/logout')
                  router.replace('/login')
                }}
              >
                Log out
              </button>
            </div>
          </div>
        )}
      </aside>

      <div className="main">
        {!heroAsk && (
          <header className="topbar">
            <AskBar />
          </header>
        )}
        <div className="content">{children}</div>
      </div>
    </div>
  )
}
