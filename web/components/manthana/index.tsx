/**
 * Manthana's component vocabulary, rebuilt on shadcn.
 *
 * This is the same set of ideas the encyclopedia furniture in
 * `components/primitives.tsx` expressed, translated into product UI rather than
 * abandoned. The names are kept because they carry domain meaning that generic
 * component names lose:
 *
 *   Ambox    → Notice      a maintenance banner about an ENTRY's trustworthiness
 *   Infobox  → FactCard    the key facts about a subject, at a glance
 *   Reflist  → Citations   the sessions a claim is grounded in
 *   CatLinks → Categories   what an entry belongs to
 *
 * The one rule worth defending: `distilled` (amber) marks knowledge that survived
 * contact with a human — confirmed, reviewed. It is not "highlight". The moment
 * it starts appearing on things that are merely important, the palette has one
 * fewer meaning and the product has one fewer signal.
 */

import type { StatusTone } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

/* ── notices ────────────────────────────────────────────────────────────── */

export type NoticeTone = 'info' | 'unreviewed' | 'disputed'

const NOTICE_STYLES: Record<NoticeTone, string> = {
  info: 'border-l-muted-foreground/40',
  unreviewed: 'border-l-warning',
  disputed: 'border-l-destructive',
}

/**
 * A banner about the ENTRY, not about the reader's last action.
 *
 * Kept as a left-edge rule rather than a filled panel: these sit above article
 * text and a saturated block at the top of every unreviewed note would make the
 * common case look like an error. The rule marks it; the words explain it.
 */
export function Notice({
  tone = 'info',
  title,
  children,
}: {
  tone?: NoticeTone
  title?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Alert className={cn('border-l-4', NOTICE_STYLES[tone])}>
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

/* ── facts ──────────────────────────────────────────────────────────────── */

/**
 * Key facts about a subject. Rows with no value are dropped rather than rendered
 * empty — a fact card full of dashes reads as missing data when it usually just
 * means the field does not apply.
 */
export function FactCard({
  title,
  subtitle,
  rows,
  className,
}: {
  title: string
  subtitle?: string
  rows: Array<[string, React.ReactNode]>
  className?: string
}) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '')
  return (
    <Card className={cn('w-full', className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[minmax(0,9rem)_1fr] gap-x-4 gap-y-2 text-sm">
          {shown.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="min-w-0 break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

/* ── status ─────────────────────────────────────────────────────────────── */

const TONE_TEXT: Record<StatusTone, string> = {
  distilled: 'text-distilled',
  warning: 'text-warning',
  danger: 'text-destructive',
  muted: 'text-muted-foreground',
}

/**
 * An editorial state, set as words in the flow of a sentence.
 *
 * Deliberately not a filled pill: these appear inline in lists where a row can
 * carry two or three of them, and a line of coloured chips turns a sentence into
 * a status board — which is the failure the previous design was reverted for.
 */
export function StatusText({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return <span className={cn('font-medium', TONE_TEXT[tone])}>{children}</span>
}

/** A count or label attached to a thing. Quiet by default; `distilled` is earned. */
export function Tag({
  tone = 'muted',
  children,
}: {
  tone?: 'muted' | 'distilled' | 'primary'
  children: React.ReactNode
}) {
  return (
    <Badge
      variant="secondary"
      className={cn(
        'font-normal',
        tone === 'distilled' && 'bg-distilled/15 text-distilled',
        tone === 'primary' && 'bg-primary/10 text-primary',
      )}
    >
      {children}
    </Badge>
  )
}

/* ── structure ──────────────────────────────────────────────────────────── */

export function SectionHeading({
  id,
  children,
  action,
}: {
  id?: string
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="mt-8 mb-3 flex items-baseline justify-between gap-4 border-b pb-2">
      <h2 id={id} className="text-lg font-semibold tracking-tight">
        {children}
      </h2>
      {action && <div className="text-sm text-muted-foreground">{action}</div>}
    </div>
  )
}

/**
 * The nothing-here state, treated as a designed surface rather than a blank.
 * `hint` is where the next action goes — an empty page that does not say what to
 * do reads as broken rather than new.
 */
export function EmptyState({
  children,
  hint,
}: {
  children: React.ReactNode
  hint?: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-dashed px-6 py-8 text-center">
      <p className="text-sm text-muted-foreground">{children}</p>
      {hint && <div className="mt-3 text-sm">{hint}</div>}
    </div>
  )
}

/** Sources — the sessions a claim stands on. Numbered, so prose can cite them. */
export function Citations({ children }: { children: React.ReactNode }) {
  return (
    <ol className="mt-2 list-decimal space-y-1 pl-6 text-sm text-muted-foreground marker:text-muted-foreground/60">
      {children}
    </ol>
  )
}

export function Categories({ items }: { items: React.ReactNode[] }) {
  if (!items.length) return null
  return (
    <div className="mt-10 flex flex-wrap items-center gap-2 border-t pt-4 text-sm">
      <span className="text-muted-foreground">Categories</span>
      {items.map((item, i) => (
        <Tag key={i}>{item}</Tag>
      ))}
    </div>
  )
}

/** Monospace for anything a machine produced: ids, money, commands, timestamps. */
export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('font-mono text-[0.925em] tabular', className)}>{children}</span>
}
