import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Login } from './pages/Login'
import { Upload } from './pages/Upload'
import { JobStatus } from './pages/JobStatus'
import { AdminJobs } from './pages/AdminJobs'
import { AdminUsers } from './pages/AdminUsers'
import { AdminCatalog } from './pages/AdminCatalog'
import { AdminAssumptions } from './pages/AdminAssumptions'
import { AdminReportSettings } from './pages/AdminReportSettings'
import { AdminAudit } from './pages/AdminAudit'
import { AdminAnalytics } from './pages/AdminAnalytics'
import { SharedReport } from './pages/SharedReport'
import { Facets } from './pages/Facets'
import { useEffect, useState } from 'react'
import { getMe } from './api/auth'
import type { UserInfo } from './api/auth'
import { Toaster } from 'sonner'

export default function App() {
  const [user, setUser] = useState<UserInfo | null | undefined>(undefined)

  useEffect(() => { getMe().then(setUser).catch(() => setUser(null)) }, [])

  // /reports/:token is a public, unauthenticated page — don't block it behind
  // the getMe() loading spinner or force a login redirect.
  if (window.location.pathname.startsWith('/reports/')) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/reports/:token" element={<SharedReport />} />
        </Routes>
      </BrowserRouter>
    )
  }

  if (user === undefined) return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0a0a0f' }}>
      <div style={{ width: 32, height: 32, border: '3px solid #645DF6', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  )

  const email = (user?.email || '').toLowerCase()
  const isFacetsUser = Boolean(
    email && (
      email.endsWith('@facets.cloud') ||
      email.endsWith('@facets.io') ||
      email.includes('@facets.')
    )
  )

  return (
    <BrowserRouter>
      <Toaster position="top-center" richColors closeButton theme="dark" duration={3500} toastOptions={{ duration: 3500 }} visibleToasts={3} />
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" /> : <Login />} />
        <Route path="/" element={user ? <Upload user={user} /> : <Navigate to="/login" />} />
        <Route path="/jobs/:id" element={user ? <JobStatus user={user} /> : <Navigate to="/login" />} />
        <Route path="/admin" element={user && isFacetsUser ? <AdminJobs user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/users" element={user && isFacetsUser ? <AdminUsers user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/catalog" element={user && isFacetsUser ? <AdminCatalog user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/assumptions" element={user && isFacetsUser ? <AdminAssumptions user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/report-settings" element={user && isFacetsUser ? <AdminReportSettings user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/audit" element={user && isFacetsUser ? <AdminAudit user={user} /> : <Navigate to="/" />} />
        <Route path="/admin/analytics" element={user && isFacetsUser ? <AdminAnalytics user={user} /> : <Navigate to="/" />} />
        <Route path="/reports/:token" element={<SharedReport />} />
        {/* Visible and accessible only for @facets sessions */}
        <Route path="/facets" element={user && isFacetsUser ? <Facets user={user} /> : <Navigate to="/" />} />
        <Route path="*" element={<Navigate to={user ? "/" : "/login"} replace />} />
      </Routes>
    </BrowserRouter>
  )
}
