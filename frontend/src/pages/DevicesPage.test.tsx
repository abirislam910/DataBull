/**
 * The load-bearing assertion here is that clicking the trash icon deletes
 * nothing — a DELETE must only leave the browser after an explicit confirm.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '@/test/utils'
import { DevicesPage } from './DevicesPage'

const DEVICE = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'Pump-3',
  type: 'flow',
  unit: 'L/min',
  min_threshold: null,
  max_threshold: null,
  created_at: '2026-03-01T12:00:00Z',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Requests the component made, so tests can assert on what did NOT happen. */
let calls: Array<{ url: string; method: string }>

function stubApi(deleteResponse: () => Response = () => new Response(null, { status: 204 })): void {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (method === 'DELETE') return Promise.resolve(deleteResponse())
      return Promise.resolve(jsonResponse([DEVICE]))
    }),
  )
}

const deleteCalls = (): Array<{ url: string; method: string }> =>
  calls.filter((call) => call.method === 'DELETE')

beforeEach(() => stubApi())
afterEach(() => vi.unstubAllGlobals())

async function openDeleteDialog(): Promise<void> {
  await screen.findByText('Pump-3')
  await userEvent.click(screen.getByRole('button', { name: 'Delete Pump-3' }))
}

describe('deleting a device', () => {
  it('asks for confirmation instead of deleting immediately', async () => {
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(deleteCalls()).toHaveLength(0)
  })

  it('names the device and warns that readings go too', async () => {
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Pump-3')).toBeInTheDocument()
    expect(within(dialog).getByText(/every reading recorded for it/i)).toBeInTheDocument()
  })

  it('deletes nothing when cancelled', async () => {
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(deleteCalls()).toHaveLength(0)
  })

  it('deletes nothing when dismissed with Escape', async () => {
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(deleteCalls()).toHaveLength(0)
  })

  it('sends the DELETE only after an explicit confirm', async () => {
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    await userEvent.click(screen.getByRole('button', { name: 'Delete device' }))

    await waitFor(() => expect(deleteCalls()).toHaveLength(1))
    expect(deleteCalls()[0]?.url).toBe(`/api/devices/${DEVICE.id}`)
  })

  it('does not put focus on the destructive button', async () => {
    // Enter pressed by reflex should not destroy data, so Cancel holds focus.
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus())
  })

  it('keeps the dialog open and shows the reason when the delete fails', async () => {
    stubApi(() => jsonResponse({ detail: 'Device not found.', code: 'device_not_found' }, 404))
    renderWithProviders(<DevicesPage />)
    await openDeleteDialog()

    await userEvent.click(screen.getByRole('button', { name: 'Delete device' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Device not found.')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
