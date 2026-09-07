/**
 * The three states SPEC requires on every list, table, and chart.
 *
 * They live here as components rather than as inline markup so "never leave
 * defaults" is a matter of importing the right thing instead of remembering to
 * hand-write an empty state for the twelfth time.
 */
import { AlertTriangle, RotateCw, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * Loading placeholder for tabular content.
 *
 * Skeletons, never a spinner: SPEC reserves spinners for button-scoped actions,
 * because a skeleton sized like the incoming rows keeps the layout from jumping
 * when data lands.
 */
export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-2 p-4" data-testid="table-skeleton">
      {Array.from({ length: rows }, (_, rowIndex) => (
        <div key={rowIndex} className="flex gap-4">
          {Array.from({ length: columns }, (_, columnIndex) => (
            <Skeleton key={columnIndex} className="h-6 flex-1" />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Loading placeholder for a chart, sized to the chart it replaces. */
export function ChartSkeleton({ className }: { className?: string }) {
  return <Skeleton className={cn('w-full', className ?? 'h-60')} data-testid="chart-skeleton" />
}

/**
 * Empty state: icon, one sentence, and a call to action when one makes sense.
 *
 * The CTA is optional because not every emptiness is actionable — "no alerts in
 * the last 24 hours" is good news, not a task.
 */
export function EmptyState({
  icon: Icon,
  message,
  action,
}: {
  icon: LucideIcon
  message: string
  action?: ReactNode
}) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center"
      data-testid="empty-state"
    >
      <Icon className="h-6 w-6 text-text-muted" aria-hidden />
      <p className="text-chrome text-text-secondary">{message}</p>
      {action}
    </div>
  )
}

/**
 * Error state with a retry affordance.
 *
 * Never `window.alert` and never an unstyled browser dialog — a failed fetch is
 * a normal condition in an ops UI and should read as recoverable.
 */
export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center"
      role="alert"
      data-testid="error-state"
    >
      <AlertTriangle className="h-6 w-6 text-alert" aria-hidden />
      <p className="text-chrome text-alert">{error.message}</p>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          <RotateCw className="h-4 w-4" aria-hidden />
          Retry
        </Button>
      ) : null}
    </div>
  )
}
