'use client'

/**
 * Wiki chrome: a plain-text left rail and a white article surface.
 *
 * MediaWiki's sidebar is a stack of "portals" — small labelled groups of plain
 * blue links, no icons, no pills, no counts styled as badges. That restraint is
 * doing work: the navigation stays out of the way so the article is the loudest
 * thing on screen, which is exactly backwards from a dashboard, where the
 * chrome competes with the content.
 *
 * The knowledge portal is built from the org's own note kinds (served by /me),
 * so a taxonomy change on the server needs no client release.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import useSWR from 'swr'
import { fetcher, post } from '@/lib/api'
import { KIND_LABEL, type Me } from '@/lib/types'

const NAV = [
  { href: '/', label: 'Main page' },
  { href: '/sessions', label: 'Recent sessions' },
  { href: '/people', label: 'People' },
  { href: '/projects', label: 'Projects' },
]

function NavLink({ href, label, count }: { href: string; label: string; count?: number }) {
  const pathname = usePathname()
  const active = href === '/' ? pathname === '/' : pathname.startsWith(href)
  return (
    <Link
      className={`nav-link${count === 0 ? ' nav-link-empty' : ''}`}
      href={href}
      aria-current={active ? 'page' : undefined}
    >
      {label}
      {count !== undefined && count > 0 && <span className="nav-count"> ({count})</span>}
    </Link>
  )
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  // Skipped on /login: no session yet, so asking only produces a guaranteed 401.
  const { data: me } = useSWR<Me>(pathname === '/login' ? null : '/me', fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  if (pathname === '/login') return <>{children}</>

  return (
    <div className="shell">
      <nav className="sidebar">
        <Link className="brand" href="/">
          Manthana
        </Link>
        <span className="brand-sub">{me?.org_id ?? 'team wiki'}</span>

        <div className="nav-portal">
          <div className="nav-label">Navigation</div>
          {NAV.map((item) => (
            <NavLink key={item.href} {...item} />
          ))}
        </div>

        <div className="nav-portal">
          <div className="nav-label">Knowledge</div>
          <NavLink href="/knowledge/all" label="All entries" count={me?.total_notes} />
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
          <div className="nav-portal">
            <div className="nav-label">{me.actor ?? me.role}</div>
            <a
              className="nav-link"
              href="#"
              onClick={async (e) => {
                e.preventDefault()
                await post('/logout')
                router.replace('/login')
              }}
            >
              Log out
            </a>
          </div>
        )}
      </nav>

      <div className="main">
        <div className="content">{children}</div>
      </div>
    </div>
  )
}
