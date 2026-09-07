import { useAuth } from '@/lib/auth'
import { AuthForm } from './AuthForm'

export function LoginPage(): JSX.Element {
  const { login } = useAuth()
  return (
    <AuthForm
      title="Sign in"
      description="Access your devices and telemetry."
      submitLabel="Sign in"
      onSubmit={login}
      footer={{ prompt: 'No account yet?', linkLabel: 'Create one', to: '/signup' }}
    />
  )
}
