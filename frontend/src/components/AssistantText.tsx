/**
 * Renders the small slice of Markdown the assistant actually emits.
 *
 * Not a Markdown library, and deliberately not a general parser. Scanning the 30
 * recorded eval answers, the model reaches for exactly three constructs — bold
 * spans, `-` bullet lists, and `1.` numbered lists. No italics, code spans,
 * headings, tables, or links appear at all. Supporting more than that would be
 * speculative surface area.
 *
 * **Returns React elements, never an HTML string.** There is no
 * `dangerouslySetInnerHTML` here, and there must not be: device names are
 * user-supplied and travel into the assistant's prose through tool results, so a
 * device called `<img src=x onerror=…>` would be a stored XSS the moment this
 * rendered HTML. React escapes text children, which closes that off structurally
 * rather than by sanitizing.
 *
 * Anything unrecognised falls through as literal text, so a construct this does
 * not know about degrades to what the drawer showed before — never to an error.
 */
import { type ReactNode } from 'react'

const BULLET = /^\s*[-*]\s+(.*)$/
const NUMBERED = /^\s*\d+\.\s+(.*)$/
const BOLD = /\*\*([^*\n]+)\*\*/g

type Block =
  | { kind: 'paragraph'; lines: string[] }
  | { kind: 'bullets'; items: string[] }
  | { kind: 'numbers'; items: string[] }

/** Split `**bold**` runs into elements, leaving everything else as text. */
function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  let cursor = 0
  let key = 0

  // `exec` in a loop needs the regex reset, since BOLD is module-level and
  // carries `lastIndex` between calls.
  BOLD.lastIndex = 0
  for (let match = BOLD.exec(text); match !== null; match = BOLD.exec(text)) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index))
    parts.push(
      <strong key={`b${key}`} className="font-semibold text-text">
        {match[1] ?? ''}
      </strong>,
    )
    key += 1
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return parts
}

function toBlocks(content: string): Block[] {
  const blocks: Block[] = []

  for (const line of content.split('\n')) {
    const bullet = BULLET.exec(line)
    const numbered = NUMBERED.exec(line)
    const last = blocks.at(-1)

    if (bullet !== null) {
      const item = bullet[1] ?? ''
      if (last?.kind === 'bullets') last.items.push(item)
      else blocks.push({ kind: 'bullets', items: [item] })
      continue
    }

    if (numbered !== null) {
      const item = numbered[1] ?? ''
      if (last?.kind === 'numbers') last.items.push(item)
      else blocks.push({ kind: 'numbers', items: [item] })
      continue
    }

    if (line.trim() === '') {
      // A blank line closes whatever block was open; it is not content itself.
      if (last !== undefined) blocks.push({ kind: 'paragraph', lines: [] })
      continue
    }

    if (last?.kind === 'paragraph') last.lines.push(line)
    else blocks.push({ kind: 'paragraph', lines: [line] })
  }

  return blocks.filter((block) => block.kind !== 'paragraph' || block.lines.length > 0)
}

export function AssistantText({ content }: { content: string }): JSX.Element {
  const blocks = toBlocks(content)

  return (
    <div className="space-y-2 text-chrome text-text">
      {blocks.map((block, index) => {
        if (block.kind === 'bullets') {
          return (
            <ul key={index} className="list-disc space-y-1 pl-5">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{inline(item)}</li>
              ))}
            </ul>
          )
        }
        if (block.kind === 'numbers') {
          return (
            <ol key={index} className="list-decimal space-y-1 pl-5">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{inline(item)}</li>
              ))}
            </ol>
          )
        }
        return (
          <p key={index} className="whitespace-pre-wrap">
            {inline(block.lines.join('\n'))}
          </p>
        )
      })}
    </div>
  )
}
