'use client'

/** Search box. Wikipedia's is plain: a field and a button, no ornament. */

import { useRouter } from 'next/navigation'
import { useState } from 'react'

export function AskBar({
  placeholder = 'Search the wiki, or ask a question',
  initial = '',
}: {
  placeholder?: string
  initial?: string
}) {
  const router = useRouter()
  const [query, setQuery] = useState(initial)

  return (
    <form
      className="searchbar"
      onSubmit={(e) => {
        e.preventDefault()
        const q = query.trim()
        if (q) router.push(`/ask?q=${encodeURIComponent(q)}`)
      }}
    >
      <input
        type="search"
        value={query}
        placeholder={placeholder}
        aria-label="Search the wiki"
        onChange={(e) => setQuery(e.target.value)}
      />
      <button className="button-progressive" type="submit">
        Search
      </button>
    </form>
  )
}
