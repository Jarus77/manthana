/**
 * Same-origin fetch wrapper for the wiki API.
 *
 * Auth is the httponly `manthana_admin` cookie the server sets at
 * /ui/api/wiki/login — there is no token in JS to leak, and nothing here has to
 * manage it. A 401 means the cookie is missing or expired, which the layout
 * turns into a redirect to /login rather than an error toast: not being signed
 * in is a state, not a failure.
 */

export const API = '/ui/api/wiki'
/** The signup/onboarding endpoints. Same cookie, same origin, different prefix. */
export const SIGNUP_API = '/ui/api/signup'
/** The founder console's endpoints. */
export const CONSOLE_API = '/ui/api/console'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }

  get unauthenticated(): boolean {
    return this.status === 401
  }
}

async function parse(resp: Response): Promise<never | unknown> {
  if (resp.ok) return resp.json()
  let detail = resp.statusText
  try {
    const body = await resp.json()
    if (typeof body?.detail === 'string') detail = body.detail
  } catch {
    // A non-JSON error body (a proxy's HTML 502, say) — the status is the signal.
  }
  throw new ApiError(resp.status, detail)
}

/** GET `base + path`. */
async function getFrom<T>(base: string, path: string): Promise<T> {
  const resp = await fetch(`${base}${path}`, {
    credentials: 'same-origin',
    headers: { accept: 'application/json' },
  })
  return parse(resp) as Promise<T>
}

/**
 * POST JSON to `base + path`. The content type is not optional: the server
 * rejects non-JSON writes under /ui/api/ as CSRF-shaped, since only a
 * same-origin script can set it — an HTML form cannot.
 */
async function postTo<T>(base: string, path: string, body: unknown = {}): Promise<T> {
  const resp = await fetch(`${base}${path}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify(body),
  })
  return parse(resp) as Promise<T>
}

/** GET a wiki endpoint. `path` is relative to the API root, e.g. "/home". */
export const get = <T,>(path: string): Promise<T> => getFrom<T>(API, path)
export const post = <T,>(path: string, body: unknown = {}): Promise<T> =>
  postTo<T>(API, path, body)

/** The same pair against the signup API. */
export const signupGet = <T,>(path: string): Promise<T> => getFrom<T>(SIGNUP_API, path)
export const signupPost = <T,>(path: string, body: unknown = {}): Promise<T> =>
  postTo<T>(SIGNUP_API, path, body)

/** The same pair against the console API. */
export const consoleGet = <T,>(path: string): Promise<T> => getFrom<T>(CONSOLE_API, path)
export const consolePost = <T,>(path: string, body: unknown = {}): Promise<T> =>
  postTo<T>(CONSOLE_API, path, body)

/** SWR fetchers — the same GETs, keyed by API-relative path. */
export const fetcher = <T,>(path: string): Promise<T> => get<T>(path)
export const signupFetcher = <T,>(path: string): Promise<T> => signupGet<T>(path)
export const consoleFetcher = <T,>(path: string): Promise<T> => consoleGet<T>(path)

/** Build a query string, dropping empty values so URLs stay readable. */
export function qs(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== '',
  )
  return pairs.length ? `?${new URLSearchParams(pairs.map(([k, v]) => [k, String(v)]))}` : ''
}
