import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/** Loading placeholder. Sized by the caller to match the content it stands in for. */
function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return <div className={cn('animate-pulse rounded-md bg-surface-hover', className)} {...props} />
}

export { Skeleton }
