'use client'

import { Suspense } from 'react'
import { ConsoleShell } from '@/components/console/Shell'
import { Skeleton } from '@/components/ui/skeleton'

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  // The shell reads ?org= to decide which tenant is in view, and useSearchParams
  // needs a Suspense boundary or every console route opts into dynamic rendering.
  return (
    <Suspense fallback={<Skeleton className="m-6 h-40" />}>
      <ConsoleShell>{children}</ConsoleShell>
    </Suspense>
  )
}
