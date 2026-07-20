'use client'

/**
 * The ask box. Present in the top bar on every page, and again, larger, on the
 * home page: the two ways in are "I have a question" and "show me what's
 * happening", and neither should require finding the other first.
 */

import { useRouter } from 'next/navigation'
import { useState } from 'react'

export function AskBar({
  hero = false,
  placeholder = 'Ask anything — e.g. why did we pin torch 2.4?',
  initial = '',
}: {
  hero?: boolean
  placeholder?: string
  initial?: string
}) {
  const router = useRouter()
  const [query, setQuery] = useState(initial)

  return (
    <form
      className={`askbar${hero ? ' askbar-hero' : ''}`}
      onSubmit={(e) => {
        e.preventDefault()
        const q = query.trim()
        if (q) router.push(`/ask?q=${encodeURIComponent(q)}`)
      }}
    >
      <input
        type="text"
        value={query}
        placeholder={placeholder}
        aria-label="Ask the wiki"
        onChange={(e) => setQuery(e.target.value)}
      />
      <button className="btn-primary" type="submit">
        Ask
      </button>
    </form>
  )
}
