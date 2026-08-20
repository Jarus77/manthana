'use client'

/**
 * Growing the team: invites, wiki logins, and promotion.
 *
 * The invite is the PRIMARY path — one line an engineer runs, after which their
 * sessions start flowing into the wiki. A wiki login only lets someone read, which
 * is why it sits second and says so.
 *
 * Both credentials are shown once and never stored. The page says that where it
 * matters, because a founder who assumes they can come back for a token later is a
 * founder who gets stuck.
 */

import { useState } from 'react'
import useSWR from 'swr'
import { PageTitle, useOrgId } from '@/components/console/Shell'
import { CopyBlock } from '@/components/signup/CopyBlock'
import { Loading } from '@/components/Loader'
import { ApiError, consoleFetcher, consolePost, qs } from '@/lib/api'

type Invite = {
  code: string
  team_id: string
  actor: string | null
  uses_left: number
  expires_at: string
}
type Member = { id: string; email: string; display_name: string | null; role: string }
type NewInvite = { code: string; actor: string | null; single_use: boolean; setup_line: string; join_url: string }

export default function TeamPage() {
  const org = useOrgId()
  const opts = { revalidateOnFocus: false, shouldRetryOnError: false }
  const { data: invites, mutate: refetchInvites } = useSWR<{ invites: Invite[] }, ApiError>(
    org ? `/invites${qs({ org_id: org })}` : null, consoleFetcher, opts,
  )
  const { data: members, mutate: refetchMembers } = useSWR<{ members: Member[] }, ApiError>(
    org ? `/members${qs({ org_id: org })}` : null, consoleFetcher, opts,
  )

  const [inviteFor, setInviteFor] = useState('')
  const [minted, setMinted] = useState<NewInvite | null>(null)
  const [tokenFor, setTokenFor] = useState('')
  const [token, setToken] = useState<{ actor: string; token: string; login_url: string } | null>(null)
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState('')

  async function run(what: string, fn: () => Promise<void>) {
    setBusy(what)
    setFailure('')
    try {
      await fn()
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : 'Something went wrong.')
    }
    setBusy('')
  }

  return (
    <>
      <PageTitle>Team</PageTitle>

      {failure && <div className="error-box">{failure}</div>}

      {/* ── invites ─────────────────────────────────────────────────── */}
      <h2>Invite engineers</h2>
      <p>
        One line to send them. That is their whole onboarding, and it is what starts their
        sessions flowing into the wiki. Leave the email blank for a shared invite the whole
        team can use; fill it in for a single-use, per-person one.
      </p>
      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault()
          void run('invite', async () => {
            setMinted(await consolePost<NewInvite>('/invites', { org_id: org, actor: inviteFor }))
            setInviteFor('')
            await refetchInvites()
          })
        }}
      >
        <div className="field grow">
          <label htmlFor="invite-for">Engineer email (optional)</label>
          <input
            id="invite-for"
            type="email"
            value={inviteFor}
            onChange={(e) => setInviteFor(e.target.value)}
            placeholder="engineer@yourcompany.com"
          />
        </div>
        <button type="submit" className="button button-progressive" disabled={busy === 'invite'}>
          {busy === 'invite' ? 'Creating…' : 'Create invite'}
        </button>
      </form>

      {minted && (
        <div className="portal-box">
          <p>
            Invite created{' '}
            {minted.actor ? (
              <>
                for <b>{minted.actor}</b> — single use.
              </>
            ) : (
              <>— shared, reusable by the whole team.</>
            )}
          </p>
          <CopyBlock label="Send them this line" value={minted.setup_line} />
          <CopyBlock label="Or this browser link" value={minted.join_url} />
        </div>
      )}

      {!invites ? (
        <Loading />
      ) : invites.invites.length === 0 ? (
        <div className="empty">No open invites.</div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>For</th>
                <th>Uses left</th>
                <th>Expires</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {invites.invites.map((i) => (
                <tr key={i.code}>
                  <td>{i.actor ?? <span className="subtle">shared</span>}</td>
                  <td className="tabular">{i.uses_left}</td>
                  <td className="subtle">{i.expires_at.slice(0, 10)}</td>
                  <td>
                    <button
                      type="button"
                      className="button"
                      onClick={() =>
                        void run('revoke', async () => {
                          await consolePost('/invites/revoke', { org_id: org, code: i.code })
                          await refetchInvites()
                        })
                      }
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── wiki logins ─────────────────────────────────────────────── */}
      <h2>Wiki access without installing anything</h2>
      <p>
        A login for someone who should read and correct the wiki but is not running the
        agent. They never see cost, mining, or audit — those are yours.
      </p>
      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault()
          void run('token', async () => {
            setToken(
              await consolePost<{ actor: string; token: string; login_url: string }>(
                '/engineer-token', { org_id: org, actor: tokenFor },
              ),
            )
            setTokenFor('')
          })
        }}
      >
        <div className="field grow">
          <label htmlFor="token-for">Their email</label>
          <input
            id="token-for"
            type="email"
            value={tokenFor}
            onChange={(e) => setTokenFor(e.target.value)}
            placeholder="teammate@yourcompany.com"
            required
          />
        </div>
        <button type="submit" className="button" disabled={busy === 'token' || !tokenFor.trim()}>
          {busy === 'token' ? 'Creating…' : 'Create wiki login'}
        </button>
      </form>

      {token && (
        <div className="portal-box">
          <p>
            Wiki login for <b>{token.actor}</b>. Their edits will be recorded under their own
            name.
          </p>
          <CopyBlock label="Token" value={token.token} />
          <CopyBlock label="Sign-in link" value={token.login_url} />
          <div className="ambox ambox-content">
            <b>Shown once.</b>
            <p>
              It is a signed token, so there is nothing stored to look up later. If it is
              lost, create another — both keep working.
            </p>
          </div>
        </div>
      )}

      {/* ── members ─────────────────────────────────────────────────── */}
      <h2>Members</h2>
      <p>
        Everyone who joins by email domain or an invite link lands as an engineer. Promote
        someone when they should also see cost, mining, and audit.
      </p>
      {!members ? (
        <Loading />
      ) : members.members.length === 0 ? (
        <div className="empty">Nobody has signed in with Google yet.</div>
      ) : (
        <div className="scroll-x">
          <table className="wikitable">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.members.map((m) => (
                <tr key={m.id}>
                  <td>{m.display_name ?? m.email.split('@')[0]}</td>
                  <td className="subtle">{m.email}</td>
                  <td>{m.role === 'founder' ? <b>founder</b> : 'engineer'}</td>
                  <td>
                    {m.role !== 'founder' && (
                      <button
                        type="button"
                        className="button"
                        onClick={() =>
                          void run('promote', async () => {
                            await consolePost('/members/promote', {
                              org_id: org, identity_id: m.id,
                            })
                            await refetchMembers()
                          })
                        }
                      >
                        Make founder
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
