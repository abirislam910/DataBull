/**
 * Confirmation gate for irreversible actions.
 *
 * Composed from the shadcn Dialog primitive rather than hand-built, and
 * deliberately not `window.confirm`: SPEC forbids unstyled browser dialogs, and
 * a native confirm blocks the event loop and cannot show pending or error state.
 *
 * The confirm button is NOT autofocused. Focus lands on Cancel, so an operator
 * pressing Enter out of habit dismisses the dialog instead of destroying data.
 */
import { Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  onConfirm: () => void
  /** Disables both buttons and shows a spinner while the action is in flight. */
  isPending?: boolean
  /** Rendered inside the dialog so a failure is visible where the user is looking. */
  error?: Error | null
  /** `destructive` paints the confirm button in `alert`. */
  variant?: 'destructive' | 'default'
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  isPending = false,
  error = null,
  variant = 'destructive',
}: ConfirmDialogProps): JSX.Element {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Closing mid-flight would leave the user unsure whether it happened.
        if (isPending && !next) return
        onOpenChange(next)
      }}
    >
      <DialogContent
        // Keeps Enter from firing the destructive action by reflex.
        onOpenAutoFocus={(event) => {
          event.preventDefault()
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription asChild>
            <div>{description}</div>
          </DialogDescription>
        </DialogHeader>

        {error !== null ? (
          <p role="alert" className="mt-4 text-cell text-alert">
            {error.message}
          </p>
        ) : null}

        <DialogFooter className="mt-6">
          <DialogClose asChild>
            <Button variant="secondary" disabled={isPending} autoFocus>
              Cancel
            </Button>
          </DialogClose>
          <Button
            variant={variant === 'destructive' ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={isPending}
          >
            {isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
