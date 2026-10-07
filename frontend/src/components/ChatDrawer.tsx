/**
 * The operator's assistant, as a persistent right-side drawer.
 *
 * SPEC § Frontend § Scope item 4: available on `/dashboard` and `/devices/:id`,
 * never a route of its own. Mounted by `AppShell` so the transcript survives
 * navigation between those two pages.
 */
import {
  AlertTriangle,
  Loader2,
  MessageSquare,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  Wrench,
} from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { AssistantText } from '@/components/AssistantText'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useChat, type ChatTurn, type ToolActivity } from '@/lib/useChat'
import { cn } from '@/lib/utils'

/** Starting points for an empty transcript — the SPEC-required empty-state CTA. */
const SUGGESTIONS = [
  'Which devices am I monitoring?',
  'Any threshold breaches in the last 24 hours?',
  'What is the average on my pressure sensors today?',
] as const

function ToolRow({ tool }: { tool: ToolActivity }): JSX.Element {
  const pending = tool.summary === null
  return (
    <div className="flex items-start gap-2 rounded-sm bg-bg px-2 py-1.5">
      {pending ? (
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-text-muted" aria-hidden />
      ) : (
        <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-text-muted" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <span className="font-mono text-cell text-text-secondary">{tool.name}</span>
        {tool.summary !== null ? (
          <p className="text-cell text-text-muted">
            {tool.summary}
            {tool.truncated ? ' (truncated)' : ''}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function Turn({ turn, isStreaming }: { turn: ChatTurn; isStreaming: boolean }): JSX.Element {
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-md bg-surface-hover px-3 py-2 text-chrome text-text">
          {turn.content}
        </p>
      </div>
    )
  }

  // An assistant turn with no text and no tools yet is the gap between submit and
  // the first delta — the only moment the drawer has nothing to show.
  const awaitingFirstToken =
    isStreaming && turn.content === '' && turn.tools.length === 0 && turn.error === null

  return (
    <div className="space-y-2">
      {turn.tools.length > 0 ? (
        <div className="space-y-1">
          {turn.tools.map((tool, index) => (
            <ToolRow key={`${tool.name}-${index}`} tool={tool} />
          ))}
        </div>
      ) : null}

      {awaitingFirstToken ? (
        <div className="flex items-center gap-2 text-chrome text-text-muted">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Thinking…
        </div>
      ) : null}

      {turn.content !== '' ? <AssistantText content={turn.content} /> : null}

      {turn.error !== null ? (
        <p className="flex items-start gap-2 rounded-md border border-alert px-3 py-2 text-cell text-alert">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {turn.error}
        </p>
      ) : null}

      {turn.usage !== null ? (
        <p className="font-mono text-cell text-text-muted">
          {turn.usage.input_tokens + turn.usage.output_tokens} tokens · $
          {turn.usage.cost_usd.toFixed(4)} · {turn.usage.latency_ms}ms
        </p>
      ) : null}
    </div>
  )
}

export function ChatDrawer(): JSX.Element {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const { turns, isStreaming, transportError, send, stop, reset } = useChat()
  const endRef = useRef<HTMLDivElement | null>(null)

  // A DOM side effect, not data fetching: keep the newest text in view as deltas
  // land. CLAUDE.md's ban on `useEffect` is about fetching, which happens in
  // `useChat` via `useMutation`.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns])

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (draft.trim() === '' || isStreaming) return
    send(draft)
    setDraft('')
  }

  const lastUserPrompt = [...turns].reverse().find((turn) => turn.role === 'user')?.content

  return (
    <Sheet open={open} onOpenChange={setOpen} modal={false}>
      <SheetTrigger asChild>
        <Button
          variant="secondary"
          size="sm"
          className="fixed bottom-6 right-6 z-40 shadow-lg"
          aria-label="Open the operator's assistant"
        >
          <Sparkles className="h-4 w-4 text-accent" aria-hidden />
          Assistant
        </Button>
      </SheetTrigger>

      <SheetContent aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle>Assistant</SheetTitle>
          <SheetDescription>
            Ask about your fleet. Answers come only from your own readings.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {turns.length === 0 ? (
            <div className="flex flex-col items-center gap-4 py-8 text-center">
              <MessageSquare className="h-6 w-6 text-text-muted" aria-hidden />
              <p className="text-chrome text-text-secondary">
                No questions yet. Try one of these to start.
              </p>
              <div className="flex w-full flex-col gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <Button
                    key={suggestion}
                    variant="secondary"
                    size="sm"
                    className="h-auto whitespace-normal py-2 text-left"
                    onClick={() => send(suggestion)}
                  >
                    {suggestion}
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            turns.map((turn) => <Turn key={turn.id} turn={turn} isStreaming={isStreaming} />)
          )}

          {/* Transport failures (503 with no API key, a dropped connection) are
              separate from an `error` event the agent absorbed — those render on
              the turn itself. */}
          {transportError !== null ? (
            <div className="space-y-2 rounded-md border border-alert p-3">
              <p className="flex items-start gap-2 text-cell text-alert">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                {transportError.message}
              </p>
              {lastUserPrompt !== undefined ? (
                <Button variant="secondary" size="sm" onClick={() => send(lastUserPrompt)}>
                  <RotateCcw className="h-4 w-4" aria-hidden />
                  Retry
                </Button>
              ) : null}
            </div>
          ) : null}

          <div ref={endRef} />
        </div>

        <SheetFooter>
          <form onSubmit={onSubmit} className="space-y-2">
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask about a device…"
              aria-label="Message the assistant"
              disabled={isStreaming}
            />
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" disabled={isStreaming || draft.trim() === ''}>
                {isStreaming ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Send className="h-4 w-4" aria-hidden />
                )}
                Send
              </Button>
              {isStreaming ? (
                <Button type="button" variant="ghost" size="sm" onClick={stop}>
                  <Square className="h-4 w-4" aria-hidden />
                  Stop
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={cn('ml-auto', turns.length === 0 && 'invisible')}
                onClick={reset}
              >
                Clear
              </Button>
            </div>
          </form>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
