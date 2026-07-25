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
import { Notice } from '@/components/manthana'
import { Skeleton } from '@/components/ui/skeleton'
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
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-64" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
    </div>
  )
}

export function ErrorBox({ error }: { error: ApiError }) {
  // A 401 is not an error state — the layout is already redirecting to /login, so
  // showing a failure would flash a problem the reader cannot act on.
  if (error.unauthenticated) return <Loading />
  return <Notice tone="disputed">{error.message}</Notice>
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
