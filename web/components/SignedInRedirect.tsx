'use client'

/**
 * Sends an already-signed-in reader from the marketing page to their wiki.
 *
 * Deliberately a small client island rather than a check that gates the whole
 * page: the marketing HTML is static and must paint immediately for a stranger,
 * who is the only person the page is for. Making everyone wait on a `/me` round
 * trip to find out they are NOT signed in would slow down the exact case the
 * page exists to serve.
 *
 * So the pitch renders, and the handful of visitors who already have a session
 * are moved on a moment later. `replace`, not `push`, so Back does not bounce
 * them straight back here.
 */

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import useSWR from 'swr'
import { fetcher } from '@/lib/api'
import type { Me } from '@/lib/types'

export function SignedInRedirect() {
  const router = useRouter()
  // A 401 is the expected answer here, so failures stay silent: no retry, no
  // error surface. Not being signed in is the normal state on this page.
  const { data } = useSWR<Me>('/me', fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  useEffect(() => {
    if (data) router.replace('/home')
  }, [data, router])

  return null
}
