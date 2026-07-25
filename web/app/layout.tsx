import type { Metadata } from 'next'
import './globals.css'
import { GeistMono } from 'geist/font/mono'
import { GeistSans } from 'geist/font/sans'
import { Shell } from '@/components/Shell'
import { ThemeProvider } from '@/components/ThemeProvider'

export const metadata: Metadata = {
  title: 'Manthana — team wiki',
  description: 'The shared context layer: what everyone is working on, and what the team knows.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Font variables belong on <html>, not <body>: `@theme inline` reads them
    // through var(), and a variable declared on <body> is out of scope for
    // anything the theme generates above it.
    //
    // suppressHydrationWarning is required, not defensive — next-themes writes
    // class="dark" onto this element before React hydrates, so the server HTML
    // and the first client render legitimately disagree on exactly this
    // attribute. Without it every page logs a hydration mismatch.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body>
        <ThemeProvider>
          <Shell>{children}</Shell>
        </ThemeProvider>
      </body>
    </html>
  )
}
