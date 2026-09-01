/**
 * SPEC requires loading, empty, and error states on every list, table, and
 * chart. These tests pin the contract those components promise.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HardDrive } from 'lucide-react'
import { describe, expect, it, vi } from 'vitest'
import { ChartSkeleton, EmptyState, ErrorState, TableSkeleton } from './DataStates'

describe('TableSkeleton', () => {
  it('renders placeholders rather than a spinner', () => {
    render(<TableSkeleton rows={3} columns={2} />)
    expect(screen.getByTestId('table-skeleton')).toBeInTheDocument()
  })
})

describe('ChartSkeleton', () => {
  it('renders at the height it is given', () => {
    render(<ChartSkeleton className="h-[400px]" />)
    expect(screen.getByTestId('chart-skeleton')).toHaveClass('h-[400px]')
  })
})

describe('EmptyState', () => {
  it('explains the emptiness', () => {
    render(<EmptyState icon={HardDrive} message="No devices yet." />)
    expect(screen.getByText('No devices yet.')).toBeInTheDocument()
  })

  it('renders a call to action when one is supplied', () => {
    render(
      <EmptyState
        icon={HardDrive}
        message="No devices yet."
        action={<button>Add a device</button>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Add a device' })).toBeInTheDocument()
  })

  it('omits the CTA when the emptiness is not actionable', () => {
    render(<EmptyState icon={HardDrive} message="No alerts in the last 24 hours." />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('announces itself to assistive tech and shows the message', () => {
    render(<ErrorState error={new Error('Device not found.')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Device not found.')
  })

  it('retries when asked', async () => {
    const onRetry = vi.fn()
    render(<ErrorState error={new Error('boom')} onRetry={onRetry} />)

    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('has no retry button when retrying is not possible', () => {
    render(<ErrorState error={new Error('boom')} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
