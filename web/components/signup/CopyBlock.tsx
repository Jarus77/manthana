'use client'

/**
 * A block of text whose whole purpose is to be copied.
 *
 * The button confirms in place and reverts, rather than firing a toast: the
 * reader's attention is already on the thing they just copied, and a notification
 * somewhere else asks them to look away to learn that it worked.
 *
 * `navigator.clipboard` needs a secure context, so it is absent over plain HTTP on
 * anything but localhost. The text stays selectable and the button reports honestly
 * when it could not copy, instead of claiming success into a void.
 */

import { Check, Copy, X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'

type State = 'idle' | 'copied' | 'failed'

export function CopyBlock({ label, value }: { label?: string; value: string }) {
  const [state, setState] = useState<State>('idle')

  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error('no clipboard')
      await navigator.clipboard.writeText(value)
      setState('copied')
    } catch {
      setState('failed')
    }
    setTimeout(() => setState('idle'), 2000)
  }

  return (
    <div>
      {label && <div className="mb-1.5 text-sm text-muted-foreground">{label}</div>}
      <div className="flex items-start gap-2">
        <pre className="min-w-0 flex-1 overflow-x-auto rounded-lg border bg-muted/40 px-3 py-2.5 text-sm">
          <code className="font-mono">{value}</code>
        </pre>
        <Button
          variant="outline"
          size="icon"
          onClick={copy}
          aria-label={label ? `Copy ${label}` : 'Copy'}
          className="mt-0.5 shrink-0"
        >
          {state === 'copied' ? (
            <Check className="size-4 text-success" />
          ) : state === 'failed' ? (
            <X className="size-4 text-destructive" />
          ) : (
            <Copy className="size-4" />
          )}
        </Button>
      </div>
      {state === 'failed' && (
        <p className="mt-1 text-xs text-destructive">
          Could not reach the clipboard — select the text and copy it manually.
        </p>
      )}
    </div>
  )
}
