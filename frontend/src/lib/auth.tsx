/**
 * Auth state: the access token and the user it belongs to.
 *
 * THE TOKEN LIVES IN MEMORY ONLY — React state, nothing more. It is never
 * written to `localStorage`, `sessionStorage`, or `document.cookie`. Anything
 * readable from JavaScript is exfiltratable by an XSS payload; a value held in
 * a closure is not. The cost is real and accepted: a tab reload loses the
 * session and the user signs in again. See README § Documented tradeoffs.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { apiFetch, setTokenGetter } from './api'
import type { Credentials, TokenResponse, User } from './types'

interface AuthContextValue {
  user: User | null
  isAuthenticated: boolean
  /** True while a stored token is being exchanged for the current user. */
  isLoading: boolean
  login: (credentials: Credentials) => Promise<void>
  signup: (credentials: Credentials) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  // The token is a ref, not state: `api.ts` reads it through a getter at call
  // time, and re-rendering on every token change would buy nothing. `user` is
  // state because the UI does render from it.
  const tokenRef = useRef<string | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  // Registered once so every apiFetch call can read the live token without the
  // module holding a copy of its own.
  useMemo(() => setTokenGetter(() => tokenRef.current), [])

  const authenticate = useCallback(
    async (path: '/auth/login' | '/auth/signup', credentials: Credentials) => {
      setIsLoading(true)
      try {
        const token = await apiFetch<TokenResponse>(path, {
          method: 'POST',
          body: credentials,
        })
        tokenRef.current = token.access_token
        // Fetch the profile immediately: it confirms the token works and gives
        // the UI a name to show, in one round-trip we would make anyway.
        setUser(await apiFetch<User>('/auth/me'))
      } catch (error) {
        tokenRef.current = null
        setUser(null)
        throw error
      } finally {
        setIsLoading(false)
      }
    },
    [],
  )

  const login = useCallback(
    (credentials: Credentials) => authenticate('/auth/login', credentials),
    [authenticate],
  )

  const signup = useCallback(
    (credentials: Credentials) => authenticate('/auth/signup', credentials),
    [authenticate],
  )

  const logout = useCallback(() => {
    // Purely client-side: the JWT is stateless, so there is no server session
    // to end. The token stays technically valid until it expires — dropping our
    // only copy is exactly what "logging out" means here. README documents this.
    tokenRef.current = null
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, isAuthenticated: user !== null, isLoading, login, signup, logout }),
    [user, isLoading, login, signup, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside an <AuthProvider>')
  }
  return context
}
