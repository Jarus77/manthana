import type { Metadata } from 'next'
import './globals.css'
import { Shell } from '@/components/Shell'

export const metadata: Metadata = {
  title: 'Manthana — team wiki',
  description: 'The shared context layer: what everyone is working on, and what the team knows.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  )
}
