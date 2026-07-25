/**
 * The front door.
 *
 * A server component with no data fetching, so a stranger gets static HTML on
 * the first paint. The only client JS is the theme toggle and the small island
 * that moves an already-signed-in reader along to /home.
 *
 * The copy makes the argument the product actually makes, and says only things
 * that are true of the shipped software: what gets captured, who can see it, and
 * what it costs. No invented customers, no metrics nobody measured — the reader
 * is an engineer who will check.
 */

import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowRight, GitBranch, MessagesSquare, Sparkles } from 'lucide-react'
import { Logo, Mark } from '@/components/Logo'
import { SignedInRedirect } from '@/components/SignedInRedirect'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Mono, SectionHeading } from '@/components/manthana'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

export const metadata: Metadata = {
  title: 'Manthana — the shared context behind what your team builds',
  description:
    'Your engineers learn something in every coding session, and it dies when the session closes. Manthana captures it, distils it into a team wiki, and lets you ask it questions.',
}

/** The two lines an engineer actually runs. Shown, not described. */
const INSTALL = `curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana setup mia_…`

function Step({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof GitBranch
  title: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <Icon className="mb-3 size-5 text-primary" />
        <h3 className="mb-1.5 font-medium">{title}</h3>
        <p className="text-sm text-muted-foreground">{children}</p>
      </CardContent>
    </Card>
  )
}

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <SignedInRedirect />

      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Logo />
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button asChild variant="ghost" size="sm">
              <Link href="/login">Sign in</Link>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6">
        {/* ── hero ───────────────────────────────────────────────────── */}
        <section className="py-20 sm:py-28">
          <h1 className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">
            Your team learns something every day.
            <span className="block text-muted-foreground">
              Almost none of it survives the session.
            </span>
          </h1>
          <p className="mt-6 max-w-2xl text-lg text-muted-foreground">
            Engineers work things out inside coding sessions — why the migration had to run
            twice, which config actually fixed the timeout, what the retry budget is now.
            When the session closes, it is gone, and the next person works it out again.
          </p>
          <p className="mt-4 max-w-2xl text-lg">
            Manthana captures what was learned, distils it into a wiki your team can read, and
            lets you ask questions that come back with citations.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button asChild size="lg">
              <a href="/ui/auth/google">
                Sign in with Google
                <ArrowRight className="ml-1 size-4" />
              </a>
            </Button>
            <span className="text-sm text-muted-foreground">
              Creates your organization. About a minute.
            </span>
          </div>
        </section>

        {/* ── the install, as proof ──────────────────────────────────── */}
        <section className="pb-16">
          <div className="rounded-lg border bg-muted/40 p-5">
            <p className="mb-3 text-sm text-muted-foreground">
              Then each engineer runs two lines. No account to create, nothing to configure.
            </p>
            <pre className="overflow-x-auto text-sm">
              <code className="font-mono">{INSTALL}</code>
            </pre>
          </div>
        </section>

        {/* ── how it works ───────────────────────────────────────────── */}
        <SectionHeading>How it works</SectionHeading>
        <div className="grid gap-4 py-2 md:grid-cols-3">
          <Step icon={GitBranch} title="Capture">
            A small agent on each laptop summarises finished coding sessions locally. Nothing
            leaves the machine until the engineer releases it.
          </Step>
          <Step icon={Sparkles} title="Distil">
            Released sessions become a living article per project, and typed notes — decisions,
            gotchas, benchmarks — each one citing the sessions it came from.
          </Step>
          <Step icon={MessagesSquare} title="Ask">
            Ask in plain language and get an answer grounded in real sessions, with the
            evidence attached. When there is not enough evidence, it says so instead of
            guessing.
          </Step>
        </div>

        {/* ── founders ───────────────────────────────────────────────── */}
        <SectionHeading>What you see as a founder</SectionHeading>
        <div className="grid gap-8 py-2 md:grid-cols-2">
          <div className="space-y-3 text-sm">
            <p>
              A weekly digest of what actually happened, written from the work rather than from
              standup. What each project moved on, what stalled, and what the team learned that
              nobody wrote down.
            </p>
            <p>
              Plus the operational things you would otherwise guess at: what your AI coding
              spend went on, which sessions could have run on a cheaper model, and where the
              same problem is being solved twice in two places.
            </p>
          </div>
          <div className="space-y-3 text-sm">
            <p className="font-medium">Every answer is cited.</p>
            <p className="text-muted-foreground">
              Narratives point back at the sessions they came from, so you can read the
              original work rather than trusting a summary. A claim nobody has reviewed says
              so; a claim later sessions contradict says that too.
            </p>
          </div>
        </div>

        {/* ── privacy ────────────────────────────────────────────────── */}
        <SectionHeading>What it does not do</SectionHeading>
        <div className="grid gap-4 py-2 sm:grid-cols-2">
          <Card>
            <CardContent className="space-y-2 pt-6 text-sm">
              <p className="font-medium">It is not surveillance.</p>
              <p className="text-muted-foreground">
                Engineers choose what is released. A session can be marked personal and never
                leaves the laptop. Nothing is captured in real time — only finished sessions,
                only after review.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="space-y-2 pt-6 text-sm">
              <p className="font-medium">Aggregates have a floor.</p>
              <p className="text-muted-foreground">
                By default, cross-team answers need contributions from several people before
                they are shown at all, so a &ldquo;team pattern&rdquo; can never be one
                person&rsquo;s activity in disguise. An org can waive this deliberately; it is
                not off by accident.
              </p>
            </CardContent>
          </Card>
        </div>

        {/* ── close ──────────────────────────────────────────────────── */}
        <section className="py-20">
          <div className="rounded-xl border p-8 text-center">
            <Mark size={32} className="mx-auto mb-4 text-primary" />
            <h2 className="text-2xl font-semibold tracking-tight">
              Start with your own sessions
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-muted-foreground">
              Sign in, run the install line on your own machine, and see what a week of your
              work looks like written down. Invite the team once it is worth reading.
            </p>
            <Button asChild size="lg" className="mt-6">
              <a href="/ui/auth/google">
                Sign in with Google
                <ArrowRight className="ml-1 size-4" />
              </a>
            </Button>
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-sm text-muted-foreground">
          <span>
            <Mono>Manthana</Mono> — Sanskrit for the churning that separates what is worth
            keeping.
          </span>
          <nav className="flex gap-4">
            <a className="hover:text-foreground" href="https://docs.latentspaces.in">
              Docs
            </a>
            <a className="hover:text-foreground" href="https://github.com/Jarus77/manthana">
              GitHub
            </a>
            <Link className="hover:text-foreground" href="/login">
              Sign in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  )
}
