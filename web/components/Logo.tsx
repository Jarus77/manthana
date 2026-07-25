/**
 * The Manthana mark — the churn.
 *
 * Manthana is the Sanskrit for churning: the churning of the ocean, sustained
 * opposing effort producing something precious. The mark is that motion reduced
 * to geometry — two arcs turning in opposite directions around a solid centre.
 * Raw motion on the outside, the distilled thing at the middle.
 *
 * Drawn with maths rather than illustrated, for three reasons that all matter at
 * this stage: it is one file with no asset pipeline, it inherits `currentColor`
 * so it is correct in both themes and inside a button without a second variant,
 * and the arcs are stroked on a 24-unit grid so it survives being shrunk to a
 * 16px favicon. Below about 20px the gap between two thin arcs closes up into a
 * grey ring, so `Mark` drops to a single heavier arc at small sizes — the icon
 * stays legible instead of staying faithful.
 */

export function Mark({
  size = 24,
  className,
  title,
}: {
  size?: number
  className?: string
  title?: string
}) {
  // One arc reads at favicon scale; two read everywhere else.
  const dense = size < 20
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title && <title>{title}</title>}
      {/* Outer arc, opening at the top-right — the churn still in motion. */}
      <path
        d="M20.5 12a8.5 8.5 0 1 1-4.2-7.35"
        stroke="currentColor"
        strokeWidth={dense ? 2.6 : 2}
        strokeLinecap="round"
      />
      {/* Inner counter-arc, turning the other way. Dropped at favicon sizes. */}
      {!dense && (
        <path
          d="M3.9 12a4.6 4.6 0 0 0 6.9 3.98"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          opacity={0.55}
        />
      )}
      {/* The centre: what the churning produced. */}
      <circle cx="12" cy="12" r={dense ? 3 : 2.6} fill="currentColor" />
    </svg>
  )
}

/** Mark plus wordmark, for headers and the marketing page. */
export function Logo({
  className,
  size = 22,
}: {
  className?: string
  size?: number
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className ?? ''}`}>
      <Mark size={size} title="Manthana" className="text-primary" />
      {/* -0.01em: Geist sets a touch loose for a wordmark at display size. */}
      <span className="font-sans font-semibold tracking-[-0.01em]" style={{ fontSize: size * 0.85 }}>
        Manthana
      </span>
    </span>
  )
}
