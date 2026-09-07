import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/lib/auth'

/**
 * Gate for every authenticated route.
 *
 * Because the token lives in memory only, a page reload always lands here
 * unauthenticated — that is the documented cost of not persisting it. We send
 * the user to /login and remember where they were headed.
 */
export function ProtectedRoute(): JSX.Element {
  const { isAuthenticated } = useAuth()
  const location = useLocation()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}
