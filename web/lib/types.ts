/**
 * Payload shapes returned by server/src/manthana/server/wiki_api.py.
 *
 * These mirror the server's dataclasses (skills/projections.py, server/pages.py,
 * server/graph.py) and the KnowledgeNote pydantic model. They are hand-written
 * rather than generated: the API is small and stable, and a generator would add
 * a build step to a client whose whole point is that it has almost none.
 */

export type NoteKind =
  | 'decision'
  | 'convention'
  | 'gotcha'
  | 'failure_pattern'
  | 'benchmark'
  | 'procedure_ref'
  | 'faq'

export type NoteStatus =
  | 'candidate'
  | 'established'
  | 'disputed'
  | 'stale'
  | 'superseded'

export interface Note {
  id: string
  org_id: string
  kind: NoteKind
  title: string
  body: string
  scope: string
  project?: string
  entities: { files: string[]; libraries: string[]; projects: string[]; concepts: string[] }
  evidence: string[]
  disputed_by: string[]
  actors: string[]
  source: 'ai' | 'human'
  author: string | null
  confidence: number | null
  status: NoteStatus
  confirmed_by: string | null
  version: number
  supersedes: string | null
  superseded_by: string | null
  metric: string | null
  value: string | null
  created_at: string
  updated_at: string
  last_confirmed_at: string | null
}

/** One released session digest. Never carries raw transcript turns. */
export interface Session {
  id: string
  session_id: string
  actor: string
  project: string
  surface: string
  started_at: string
  duration_seconds: number
  task_intent: string
  approach: string
  outcome: string
  friction: string[]
  artifacts: string[]
  files_touched: string[]
  prs_opened: string[]
  tests_added: string[]
  languages: string[]
  tier_used: string | null
  est_cost_usd: number | null
  total_tokens: number | null
  source: string
  released: boolean
  hold: boolean
}

export interface ProjectRollup {
  project: string
  sessions: number
  actors: string[]
  outcome_mix: Record<string, number>
  last_active: string
  top_intent: string
  est_cost_usd: number
  total_tokens: number
}

export interface ActorActivity {
  actor: string
  sessions: number
  projects: string[]
  intents: string[]
  last_active: string
  outcome_mix: Record<string, number>
}

/**
 * A collaborator link, with the evidence that produced it.
 *
 * `via_*` are capped display samples; `shared_*` are the true counts. Always
 * count from `shared_*` — the samples under-report exactly the strongest links.
 */
export interface NoteRef {
  id: string
  title: string
}

export interface PersonEdge {
  actor: string
  weight: number
  shared_projects: number
  shared_notes: number
  shared_files: number
  via_projects: string[]
  via_notes: NoteRef[]
  via_files: string[]
}

/** How an edge is phrased for a reader: the shared work, most specific first. */
export function edgeReason(edge: PersonEdge): string {
  const parts: string[] = []
  if (edge.via_projects.length) parts.push(edge.via_projects.join(', '))
  if (edge.shared_notes) {
    parts.push(`${edge.shared_notes} shared note${edge.shared_notes === 1 ? '' : 's'}`)
  }
  if (edge.shared_files) {
    parts.push(`${edge.shared_files} shared file${edge.shared_files === 1 ? '' : 's'}`)
  }
  return parts.join(' · ')
}

export interface ProjectEdge {
  project: string
  weight: number
  via_actors: string[]
}

export interface Section {
  kind: NoteKind
  notes: Note[]
}

export interface Me {
  role: 'admin' | 'founder' | 'engineer'
  org_id: string
  actor: string | null
  author: string
  can_switch_org: boolean
  orgs: string[]
  kinds: NoteKind[]
  kind_counts: Record<NoteKind, number>
  total_notes: number
}

export interface HomeFeed {
  org_id: string
  since: string
  stream: Session[]
  sections: Section[]
  projects: ProjectRollup[]
  people: ActorActivity[]
  // `moved` is a server-side @property and so is absent from JSON — compare the
  // values here instead.
  benchmarks: Record<string, { note: Note; previous_value: string | null }>
  unreviewed: number
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
  total?: number
  org_id: string
}

export interface PersonPage {
  actor: string
  activity: ActorActivity | null
  sections: Section[]
  sessions: Session[]
  connections: PersonEdge[]
  org_id: string
}

export interface ProjectPage {
  project: string
  rollup: ProjectRollup | null
  sections: Section[]
  sessions: Session[]
  note_count: number
  neighbors: ProjectEdge[]
  org_id: string
}

export interface SessionPage {
  session: Session
  /** The coding agent's own compaction summary, redacted like every other free
   *  text field. Null when the session carried none. NOT the raw transcript. */
  native_summary: string | null
  source: string | null
  notes: Note[]
  disputes: Note[]
  same_actor: Session[]
  same_project: Session[]
  org_id: string
}

export interface NotePage {
  note: Note
  evidence: Session[]
  disputed_by: Session[]
  org_id: string
}

export interface AskResult {
  query: string
  narrative: string
  coverage: string
  insufficient_data: boolean
  drilled: boolean
  notes: Note[]
  sessions: Session[]
  org_id: string
}

/** Human-readable labels for the fixed note taxonomy. */
export const KIND_LABEL: Record<NoteKind, string> = {
  decision: 'Decisions',
  convention: 'Conventions',
  gotcha: 'Gotchas',
  failure_pattern: 'Failure patterns',
  benchmark: 'Benchmarks',
  procedure_ref: 'Procedures',
  faq: 'FAQ',
}

export const KIND_SINGULAR: Record<NoteKind, string> = {
  decision: 'decision',
  convention: 'convention',
  gotcha: 'gotcha',
  failure_pattern: 'failure pattern',
  benchmark: 'benchmark',
  procedure_ref: 'procedure',
  faq: 'question',
}
