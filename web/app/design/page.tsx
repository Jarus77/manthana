'use client'

/**
 * The design system, as running components.
 *
 * A specimen page rather than a document, because a palette in a PDF tells you
 * nothing about whether amber still reads as "confirmed" when it sits next to a
 * disputed claim in a real list. Everything here is the actual component under
 * the actual tokens, in both themes.
 *
 * Non-happy states are shown on purpose — empty, loading, error. They are the
 * ones that get skipped and then improvised inconsistently later.
 */

import { Logo, Mark } from '@/components/Logo'
import { ThemeToggle } from '@/components/ThemeToggle'
import {
  Categories,
  Citations,
  EmptyState,
  FactCard,
  Mono,
  Notice,
  SectionHeading,
  StatusText,
  Tag,
} from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function Swatch({ name, className, note }: { name: string; className: string; note?: string }) {
  return (
    <div className="space-y-1.5">
      <div className={`h-14 w-full rounded-md border ${className}`} />
      <div className="text-xs">
        <div className="font-medium">{name}</div>
        {note && <div className="text-muted-foreground">{note}</div>}
      </div>
    </div>
  )
}

export default function DesignPage() {
  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <header className="sticky top-0 z-10 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <Logo />
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">Design system</span>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 pb-24">
        <section className="py-10">
          <h1 className="text-3xl font-semibold tracking-tight">Manthana</h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            <em>Manthana</em> is the Sanskrit for churning — the churning of the ocean,
            sustained opposing effort producing something precious. That is what the product
            does to engineering sessions, and it is what this system is built to say:{' '}
            <strong className="font-medium text-foreground">motion resolving into clarity</strong>.
          </p>
        </section>

        {/* ── mark ─────────────────────────────────────────────────────── */}
        <SectionHeading>The mark</SectionHeading>
        <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
          Two arcs turning against each other around a solid centre — raw motion outside, the
          distilled thing at the middle. Below 20px the counter-arc is dropped, because two thin
          strokes close into a grey ring at favicon size.
        </p>
        <div className="flex flex-wrap items-end gap-8 rounded-lg border p-6">
          {[64, 40, 24, 16].map((size) => (
            <div key={size} className="space-y-2 text-center">
              <Mark size={size} className="text-primary" />
              <div className="text-xs text-muted-foreground">{size}px</div>
            </div>
          ))}
          <div className="space-y-2">
            <Logo size={28} />
            <div className="text-xs text-muted-foreground">lockup</div>
          </div>
        </div>

        {/* ── colour ───────────────────────────────────────────────────── */}
        <SectionHeading>Colour</SectionHeading>
        <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
          One accent. Indigo carries anything actionable; slate is the ground. Amber is not a
          highlight — it means <em>distilled</em>: knowledge that survived contact with a human.
        </p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Swatch name="primary" className="bg-primary" note="indigo · actionable" />
          <Swatch name="distilled" className="bg-distilled" note="amber · confirmed" />
          <Swatch name="success" className="bg-success" note="emerald" />
          <Swatch name="destructive" className="bg-destructive" note="red · disputed" />
          <Swatch name="background" className="bg-background" note="page" />
          <Swatch name="card" className="bg-card" note="raised" />
          <Swatch name="muted" className="bg-muted" note="recessed" />
          <Swatch name="border" className="bg-border" note="hairlines" />
        </div>

        {/* ── type ─────────────────────────────────────────────────────── */}
        <SectionHeading>Type</SectionHeading>
        <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
          Geist Sans for interface and prose, Geist Mono for anything a machine produced — ids,
          money, commands, timestamps. Both self-hosted, so no network fetch at build.
        </p>
        <div className="space-y-3 rounded-lg border p-6">
          <div className="text-3xl font-semibold tracking-tight">What your team learned</div>
          <div className="text-lg">Sessions this week, and what came out of them</div>
          <div className="text-base">
            The body size everything is read at — digests, notes, article prose.
          </div>
          <div className="text-sm text-muted-foreground">
            Secondary text: provenance, counts, and the small print under a row.
          </div>
          <div className="pt-2">
            <Mono>manthana setup mia_eyJzZXJ2ZXIiOiJ…</Mono>
          </div>
          <div>
            <Mono>$0.82 / $100.00</Mono>
            <span className="mx-2 text-muted-foreground">·</span>
            <Mono>2026-07-19 14:52</Mono>
          </div>
        </div>

        {/* ── notices ──────────────────────────────────────────────────── */}
        <SectionHeading>Notices</SectionHeading>
        <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
          These describe the trustworthiness of an <em>entry</em>, not the outcome of the
          reader&apos;s last action. A left rule marks the state; the words explain what would
          fix it.
        </p>
        <div className="space-y-3">
          <Notice tone="disputed" title="The accuracy of this entry is disputed.">
            Later sessions contradict it. The conflicting evidence is listed under Conflicting
            evidence; correcting the text resolves the dispute.
          </Notice>
          <Notice tone="unreviewed" title="This entry has not been reviewed by a human.">
            Manthana wrote it from the sessions cited below. Read it against that evidence, then
            correct or confirm it.
          </Notice>
          <Notice title="The evidence behind this entry has been purged.">
            It is kept because nobody has disputed it, but the sessions it came from can no
            longer be read.
          </Notice>
        </div>

        {/* ── states ───────────────────────────────────────────────────── */}
        <SectionHeading>Editorial states</SectionHeading>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border p-6 text-sm">
          <span>
            Postgres connection pooling <StatusText tone="distilled">confirmed</StatusText>
          </span>
          <span>
            Retry budget is 3 <StatusText tone="warning">unreviewed</StatusText>
          </span>
          <span>
            Build takes 90s <StatusText tone="danger">disputed</StatusText>
          </span>
          <span>
            Old deploy runbook <StatusText tone="muted">stale</StatusText>
          </span>
        </div>

        {/* ── facts + citations ────────────────────────────────────────── */}
        <SectionHeading>Facts and evidence</SectionHeading>
        <div className="grid gap-6 md:grid-cols-2">
          <FactCard
            title="priya"
            subtitle="Engineer · acmeco"
            rows={[
              ['Sessions', '128'],
              ['Projects', 'manthana, infra'],
              ['Most recent', '2 hours ago'],
              ['Confirmed notes', <StatusText key="c" tone="distilled">14</StatusText>],
            ]}
          />
          <div>
            <p className="text-sm">
              The enrichment pass runs every 15 minutes and is bounded per org.
              <sup className="ml-0.5 text-primary">[1]</sup>
            </p>
            <Citations>
              <li>
                Bounded the enrichment batch per org — priya, manthana, 19 July 2026 (success)
              </li>
              <li>Fixed the recursion in agent compaction — suraj, manthana, 18 July 2026</li>
            </Citations>
          </div>
        </div>

        {/* ── table ────────────────────────────────────────────────────── */}
        <SectionHeading>Dense data</SectionHeading>
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Session</TableHead>
                <TableHead>Engineer</TableHead>
                <TableHead>Project</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                ['Bounded the enrichment batch per org', 'priya', 'manthana', '$0.42', 'success'],
                ['Fixed recursion in agent compaction', 'suraj', 'manthana', '$1.08', 'success'],
                ['Untitled session — awaiting summary', 'dev', 'unknown', '$0.00', 'abandoned'],
              ].map(([title, who, project, cost, outcome]) => (
                <TableRow key={title}>
                  <TableCell className="max-w-xs truncate">{title}</TableCell>
                  <TableCell className="text-muted-foreground">{who}</TableCell>
                  <TableCell className="text-muted-foreground">{project}</TableCell>
                  <TableCell className="text-right">
                    <Mono>{cost}</Mono>
                  </TableCell>
                  <TableCell>
                    {outcome === 'success' ? (
                      <StatusText tone="distilled">success</StatusText>
                    ) : (
                      <StatusText tone="muted">{outcome}</StatusText>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        {/* ── controls ─────────────────────────────────────────────────── */}
        <SectionHeading>Controls</SectionHeading>
        <div className="space-y-6 rounded-lg border p-6">
          <div className="flex flex-wrap items-center gap-3">
            <Button>Create organization</Button>
            <Button variant="secondary">Copy invite</Button>
            <Button variant="outline">Cancel</Button>
            <Button variant="ghost">Skip</Button>
            <Button variant="destructive">Delete org</Button>
            <Button disabled>Working…</Button>
          </div>
          <div className="grid max-w-sm gap-2">
            <Label htmlFor="org">Organization name</Label>
            <Input id="org" placeholder="Acme Co" defaultValue="Acme Co" />
          </div>
          <div className="flex flex-wrap gap-2">
            <Tag>12 sessions</Tag>
            <Tag tone="primary">manthana</Tag>
            <Tag tone="distilled">confirmed</Tag>
          </div>
        </div>

        {/* ── non-happy paths ──────────────────────────────────────────── */}
        <SectionHeading>Empty, loading, error</SectionHeading>
        <div className="grid gap-4 md:grid-cols-3">
          <EmptyState hint={<Button size="sm">Invite your team</Button>}>
            No sessions yet. They appear here once an engineer runs{' '}
            <Mono>manthana setup</Mono>.
          </EmptyState>
          <div className="space-y-2 rounded-lg border p-6">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-5/6" />
          </div>
          <Notice tone="disputed" title="Could not reach the server">
            The wiki is served from the same origin as the API. Retrying usually fixes it.
          </Notice>
        </div>

        <Categories items={['Design system', 'Manthana', 'Phase 1']} />
      </main>
    </div>
  )
}
