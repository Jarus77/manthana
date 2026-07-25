'use client'

/**
 * Light/dark switch.
 *
 * Renders a fixed-size placeholder until mounted rather than nothing at all: the
 * theme is only known on the client, so drawing the real icon during SSR would
 * either mismatch on hydration or make the surrounding row jump by a button's
 * width the moment it resolved.
 */

import { Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  const dark = resolvedTheme === 'dark'

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={mounted ? `Switch to ${dark ? 'light' : 'dark'} theme` : 'Switch theme'}
      onClick={() => setTheme(dark ? 'light' : 'dark')}
      className="size-8"
    >
      {mounted ? (
        dark ? (
          <Sun className="size-4" />
        ) : (
          <Moon className="size-4" />
        )
      ) : (
        <span className="size-4" />
      )}
    </Button>
  )
}
