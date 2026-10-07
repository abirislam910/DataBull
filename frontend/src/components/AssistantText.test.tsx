/**
 * The assistant's text renderer.
 *
 * The inputs below are taken verbatim from recorded eval answers, so these test
 * the formatting the model actually produces rather than invented Markdown.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AssistantText } from './AssistantText'

describe('AssistantText', () => {
  it('renders bold spans as emphasis rather than literal asterisks', () => {
    render(<AssistantText content="You are monitoring **Furnace-1** and **Pump-3**." />)

    expect(screen.getByText('Furnace-1').tagName).toBe('STRONG')
    expect(screen.getByText('Pump-3').tagName).toBe('STRONG')
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument()
  })

  it('renders a bullet list from a real recorded answer', () => {
    render(
      <AssistantText
        content={
          'Hourly average pressure for Pump-3 (06:00–12:00 UTC):\n\n' +
          '- 06:00 – 5.66 bar\n' +
          '- 07:00 – 6.18 bar\n\n' +
          'All values are within the configured range.'
        }
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('06:00 – 5.66 bar')
    // The lead-in and the closing line stay as their own paragraphs.
    expect(screen.getByText(/hourly average pressure/i)).toBeInTheDocument()
    expect(screen.getByText(/within the configured range/i)).toBeInTheDocument()
  })

  it('renders a numbered list as an ordered list', () => {
    render(<AssistantText content={'1. Check Pump-3\n2. Check Furnace-2'} />)

    const list = screen.getByRole('list')
    expect(list.tagName).toBe('OL')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('applies bold inside list items', () => {
    render(<AssistantText content={'- **Furnace-1**: 899.96°C'} />)
    expect(screen.getByText('Furnace-1').tagName).toBe('STRONG')
  })

  it('leaves plain prose untouched', () => {
    render(<AssistantText content="Pump-3 is operating normally." />)
    expect(screen.getByText('Pump-3 is operating normally.')).toBeInTheDocument()
  })

  it('does not interpret markup it does not support', () => {
    // No italics, code spans, headings, links or tables appear in any recorded
    // answer, so they fall through as literal text rather than being parsed.
    render(<AssistantText content="A `code` span and a [link](http://example.com)." />)
    expect(screen.getByText(/`code` span/)).toBeInTheDocument()
    expect(screen.getByText(/\[link\]\(http:\/\/example\.com\)/)).toBeInTheDocument()
  })

  it('never renders model output as HTML', () => {
    // Device names are user-supplied and reach the assistant's prose through
    // tool results, so this is reachable, not theoretical: a device named with a
    // tag must appear as text and must not create an element.
    const { container } = render(
      <AssistantText content={'Device **<img src=x onerror="alert(1)">** reported 5 bar.'} />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument()
  })

  it('renders nothing for empty content without throwing', () => {
    const { container } = render(<AssistantText content="" />)
    expect(container.textContent).toBe('')
  })

  it('is not confused by a stray asterisk', () => {
    render(<AssistantText content="A 5 * 3 calculation and an unclosed **span." />)
    expect(screen.getByText(/A 5 \* 3 calculation and an unclosed \*\*span\./)).toBeInTheDocument()
  })
})
