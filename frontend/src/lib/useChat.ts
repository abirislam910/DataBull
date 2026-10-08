/**
 * Transcript state for the assistant drawer.
 *
 * Why not `useQuery`: a chat turn is not server state to be cached and
 * refetched, it is an imperative action that streams a result once. `useMutation`
 * is the TanStack primitive for that, and it keeps the CLAUDE.md rule intact —
 * no `useEffect` is doing any fetching here.
 *
 * The assistant message is appended *before* the stream starts and mutated in
 * place as deltas arrive, so the UI can render a partial answer rather than
 * waiting for `done`.
 */
import { useMutation } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { ApiError, apiStream } from './api'
import type { AgentEvent, ChatMessageIn, ChatUsage } from './types'
import { notifyUnauthorized } from './auth'

/** One tool invocation, as the drawer shows it. */
export interface ToolActivity {
  name: string
  summary: string | null
  truncated: boolean
}

export interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  tools: ToolActivity[]
  /** Set on the assistant turn once `done` arrives. */
  usage: ChatUsage | null
  /** A failure the agent absorbed, shown inline on the turn it belongs to. */
  error: string | null
}

/** Transcript turns the API will accept — tool activity and usage are ours. */
function toRequestMessages(turns: ChatTurn[]): ChatMessageIn[] {
  return turns
    .filter((turn) => turn.content.trim() !== '')
    .map((turn) => ({ role: turn.role, content: turn.content }))
}

let counter = 0
function nextId(): string {
  counter += 1
  return `turn-${counter}`
}

export interface UseChatResult {
  turns: ChatTurn[]
  /** True from submit until the stream ends. */
  isStreaming: boolean
  /** A transport-level failure, as opposed to an `error` event inside a turn. */
  transportError: Error | null
  send: (prompt: string) => void
  stop: () => void
  reset: () => void
}

export function useChat(): UseChatResult {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const abortRef = useRef<AbortController | null>(null)

  // The transcript is mirrored in a ref because `mutationFn` needs to read the
  // history *synchronously* when building a request. Reading it from inside a
  // `setTurns` updater does not work: updaters run asynchronously relative to the
  // code that follows them, and React 18 StrictMode invokes them twice. The
  // symptom was a second turn that carried no prior context at all.
  const turnsRef = useRef<ChatTurn[]>([])

  const update = useCallback((fn: (current: ChatTurn[]) => ChatTurn[]) => {
    const next = fn(turnsRef.current)
    turnsRef.current = next
    setTurns(next)
  }, [])

  /** Apply one event to the assistant turn currently being built. */
  const applyEvent = useCallback(
    (assistantId: string, event: AgentEvent) => {
      update((current) =>
        current.map((turn) => {
          if (turn.id !== assistantId) return turn
          switch (event.type) {
            case 'text':
              return { ...turn, content: turn.content + event.delta }
            case 'tool_use':
              return {
                ...turn,
                tools: [...turn.tools, { name: event.name, summary: null, truncated: false }],
              }
            case 'tool_result': {
              // Fill in the most recent pending call of this name. The runner emits
              // tool_use then tool_result per call, but parallel calls interleave,
              // so match by name rather than assuming the last entry is ours.
              // Scanned by hand because `findLastIndex` needs lib ES2023 and the
              // project targets ES2022.
              let index = -1
              for (let i = turn.tools.length - 1; i >= 0; i -= 1) {
                const candidate = turn.tools[i]
                if (
                  candidate !== undefined &&
                  candidate.name === event.name &&
                  candidate.summary === null
                ) {
                  index = i
                  break
                }
              }
              if (index === -1) return turn
              const tools = [...turn.tools]
              tools[index] = {
                name: event.name,
                summary: event.summary,
                truncated: event.truncated,
              }
              return { ...turn, tools }
            }
            case 'error':
              return { ...turn, error: event.message }
            case 'done':
              return { ...turn, usage: event.usage }
          }
        }),
      )
    },
    [update],
  )

  const mutation = useMutation<void, Error, string>({
    mutationFn: async (prompt: string) => {
      const controller = new AbortController()
      abortRef.current = controller

      const userTurn: ChatTurn = {
        id: nextId(),
        role: 'user',
        content: prompt,
        tools: [],
        usage: null,
        error: null,
      }
      const assistantTurn: ChatTurn = {
        id: nextId(),
        role: 'assistant',
        content: '',
        tools: [],
        usage: null,
        error: null,
      }

      // Read the history from the ref before appending, so the request carries
      // the turns that preceded this one and not the empty assistant turn about
      // to be filled in.
      const history = toRequestMessages(turnsRef.current)
      update((current) => [...current, userTurn, assistantTurn])

      try {
        const stream = apiStream<AgentEvent>('/chat/stream', {
          body: { messages: [...history, { role: 'user', content: prompt }] },
          signal: controller.signal,
        })
        for await (const event of stream) {
          applyEvent(assistantTurn.id, event)
        }
      } catch (error) {
        if (controller.signal.aborted) return
        // A 401 here means the same thing it means anywhere else: the session is
        // over. Routing it through the shared handler signs the user out rather
        // than leaving a dead drawer open.
        if (error instanceof ApiError && error.isUnauthorized) notifyUnauthorized()
        throw error
      } finally {
        abortRef.current = null
      }
    },
  })

  const send = useCallback(
    (prompt: string) => {
      const trimmed = prompt.trim()
      if (trimmed === '' || mutation.isPending) return
      mutation.mutate(trimmed)
    },
    [mutation],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    mutation.reset()
    // Through `update`, so the ref is cleared alongside the state — otherwise the
    // next message would still carry the transcript the user just discarded.
    update(() => [])
  }, [mutation, update])

  return {
    turns,
    isStreaming: mutation.isPending,
    transportError: mutation.error,
    send,
    stop,
    reset,
  }
}
