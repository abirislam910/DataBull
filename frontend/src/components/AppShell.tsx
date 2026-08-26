import { Activity, HardDrive, LogOut, ZodiacTaurus } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: Activity },
  { to: '/devices', label: 'Devices', icon: HardDrive },
] as const

/** Persistent chrome: brand, primary nav, and the signed-in identity. */
export function AppShell(): JSX.Element {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-bg">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-8 px-6">
          <NavLink to='/dashboard' className='flex items-center gap-2 rounded-md px-3 py-2 text-chrome transition-colors text-text-secondary hover:bg-surface-hover hover:text-text'>
            <span className="flex items-center gap-2 font-semibold text-text">
                <ZodiacTaurus className="h-5 w-5 text-accent" aria-hidden />
                DataBull
            </span>
          </NavLink>

          <nav className="flex items-center gap-1" aria-label="Main">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2 rounded-md px-3 py-2 text-chrome transition-colors',
                    isActive
                      ? 'bg-surface-hover text-text'
                      : 'text-text-secondary hover:bg-surface-hover hover:text-text',
                  )
                }
              >
                <Icon className="h-4 w-4" aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {user ? <span className="font-mono text-cell text-text-muted">{user.email}</span> : null}
            <Button variant="ghost" size="sm" onClick={logout}>
              <LogOut className="h-4 w-4" aria-hidden />
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
