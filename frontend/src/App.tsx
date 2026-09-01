import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { AuthProvider } from '@/lib/auth'
import { DashboardPage } from '@/pages/DashboardPage'
import { DeviceDetailPage } from '@/pages/DeviceDetailPage'
import { DevicesPage } from '@/pages/DevicesPage'
import { LoginPage } from '@/pages/LoginPage'
import { SignupPage } from '@/pages/SignupPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Telemetry ages fast; a stale window longer than the poll interval would
      // just serve data the poll already replaced.
      staleTime: 5_000,
      refetchOnWindowFocus: true,
    },
  },
})

export function App(): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        {/* AuthProvider sits inside the Router so auth screens can navigate,
            and outside the routes so the token survives navigation. */}
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />

            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/devices" element={<DevicesPage />} />
                <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </AuthProvider>
      </Router>
    </QueryClientProvider>
  )
}
