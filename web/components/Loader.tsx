'use client'

/**
 * Data-fetching shell shared by every page.
 *
 * The one behaviour worth naming: a 401 is not an error state. The cookie
 * expiring mid-session is ordinary, so it redirects to /login rather than
 * showing a failure the reader can do nothing about.
 */

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import useSWR from 'swr'
import { ApiError, fetcher } from '@/lib/api'

export function useWiki<T>(path: string | null) {
  const router = useRouter()
  const { data, error, isLoading, mutate } = useSWR<T, ApiError>(path, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  useEffect(() => {
    if (error?.unauthenticated) router.replace('/login')
  }, [error, router])

  return { data, error, isLoading, mutate }
}

export function Loading() {
  return <div className="empty">Loading…</div>
}

export function ErrorBox({ error }: { error: ApiError }) {
  if (error.unauthenticated) return <Loading />
  return <div className="error-box">{error.message}</div>
}

/** Render `children` once data has arrived, with consistent loading/error UI. */
export function Wiki<T>({
  path,
  children,
}: {
  path: string | null
  children: (data: T, mutate: () => void) => React.ReactNode
}) {
  const { data, error, isLoading, mutate } = useWiki<T>(path)
  if (error) return <ErrorBox error={error} />
  if (isLoading || !data) return <Loading />
  return <>{children(data, mutate)}</>
}
