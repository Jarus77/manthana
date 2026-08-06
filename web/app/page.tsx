/**
 * The front door.
 *
 * A server component with no data fetching, so a stranger gets static HTML on
 * the first paint. The only client JS is the small island that moves an
 * already-signed-in reader along to /home.
 *
 * The copy makes the argument the product actually makes, and says only things
 * that are true of the shipped software: what gets captured, who can see it, and
 * what it costs. No invented customers, no metrics nobody measured — the reader
 * is an engineer who will check.
 *
 * On the encyclopedia sheet it reads as a portal page, which is what Wikipedia's
 * own main page is: a lead panel and a grid of boxes. No icons — Vector has no
 * icon set, and three decorative glyphs are not worth acquiring one for.
 */

import type { Metadata } from 'next'
import Link from 'next/link'
import { SignedInRedirect } from '@/components/SignedInRedirect'

export const metadata: Metadata = {
  title: 'Manthana — the shared context behind what your team builds',
  description:
    'Your engineers learn something in every coding session, and it dies when the session closes. Manthana captures it, distils it into a team wiki, and lets you ask it questions.',
}

/** The two lines an engineer actually runs. Shown, not described. */
const INSTALL = `curl -LsSf https://github.com/Jarus77/manthana/releases/latest/download/install.sh | sh
manthana setup mia_…`

function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="portal-box">
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  )
}

export default function LandingPage() {
  return (
    <div className="signup-shell">
      <SignedInRedirect />

      <header className="signup-header">
        <Link className="brand" href="/">
          Manthana
        </Link>
      </header>

      <main className="marketing">
        <div className="portal-lead">
          <h1>Your team learns something every day.</h1>
          <p className="subtle">Almost none of it survives the session.</p>
        </div>

        <p>
          Engineers work things out inside coding sessions — why the migration had to run
          twice, which config actually fixed the timeout, what the retry budget is now. When
          the session closes, it is gone, and the next person works it out again.
        </p>
        <p>
          Manthana captures what was learned, distils it into a wiki your team can read, and
          lets you ask questions that come back with citations.
        </p>

        <p>
          <a className="button button-progressive" href="/ui/auth/google">
            Sign in with Google
          </a>{' '}
          <span className="faint">Creates your organization. About a minute.</span>
        </p>

        <h2>The install, in full</h2>
        <p>Then each engineer runs two lines. No account to create, nothing to configure.</p>
        <div className="scroll-x">
          <pre className="mono">{INSTALL}</pre>
        </div>

        <h2>How it works</h2>
        <div className="portal-grid">
          <Step title="Capture">
            A small agent on each laptop summarises finished coding sessions locally. Nothing
            leaves the machine until the engineer releases it.
          </Step>
          <Step title="Distil">
            Released sessions become a living article per project, and typed notes — decisions,
            gotchas, benchmarks — each one citing the sessions it came from.
          </Step>
          <Step title="Ask">
            Ask in plain language and get an answer grounded in real sessions, with the
            evidence attached. When there is not enough evidence, it says so instead of
            guessing.
          </Step>
        </div>

        <h2>What you see as a founder</h2>
        <p>
          A weekly digest of what actually happened, written from the work rather than from
          standup. What each project moved on, what stalled, and what the team learned that
          nobody wrote down.
        </p>
        <p>
          Plus the operational things you would otherwise guess at: what your AI coding spend
          went on, which sessions could have run on a cheaper model, and where the same problem
          is being solved twice in two places.
        </p>
        <div className="ambox ambox-style">
          <b>Every answer is cited.</b>
          <p>
            Narratives point back at the sessions they came from, so you can read the original
            work rather than trusting a summary. A claim nobody has reviewed says so; a claim
            later sessions contradict says that too.
          </p>
        </div>

        <h2>What it does not do</h2>
        <div className="portal-grid">
          <div className="portal-box">
            <h2>It is not surveillance</h2>
            <p>
              Engineers choose what is released. A session can be marked personal and never
              leaves the laptop. Nothing is captured in real time — only finished sessions,
              only after review.
            </p>
          </div>
          <div className="portal-box">
            <h2>Aggregates have a floor</h2>
            <p>
              By default, cross-team answers need contributions from several people before they
              are shown at all, so a &ldquo;team pattern&rdquo; can never be one person&rsquo;s
              activity in disguise. An org can waive this deliberately; it is not off by
              accident.
            </p>
          </div>
        </div>

        <h2>Start with your own sessions</h2>
        <p>
          Sign in, run the install line on your own machine, and see what a week of your work
          looks like written down. Invite the team once it is worth reading.
        </p>
        <p>
          <a className="button button-progressive" href="/ui/auth/google">
            Sign in with Google
          </a>
        </p>

        <div className="site-footer">
          <span className="subtle">
            <b>Manthana</b> — Sanskrit for the churning that separates what is worth keeping.
          </span>
          <nav>
            <a href="https://docs.latentspaces.in">Docs</a>
            <a href="https://github.com/Jarus77/manthana">GitHub</a>
            <Link href="/login">Sign in</Link>
          </nav>
        </div>
      </main>
    </div>
  )
}
