'use client'

/**
 * A knowledge entry, as an article.
 *
 * This page is where the encyclopedia framing earns the most. A claim written
 * by a model from session evidence, which a human may correct, is structurally
 * a Wikipedia article: it has provenance, sources, a revision history, and
 * maintenance notices when it is unreviewed or contradicted. So it renders as
 * one — banner at the top, body as prose, evidence as a numbered reference
 * list, categories at the foot.
 */

import Link from 'next/link'
import { use } from 'react'
import { Wiki } from '@/components/Loader'
import { TeachControls } from '@/components/TeachControls'
import {
  CatLinks,
  Empty,
  Hatnote,
  Infobox,
  Markdown,
  NoteBanners,
  PersonList,
  ProjectLink,
  Reflist,
  Section,
  Title,
  onDate,
  statusWord,
} from '@/components/primitives'
import { KIND_SINGULAR, type NotePage } from '@/lib/types'

export default function NoteArticle({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)

  return (
    <Wiki<NotePage> path={`/notes/${encodeURIComponent(id)}`}>
      {(data, mutate) => {
        const note = data.note
        const status = statusWord(note)
        const kind = KIND_SINGULAR[note.kind] ?? note.kind

        return (
          <>
            <Title tagline={`A ${kind} recorded in the Manthana wiki`}>{note.title}</Title>

            <NoteBanners note={note} />

            <Hatnote>
              {note.source === 'human' ? (
                <>
                  Written by <b>{note.author}</b>. Human entries are authoritative — Manthana
                  may dispute this with evidence, but will never overwrite it.
                </>
              ) : (
                <>
                  Written by Manthana from the {data.evidence.length} session
                  {data.evidence.length === 1 ? '' : 's'} cited below.
                </>
              )}
            </Hatnote>

            <Infobox
              title={note.title}
              subtitle={kind[0].toUpperCase() + kind.slice(1)}
              rows={[
                ['Status', status ? <span className={status.cls}>{status.text}</span> : 'established'],
                ['Source', note.source === 'human' ? `${note.author} (human)` : 'Manthana'],
                ['Project', note.project ? <ProjectLink project={note.project} /> : '—'],
                ['People', note.actors.length ? <PersonList actors={note.actors} /> : '—'],
                ['Evidence', `${data.evidence.length} session${data.evidence.length === 1 ? '' : 's'}`],
                ...(note.metric && note.value
                  ? ([[note.metric, <b key="v">{note.value}</b>]] as Array<[string, React.ReactNode]>)
                  : []),
                ['Revision', `${note.version}`],
                ['Updated', onDate(note.updated_at)],
                ['Confirmed by', note.confirmed_by ?? '—'],
              ]}
            />

            <div className="lead">
              <Markdown>{note.body}</Markdown>
            </div>

            <Section title="Evidence">
              {data.evidence.length ? (
                <>
                  <p className="subtle">
                    The sessions this entry was drawn from. Reading them is how you check it.
                  </p>
                  <Reflist sessions={data.evidence} />
                </>
              ) : (
                <Empty>
                  {note.source === 'human'
                    ? 'Added by hand — this knowledge never came from a session.'
                    : 'The sessions behind this entry have since been purged.'}
                </Empty>
              )}
            </Section>

            {data.disputed_by.length > 0 && (
              <Section id="disputed" title="Conflicting evidence">
                <p className="subtle">Later sessions that contradict the claim above.</p>
                <Reflist sessions={data.disputed_by} />
              </Section>
            )}

            <Section
              title="Edit"
              action={<Link href={`/notes/${note.id}/history`}>history</Link>}
            >
              <TeachControls note={note} onChanged={mutate} />
            </Section>

            <CatLinks
              categories={[
                { label: KIND_SINGULAR[note.kind] ?? note.kind, href: `/knowledge/${note.kind}` },
                ...(note.project
                  ? [{ label: note.project, href: `/projects/${encodeURIComponent(note.project)}` }]
                  : []),
                ...(note.status === 'candidate'
                  ? [{ label: 'Unreviewed entries', href: '/knowledge/all?status=candidate' }]
                  : []),
                ...(note.source === 'human' ? [{ label: 'Human-written entries' }] : []),
              ]}
            />
          </>
        )
      }}
    </Wiki>
  )
}
