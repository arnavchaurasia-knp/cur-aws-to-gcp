import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Login } from './pages/Login'
import { Upload } from './pages/Upload'
import { JobStatus } from './pages/JobStatus'
import { AdminJobs } from './pages/AdminJobs'
import { useEffect, useState } from 'react'
import { getMe } from './api/auth'
import type { UserInfo } from './api/auth'

export default function App() {
  const [user, setUser] = useState<UserInfo | null | undefined>(undefined)

  useEffect(() => { getMe().then(setUser) }, [])

  if (user === undefined) return null // loading

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" /> : <Login />} />
        <Route path="/" element={user ? <Upload user={user} /> : <Navigate to="/login" />} />
        <Route path="/jobs/:id" element={user ? <JobStatus user={user} /> : <Navigate to="/login" />} />
        <Route path="/admin" element={user?.is_admin ? <AdminJobs user={user} /> : <Navigate to="/" />} />
      </Routes>
    </BrowserRouter>
  )
}
