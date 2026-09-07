import { useAuth } from '@/lib/auth'
import { AuthForm } from './AuthForm'

export function SignupPage(): JSX.Element {
  const { signup } = useAuth()
  return (
    <AuthForm
      title="Create an account"
      description="Passwords must be at least 8 characters."
      submitLabel="Create account"
      onSubmit={signup}
      footer={{ prompt: 'Already registered?', linkLabel: 'Sign in', to: '/login' }}
    />
  )
}
