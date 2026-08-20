/**
 * The shared body of /login and /signup.
 *
 * The two pages differ only in wording and which auth call they make, so the
 * form lives here once. Errors are read from the API's `{detail, code, field}`
 * body: when the backend names a field, the message is attached to that input
 * rather than dumped in a generic banner.
 */
import { Activity, Loader2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError } from '@/lib/api'
import type { Credentials } from '@/lib/types'

export interface AuthFormProps {
  title: string
  description: string
  submitLabel: string
  onSubmit: (credentials: Credentials) => Promise<void>
  footer: { prompt: string; linkLabel: string; to: string }
}

export function AuthForm({
  title,
  description,
  submitLabel,
  onSubmit,
  footer,
}: AuthFormProps): JSX.Element {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // A field-scoped message when the API named a field, so the user sees the
  // problem next to the input that caused it.
  const fieldError = (field: string): string | null =>
    error !== null && error.field === field ? error.message : null
  const generalError = error !== null && error.field === undefined ? error.message : null

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await onSubmit({ email, password })
      navigate('/dashboard', { replace: true })
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError(0, 'Could not reach the server. Is the API running?', 'network_error'),
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <span className="flex items-center gap-2 text-accent">
            <Activity className="h-5 w-5" aria-hidden />
            <span className="text-chrome font-medium">Telemetry</span>
          </span>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>

        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                aria-invalid={fieldError('email') !== null}
                aria-describedby={fieldError('email') !== null ? 'email-error' : undefined}
              />
              {fieldError('email') !== null ? (
                <p id="email-error" className="text-cell text-alert">
                  {fieldError('email')}
                </p>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={submitLabel === 'Sign in' ? 'current-password' : 'new-password'}
                required
                minLength={8}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={fieldError('password') !== null}
                aria-describedby={fieldError('password') !== null ? 'password-error' : undefined}
              />
              {fieldError('password') !== null ? (
                <p id="password-error" className="text-cell text-alert">
                  {fieldError('password')}
                </p>
              ) : null}
            </div>

            {generalError !== null ? (
              <p role="alert" className="text-cell text-alert">
                {generalError}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {/* Spinners are permitted here: this is a button-scoped action. */}
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
              {submitLabel}
            </Button>
          </form>

          <p className="mt-6 text-center text-chrome text-text-secondary">
            {footer.prompt}{' '}
            <Link to={footer.to} className="text-accent underline-offset-4 hover:underline">
              {footer.linkLabel}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
