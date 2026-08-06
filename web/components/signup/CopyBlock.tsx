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
 *
 * The confirmation is a WORD, not an icon. Vector has no icon set and this sheet
 * is not going to acquire one for three states — and "Copied" is unambiguous in a
 * way a tick sitting where a clipboard glyph used to be is not.
 */

import { useState } from 'react'

type State = 'idle' | 'copied' | 'failed'

const LABELS: Record<State, string> = {
  idle: 'Copy',
  copied: 'Copied',
  failed: 'Failed',
}

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
      {label && <div className="subtle">{label}</div>}
      <div className="copyblock">
        <code>{value}</code>
        <button
          type="button"
          className="button"
          onClick={copy}
          aria-label={label ? `Copy ${label}` : 'Copy'}
        >
          {LABELS[state]}
        </button>
      </div>
      {state === 'failed' && (
        <p className="faint">
          Could not reach the clipboard — select the text and copy it manually.
        </p>
      )}
    </div>
  )
}
