'use client'

/**
 * Theme plumbing.
 *
 * `defaultTheme="light"` with `enableSystem={false}` is a product decision, not a
 * default left unexamined. The previous stylesheet auto-switched on
 * prefers-color-scheme, which meant anyone whose laptop was in dark mode got a
 * dark wiki whether or not they wanted one — you could not choose light. Dark is
 * offered here as a real toggle instead, and the choice persists.
 */

import { ThemeProvider as NextThemeProvider } from 'next-themes'

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      disableTransitionOnChange
    >
      {children}
    </NextThemeProvider>
  )
}
