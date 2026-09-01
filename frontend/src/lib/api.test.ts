import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, setTokenGetter } from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  setTokenGetter(() => null)
})

describe('apiFetch', () => {
  it('attaches the bearer token from the getter', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    setTokenGetter(() => 'a-token')

    await apiFetch('/devices')

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer a-token')
  })

  it('sends no Authorization header when signed out', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/devices')

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers['Authorization']).toBeUndefined()
  })

  it('namespaces every request under /api', async () => {
    // Guards the fix for the hard-refresh bug: with bare paths, a document
    // request for /devices matched the dev proxy and returned the API's 401
    // JSON instead of the app. Client routes must never share a prefix with
    // API calls.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/devices')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/devices')
  })

  it('drops undefined query params instead of serializing them', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/readings', { params: { device_id: 'abc', start: undefined, limit: 10 } })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/readings?device_id=abc&limit=10')
  })

  it('parses the documented error body into an ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ detail: 'Device not found.', code: 'device_not_found' }, 404),
        ),
    )

    await expect(apiFetch('/devices/nope')).rejects.toMatchObject({
      status: 404,
      message: 'Device not found.',
      code: 'device_not_found',
    })
  })

  it('exposes the field name when the API blames one', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ detail: 'too short', code: 'validation_error', field: 'password' }, 422),
        ),
    )

    const error = await apiFetch('/auth/signup').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).field).toBe('password')
  })

  it('flags 401s so callers can stop retrying', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ detail: 'Not authenticated.', code: 'not_authenticated' }, 401),
        ),
    )

    const error = (await apiFetch('/auth/me').catch((caught: unknown) => caught)) as ApiError
    expect(error.isUnauthorized).toBe(true)
  })

  it('survives a non-JSON error body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>502</html>', { status: 502 })),
    )

    const error = (await apiFetch('/devices').catch((caught: unknown) => caught)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('unknown_error')
  })

  it('returns undefined for a 204 rather than trying to parse a body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))

    await expect(apiFetch<void>('/devices/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})
