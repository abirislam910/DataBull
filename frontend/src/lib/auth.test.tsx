/**
 * The security-relevant assertion in this file is the last one: the token must
 * never reach localStorage, sessionStorage, or document.cookie.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './auth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function Probe(): JSX.Element {
  const { user, isAuthenticated, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="state">{isAuthenticated ? 'in' : 'out'}</span>
      <span data-testid="email">{user?.email ?? ''}</span>
      {/* Swallows the rejection the way the real form does — it catches and
          renders the error rather than letting it escape. */}
      <button
        onClick={() => {
          login({ email: 'op@example.com', password: 'a-good-password' }).catch(() => {})
        }}
      >
        login
      </button>
      <button onClick={logout}>logout</button>
    </div>
  )
}

function stubSuccessfulLogin(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string) => {
      if (input === '/api/auth/login') {
        return Promise.resolve(jsonResponse({ access_token: 'tok-123', token_type: 'bearer' }))
      }
      return Promise.resolve(
        jsonResponse({ id: 'u1', email: 'op@example.com', created_at: '2026-01-01T00:00:00Z' }),
      )
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('AuthProvider', () => {
  it('starts signed out', () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('state')).toHaveTextContent('out')
  })

  it('signs in and loads the profile', async () => {
    stubSuccessfulLogin()
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('in'))
    expect(screen.getByTestId('email')).toHaveTextContent('op@example.com')
  })

  it('clears the session on logout', async () => {
    stubSuccessfulLogin()
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('in'))

    await userEvent.click(screen.getByText('logout'))
    expect(screen.getByTestId('state')).toHaveTextContent('out')
  })

  it('stays signed out when the credentials are rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ detail: 'Incorrect email or password.', code: 'invalid_credentials' }, 401),
        ),
    )
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('out'))
    expect(screen.getByTestId('email')).toHaveTextContent('')
  })

  it('never persists the token outside memory', async () => {
    stubSuccessfulLogin()
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('in'))

    // The whole point of the in-memory design: an XSS payload reading these
    // stores must come away with nothing.
    expect(JSON.stringify(localStorage)).not.toContain('tok-123')
    expect(JSON.stringify(sessionStorage)).not.toContain('tok-123')
    expect(document.cookie).not.toContain('tok-123')
  })
})
