/**
 * The shell's route gating for the assistant drawer.
 *
 * SPEC § Frontend § Scope item 4 allows the chat panel on `/dashboard` and
 * `/devices/:id` only — notably *not* on the `/devices` list. That is a contract
 * worth a test rather than a comment, because nothing else fails if it drifts.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './AppShell'
import { AuthProvider } from '@/lib/auth'

function renderAt(path: string): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/dashboard" element={<p>dashboard</p>} />
              <Route path="/devices" element={<p>device list</p>} />
              <Route path="/devices/:deviceId" element={<p>device detail</p>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const TRIGGER = /open the operator's assistant/i

describe('AppShell assistant gating', () => {
  it('offers the assistant on the dashboard', () => {
    renderAt('/dashboard')
    expect(screen.getByRole('button', { name: TRIGGER })).toBeInTheDocument()
  })

  it('offers the assistant on a device detail page', () => {
    renderAt('/devices/3f1b9c54-0b3e-4a2d-9f77-2c5a1d8e4b10')
    expect(screen.getByRole('button', { name: TRIGGER })).toBeInTheDocument()
  })

  it('does not offer the assistant on the device list', () => {
    renderAt('/devices')
    expect(screen.getByText('device list')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRIGGER })).not.toBeInTheDocument()
  })
})
