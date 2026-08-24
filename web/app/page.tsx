/**
 * The front door.
 *
 * A server component with no data fetching, so a stranger gets static HTML on
 * the first paint. The only client JS is the small island that moves an
 * already-signed-in reader along to /home.
 *
 * The copy makes the argument the product actually makes, and says only things
 * that are true of the shipped software: what gets captured, who can see it, and
 * what it costs. No invented customers, no metrics nobody measured, no pricing —
 * the reader is an engineer who will check, and none of those numbers exist yet.
 *
 * THE SAMPLE ARTICLE IS THE ONE FICTION, and it is labelled twice — in the
 * heading and under the panel. The distinction that makes it honest: it
 * demonstrates the OUTPUT SHAPE, which is real and checkable, rather than
 * implying a customer, a scale, or a result. The product's entire claim is that
 * it writes one of these, and describing that in prose while never showing one
 * was the weakest thing about this page.
 *
 * On the encyclopedia sheet it reads as a portal page, which is what Wikipedia's
 * own main page is — including leading with a featured article. No icons: Vector
 * has no icon set, and three decorative glyphs are not worth acquiring one for.
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

        <h2>What Manthana writes — an example</h2>
        <p>
          One small article per project, rewritten as the work changes. This is the shape of
          it. Every claim carries the session it came from, so a reader who doubts a line can
          go read the work behind it.
        </p>
        <div className="featured">
          <div className="featured-head">checkout-rewrite</div>
          <p className="featured-sub">A project in the acme organisation · active</p>
          <dl className="featured-facts">
            <div>
              <dt>Sessions</dt>
              <dd>34</dd>
            </div>
            <div>
              <dt>Contributors</dt>
              <dd>4</dd>
            </div>
            <div>
              <dt>Last active</dt>
              <dd>2 hours ago</dd>
            </div>
            <div>
              <dt>Revisions</dt>
              <dd>7</dd>
            </div>
          </dl>
          <p>
            <b>checkout-rewrite</b> replaces the legacy payment flow with a stateless
            checkout service. It owns card capture, 3DS, and the retry ladder in front of the
            processor.
          </p>
          <h3>Current state</h3>
          <ul>
            <li>
              3DS challenge flow works end to end against the sandbox; live keys are still
              pending compliance sign-off.<cite>[1]</cite>
            </li>
            <li>
              Retries were moved off exponential backoff after it doubled processor
              charges on a partial outage.<cite>[2]</cite>
            </li>
            <li>Idempotency keys are now required on every write path.<cite>[2]</cite></li>
          </ul>
          <h3>Open questions</h3>
          <ul>
            <li>Does the retry ladder need a per-processor cap, or is the global one enough?</li>
          </ul>
        </div>
        <p className="faint">
          Illustrative — not a real customer&rsquo;s article. Nobody wrote any of this by
          hand; it is assembled from finished sessions and rewritten as they arrive.
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

        <h2>What leaves the laptop, and what never does</h2>
        <p>
          The first question an engineer asks is &ldquo;so my founder can read my
          chats?&rdquo; The answer is no, and it is worth being precise about why — a tool a
          team quietly turns off produces nothing.
        </p>
        <table className="wikitable">
          <thead>
            <tr>
              <th>Never leaves the machine</th>
              <th>Leaves, once the engineer releases it</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Sessions marked personal — at any point, retroactively</td>
              <td>A typed digest: intent, approach, files touched, outcome, cost</td>
            </tr>
            <tr>
              <td>Anything not yet released — sync pushes released work only</td>
              <td>Free text, after redaction on the way out</td>
            </tr>
            <tr>
              <td>Live keystrokes — nothing is captured in real time, only finished sessions</td>
              <td>
                The raw transcript, behind a founder-only drill-down that records every look
              </td>
            </tr>
            <tr>
              <td>
                Anything you said in a session you keep personal — the whole session stays put
              </td>
              <td>
                The judgment you contributed: a constraint you knew, a correction with its
                reason. Kept in your name, quoting your words, because it is the part of a
                session nobody can re-derive
              </td>
            </tr>
          </tbody>
        </table>
        <div className="ambox ambox-style">
          <b>Aggregates have a floor.</b>
          <p>
            Cross-team answers need contributions from several people before they are shown at
            all, so a &ldquo;team pattern&rdquo; can never be one person&rsquo;s activity in
            disguise. With three engineers, most org-wide output is empty by design. An org
            can waive this deliberately; it is never off by accident.
          </p>
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
