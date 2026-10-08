/**
 * The drawer, driven by a mocked SSE stream.
 *
 * `fetch` is stubbed with a real `ReadableStream` rather than a resolved string,
 * so the frame parser in `api.ts` is exercised the way the browser drives it —
 * including a chunk boundary that falls mid-frame, which is the case a naive
 * `split('\n\n')` on the whole body would never catch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatDrawer } from './ChatDrawer'

function streamOf(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder()
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

/** The `fetch` signature, so `mock.calls` is a typed tuple we can index into. */
type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

function frame(event: unknown): string {
  return `data: ${JSON.stringify(event)}\n\n`
}

const DONE = {
  type: 'done',
  usage: { input_tokens: 120, output_tokens: 18, cost_usd: 0.00042, latency_ms: 910 },
  prompt_version: 'v1',
  tool_calls: 1,
}

function renderDrawer(): void {
  // retry: false — a failing turn should surface its error, not be retried three
  // times behind the user's back.
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ChatDrawer />
    </QueryClientProvider>,
  )
}

async function openDrawer(): Promise<void> {
  await userEvent.click(screen.getByRole('button', { name: /open the operator's assistant/i }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatDrawer', () => {
  it('stays closed until the trigger is used', () => {
    renderDrawer()
    expect(screen.queryByLabelText('Message the assistant')).not.toBeInTheDocument()
  })

  it('shows the empty state with suggestions', async () => {
    renderDrawer()
    await openDrawer()
    expect(screen.getByText(/no questions yet/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /which devices am i monitoring/i }),
    ).toBeInTheDocument()
  })

  it('streams text deltas into the transcript', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          streamOf([
            frame({ type: 'text', delta: 'Pump-3 is ' }),
            frame({ type: 'text', delta: 'operating normally.' }),
            frame(DONE),
          ]),
        ),
      ),
    )
    renderDrawer()
    await openDrawer()

    await userEvent.type(screen.getByLabelText('Message the assistant'), 'how is Pump-3?')
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }))

    await waitFor(() =>
      expect(screen.getByText('Pump-3 is operating normally.')).toBeInTheDocument(),
    )
    // The question is echoed back as its own turn.
    expect(screen.getByText('how is Pump-3?')).toBeInTheDocument()
  })

  it('reassembles a frame split across chunk boundaries', async () => {
    const whole = frame({ type: 'text', delta: 'Furnace-1 peaked at 899C.' }) + frame(DONE)
    const cut = Math.floor(whole.length / 3)
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(streamOf([whole.slice(0, cut), whole.slice(cut)]))),
    )
    renderDrawer()
    await openDrawer()

    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))

    await waitFor(() => expect(screen.getByText('Furnace-1 peaked at 899C.')).toBeInTheDocument())
  })

  it('renders tool activity and fills in the summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          streamOf([
            frame({ type: 'tool_use', name: 'list_devices', input: {} }),
            frame({
              type: 'tool_result',
              name: 'list_devices',
              summary: 'Found 2 device(s): Pump-3, Furnace-1',
              truncated: false,
            }),
            frame({ type: 'text', delta: 'You have two devices.' }),
            frame(DONE),
          ]),
        ),
      ),
    )
    renderDrawer()
    await openDrawer()

    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))

    await waitFor(() => expect(screen.getByText('list_devices')).toBeInTheDocument())
    expect(screen.getByText(/found 2 device\(s\): Pump-3, Furnace-1/i)).toBeInTheDocument()
  })

  it('shows usage once the turn is done', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(streamOf([frame({ type: 'text', delta: 'Done.' }), frame(DONE)])),
      ),
    )
    renderDrawer()
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))

    // 120 + 18 tokens, $0.0004 at four decimals, 910ms.
    await waitFor(() => expect(screen.getByText(/138 tokens/)).toBeInTheDocument())
    expect(screen.getByText(/\$0\.0004/)).toBeInTheDocument()
    expect(screen.getByText(/910ms/)).toBeInTheDocument()
  })

  it('renders an agent error event on the turn instead of discarding it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          streamOf([
            frame({ type: 'error', code: 'rate_limited', message: 'Slow down and retry.' }),
            frame(DONE),
          ]),
        ),
      ),
    )
    renderDrawer()
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))

    await waitFor(() => expect(screen.getByText('Slow down and retry.')).toBeInTheDocument())
  })

  it('surfaces a 503 with a retry when the assistant is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              detail: 'The assistant is not configured.',
              code: 'assistant_unavailable',
            }),
            { status: 503, headers: { 'Content-Type': 'application/json' } },
          ),
        ),
      ),
    )
    renderDrawer()
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))

    await waitFor(() =>
      expect(screen.getByText('The assistant is not configured.')).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('sends the prior transcript so the assistant has context', async () => {
    const fetchMock = vi.fn<FetchLike>(() =>
      Promise.resolve(streamOf([frame({ type: 'text', delta: 'ok' }), frame(DONE)])),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderDrawer()
    await openDrawer()

    const input = screen.getByLabelText('Message the assistant')
    await userEvent.type(input, 'first question')
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('first question')).toBeInTheDocument())

    await userEvent.type(input, 'second question')
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const secondCall = fetchMock.mock.calls[1]
    expect(secondCall).toBeDefined()
    const init = secondCall?.[1] as RequestInit | undefined
    const sent = JSON.parse(String(init?.body)) as { messages: { content: string }[] }
    expect(sent.messages.map((message) => message.content)).toEqual([
      'first question',
      'ok',
      'second question',
    ])
  })

  it('does not resend a cleared transcript', async () => {
    const fetchMock = vi.fn<FetchLike>(() =>
      Promise.resolve(streamOf([frame({ type: 'text', delta: 'ok' }), frame(DONE)])),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderDrawer()
    await openDrawer()

    const input = screen.getByLabelText('Message the assistant')
    await userEvent.type(input, 'forget me')
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('forget me')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }))

    await userEvent.type(input, 'fresh start')
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    const init = fetchMock.mock.calls[1]?.[1] as RequestInit | undefined
    const sent = JSON.parse(String(init?.body)) as { messages: { content: string }[] }
    expect(sent.messages.map((message) => message.content)).toEqual(['fresh start'])
  })

  it('clears the transcript', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(streamOf([frame({ type: 'text', delta: 'Gone soon.' }), frame(DONE)])),
      ),
    )
    renderDrawer()
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: /which devices am i monitoring/i }))
    await waitFor(() => expect(screen.getByText('Gone soon.')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }))
    expect(screen.queryByText('Gone soon.')).not.toBeInTheDocument()
    expect(screen.getByText(/no questions yet/i)).toBeInTheDocument()
  })
})
