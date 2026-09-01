import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        'flex h-10 w-full rounded-md border border-border bg-bg px-3 py-2 text-chrome text-text transition-colors',
        'placeholder:text-text-muted focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50',
        'aria-[invalid=true]:border-alert',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

export { Input }
