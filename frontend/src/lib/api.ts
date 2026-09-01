/**
 * The single fetch wrapper every request goes through.
 *
 * Two jobs: attach the bearer token, and turn the backend's documented error
 * body — `{detail, code, field?}` — into a typed `ApiError` that UI code can
 * branch on. Nothing else in the app calls `fetch` directly.
 */

/** The error shape SPEC fixes for every non-2xx response. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly field: string | undefined

  constructor(status: number, detail: string, code: string, field?: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.field = field
  }

  /** True when the token is missing, expired, or names a deleted account. */
  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

/**
 * How the app reads the current token.
 *
 * A getter rather than a stored string: the token lives in React state (see
 * `auth.tsx`) and is never written to localStorage or a cookie, so this module
 * asks for it at call time instead of holding its own copy that could go stale
 * — or outlive the session it belongs to.
 */
type TokenGetter = () => string | null

let getToken: TokenGetter = () => null

export function setTokenGetter(getter: TokenGetter): void {
  getToken = getter
}

function isErrorBody(value: unknown): value is { detail: string; code: string; field?: string } {
  if (typeof value !== 'object' || value === null) return false
  const body = value as Record<string, unknown>
  return typeof body['detail'] === 'string' && typeof body['code'] === 'string'
}

async function toApiError(response: Response): Promise<ApiError> {
  let parsed: unknown = null
  try {
    parsed = await response.json()
  } catch {
    // A proxy error or a crash before the handler can produce a non-JSON body.
    parsed = null
  }
  if (isErrorBody(parsed)) {
    return new ApiError(response.status, parsed.detail, parsed.code, parsed.field)
  }
  return new ApiError(response.status, response.statusText || 'Request failed', 'unknown_error')
}

/**
 * Every API path is namespaced under `/api`.
 *
 * Without it the SPA's routes and the API's routes share one namespace, and a
 * path like `/devices` means two different things depending on whether React
 * Router or the server is answering. That ambiguity breaks on a hard refresh:
 * the browser requests the document `GET /devices`, the dev-server proxy
 * forwards it to the API, and the user sees a raw 401 JSON body instead of the
 * app. Prefixing removes the collision structurally rather than by heuristic.
 *
 * The backend is unaware of this prefix — the dev proxy rewrites it away, so
 * SPEC's endpoint contract (`/devices`, `/readings`, …) is unchanged. A
 * deployment serving both from one origin needs the same rewrite at its edge.
 */
export const API_BASE = '/api'

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  /** Query parameters; `undefined` values are dropped rather than serialized. */
  params?: Record<string, string | number | boolean | undefined>
  signal?: AbortSignal
}

function buildUrl(path: string, params: RequestOptions['params']): string {
  const url = `${API_BASE}${path}`
  if (!params) return url
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `${url}?${query}` : url
}

/**
 * Perform a request and parse the JSON response.
 *
 * The `T` is supplied by the caller from the generated types — this function
 * cannot verify the body matches at runtime, so callers must pass the type the
 * OpenAPI schema declares for that endpoint rather than inventing one.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, signal } = options
  const token = getToken()

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token !== null) headers['Authorization'] = `Bearer ${token}`

  const response = await fetch(buildUrl(path, params), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(signal ? { signal } : {}),
  })

  if (!response.ok) throw await toApiError(response)

  // 204 and other empty responses have no body to parse. Callers type these as
  // `void`, which is why the cast is confined to this one branch.
  if (response.status === 204 || response.headers.get('Content-Length') === '0') {
    return undefined as T
  }
  return (await response.json()) as T
}
