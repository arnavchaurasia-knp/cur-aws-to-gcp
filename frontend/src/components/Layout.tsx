import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from './ThemeToggle'
import { UserMenu } from './Nav'
import { NotificationDropdown } from './NotificationDropdown'
import type { UserInfo } from '../api/auth'

interface LayoutProps {
  user: UserInfo
  children: React.ReactNode
}

export function Layout({ user, children }: LayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  // Redirect unauthenticated users
  if (!user) return <Navigate to="/login" replace />

  // Only show menu bar (Sidebar) for admins
  const isAdmin = Boolean(user?.is_admin)

  return (
    <div className="min-h-screen bg-[var(--fcp-bg-base)] text-[var(--fcp-text-primary)] transition-colors duration-200">
      {/* Top Navbar */}
      <header
        className={`fixed top-0 right-0 h-16 z-30 border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-base)]/90 backdrop-blur-md transition-all duration-300 ${
          !isAdmin ? 'left-0' : sidebarCollapsed ? 'left-16' : 'left-64'
        }`}
      >
        <div className="flex items-center justify-between px-6 h-full">
          {/* Left: AWS → GCP Cost Projection */}
          <div className="flex items-center">
            <span className="text-sm font-semibold tracking-wide">
              AWS → GCP Cost Projection
            </span>
          </div>

          {/* Right: Notification Dropdown (for admins) + Theme Toggle + User Dropdown */}
          <div className="flex items-center gap-2.5">
            {/* Admin Interactive Notification Bell with Dropdown */}
            {isAdmin && <NotificationDropdown />}

            {/* Round Favicon Theme Toggle */}
            <ThemeToggle />

            {/* Separator */}
            <div className="h-5 w-px bg-white/10" />

            {/* Round User Dropdown Thing */}
            <UserMenu user={user} />
          </div>
        </div>
      </header>

      {/* Main content layout */}
      <div className={`flex transition-all duration-300 ${!isAdmin ? 'pl-0' : sidebarCollapsed ? 'pl-16' : 'pl-64'}`}>
        {/* Sidebar matching Facets Control Plane specifications (Admins only) */}
        {isAdmin && (
          <Sidebar
            user={user}
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
          />
        )}

        {/* Page Content: occupies full page smoothly */}
        <main className="flex-1 pt-16 min-h-screen overflow-x-hidden">
          {children}
        </main>
      </div>
    </div>
  )
}

export default Layout
