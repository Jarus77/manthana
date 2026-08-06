'use client'

import { Suspense } from 'react'
import { ConsoleShell } from '@/components/console/Shell'
import { Loading } from '@/components/Loader'

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  // The shell reads ?org= to decide which tenant is in view, and useSearchParams
  // needs a Suspense boundary or every console route opts into dynamic rendering.
  return (
    <Suspense fallback={<Loading />}>
      <ConsoleShell>{children}</ConsoleShell>
    </Suspense>
  )
}
