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
import { EmptyState, Notice, SectionHeading, Tag } from '@/components/manthana'
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

      {failure && (
        <div className="mb-6">
          <Notice tone="disputed">{failure}</Notice>
        </div>
      )}

      {/* ── invites ─────────────────────────────────────────────────── */}
      <SectionHeading>Invite engineers</SectionHeading>
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        One line to send them. That is their whole onboarding, and it is what starts their
        sessions flowing into the wiki. Leave the email blank for a shared invite the whole
        team can use; fill it in for a single-use, per-person one.
      </p>
      <form
        className="mb-5 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void run('invite', async () => {
            setMinted(await consolePost<NewInvite>('/invites', { org_id: org, actor: inviteFor }))
            setInviteFor('')
            await refetchInvites()
          })
        }}
      >
        <div className="grid gap-1.5">
          <Label htmlFor="invite-for">Engineer email (optional)</Label>
          <Input
            id="invite-for"
            className="w-72"
            value={inviteFor}
            onChange={(e) => setInviteFor(e.target.value)}
            placeholder="engineer@yourcompany.com"
          />
        </div>
        <Button type="submit" disabled={busy === 'invite'}>
          {busy === 'invite' ? 'Creating…' : 'Create invite'}
        </Button>
      </form>

      {minted && (
        <div className="mb-6 space-y-3 rounded-lg border p-5">
          <p className="text-sm">
            Invite created{' '}
            {minted.actor ? (
              <>
                for <span className="font-medium">{minted.actor}</span> — single use.
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
        <Skeleton className="h-20 w-full" />
      ) : invites.invites.length === 0 ? (
        <EmptyState>No open invites.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>For</TableHead>
                <TableHead className="text-right">Uses left</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {invites.invites.map((i) => (
                <TableRow key={i.code}>
                  <TableCell>{i.actor ?? <span className="text-muted-foreground">shared</span>}</TableCell>
                  <TableCell className="text-right tabular">{i.uses_left}</TableCell>
                  <TableCell className="text-muted-foreground">{i.expires_at.slice(0, 10)}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        void run('revoke', async () => {
                          await consolePost('/invites/revoke', { org_id: org, code: i.code })
                          await refetchInvites()
                        })
                      }
                    >
                      Revoke
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* ── wiki logins ─────────────────────────────────────────────── */}
      <SectionHeading>Wiki access without installing anything</SectionHeading>
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        A login for someone who should read and correct the wiki but is not running the
        agent. They never see cost, mining, or audit — those are yours.
      </p>
      <form
        className="mb-5 flex flex-wrap items-end gap-2"
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
        <div className="grid gap-1.5">
          <Label htmlFor="token-for">Their email</Label>
          <Input
            id="token-for"
            className="w-72"
            value={tokenFor}
            onChange={(e) => setTokenFor(e.target.value)}
            placeholder="teammate@yourcompany.com"
            required
          />
        </div>
        <Button type="submit" variant="secondary" disabled={busy === 'token' || !tokenFor.trim()}>
          {busy === 'token' ? 'Creating…' : 'Create wiki login'}
        </Button>
      </form>

      {token && (
        <div className="mb-6 space-y-3 rounded-lg border p-5">
          <p className="text-sm">
            Wiki login for <span className="font-medium">{token.actor}</span>. Their edits will
            be recorded under their own name.
          </p>
          <CopyBlock label="Token" value={token.token} />
          <CopyBlock label="Sign-in link" value={token.login_url} />
          <Notice tone="unreviewed" title="Shown once.">
            It is a signed token, so there is nothing stored to look up later. If it is lost,
            create another — both keep working.
          </Notice>
        </div>
      )}

      {/* ── members ─────────────────────────────────────────────────── */}
      <SectionHeading>Members</SectionHeading>
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        Everyone who joins by email domain or an invite link lands as an engineer. Promote
        someone when they should also see cost, mining, and audit.
      </p>
      {!members ? (
        <Skeleton className="h-20 w-full" />
      ) : members.members.length === 0 ? (
        <EmptyState>Nobody has signed in with Google yet.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.members.map((m) => (
                <TableRow key={m.id}>
                  <TableCell>{m.display_name ?? m.email.split('@')[0]}</TableCell>
                  <TableCell className="text-muted-foreground">{m.email}</TableCell>
                  <TableCell>
                    {m.role === 'founder' ? <Tag tone="primary">founder</Tag> : <Tag>engineer</Tag>}
                  </TableCell>
                  <TableCell className="text-right">
                    {m.role !== 'founder' && (
                      <Button
                        variant="ghost"
                        size="sm"
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
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}
