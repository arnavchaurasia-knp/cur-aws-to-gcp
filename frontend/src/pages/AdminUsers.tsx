import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Nav } from '../components/Nav'
import { listUsers, setUserRole } from '../api/admin'
import type { AdminUser } from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

export function AdminUsers({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Users — admin')
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [error, setError] = useState<Record<string, string>>({})
  const [globalError, setGlobalError] = useState<string | null>(null)

  const refresh = () => listUsers().then(setUsers).catch(err => {
    setGlobalError(err instanceof Error ? err.message : 'load failed')
  })
  useEffect(() => { refresh() }, [])

  const filteredUsers = useMemo(() => {
    if (!users) return null
    const q = search.trim().toLowerCase()
    if (!q) return users
    return users.filter(u => u.email.toLowerCase().includes(q) || u.role.toLowerCase().includes(q))
  }, [users, search])

  const handleChange = async (email: string, role: string) => {
    setBusy(prev => new Set(prev).add(email))
    setError(prev => { const next = { ...prev }; delete next[email]; return next })
    try {
      await setUserRole(email, role)
      await refresh()
    } catch (err) {
      setError(prev => ({ ...prev, [email]: err instanceof Error ? err.message : 'update failed' }))
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(email); return next })
    }
  }

  return (
    <div className="min-h-screen text-white">
      <Nav user={user} />
      <div className="max-w-5xl mx-auto px-6 py-10">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-semibold mb-1">Users</h1>
            <p className="text-sm text-gray-400">Search users, view individual runs, and manage RBAC roles.</p>
          </div>
          <div className="flex items-center gap-3">
            <input
              type="search"
              placeholder="Search user or role…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-[#645DF6] transition-colors w-64"
            />
          </div>
        </div>

        {globalError && (
          <div className="mb-4 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {globalError}
          </div>
        )}
        {users === null && <p className="text-sm text-gray-500 py-12 text-center">Loading…</p>}
        {users !== null && users.length === 0 && <p className="text-sm text-gray-500 py-12 text-center">No users registered yet.</p>}
        {filteredUsers !== null && users !== null && users.length > 0 && filteredUsers.length === 0 && (
          <p className="text-sm text-gray-500 py-12 text-center">No users matching &ldquo;{search}&rdquo;</p>
        )}

        {filteredUsers && filteredUsers.length > 0 && (
          <div className="border border-white/10 rounded-xl overflow-hidden shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.04] text-xs uppercase tracking-wider text-gray-400 border-b border-white/10">
                <tr>
                  <th className="text-left px-5 py-3 font-medium">User Email</th>
                  <th className="text-left px-5 py-3 font-medium">Role</th>
                  <th className="text-left px-5 py-3 font-medium">Last Seen</th>
                  <th className="text-right px-5 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filteredUsers.map(u => {
                  const canSetAdmin = Boolean(user?.is_facets_employee)
                  const isStoredAdminNonFacets = u.role === 'admin' && !canSetAdmin

                  return (
                    <tr key={u.email} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-5 py-3 text-gray-200 font-mono text-xs">
                        <div className="flex items-center gap-2">
                          <span>{u.email}</span>
                          {isStoredAdminNonFacets && (
                            <span
                              title="Stored as admin, not effective (restricted to @facets accounts)"
                              className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/30"
                            >
                              ineffective admin
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        <select
                          value={u.role}
                          disabled={busy.has(u.email)}
                          onChange={e => handleChange(u.email, e.target.value)}
                          className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1 text-xs text-gray-200 disabled:opacity-40 focus:outline-none focus:border-[#645DF6]"
                        >
                          <option value="viewer" className="bg-gray-900 text-white">viewer</option>
                          <option
                            value="admin"
                            disabled={!canSetAdmin}
                            title={!canSetAdmin ? "Admin access is restricted to @facets accounts" : undefined}
                            className="bg-gray-900 text-white disabled:text-gray-500"
                          >
                            admin {!canSetAdmin ? "(@facets only)" : ""}
                          </option>
                        </select>
                        {error[u.email] && <p className="text-xs text-orange-400 mt-1">{error[u.email]}</p>}
                      </td>
                      <td className="px-5 py-3 text-gray-400 text-xs">{new Date(u.updated_at).toLocaleString()}</td>
                      <td className="px-5 py-3 text-right">
                        <Link
                          to={`/admin?q=${encodeURIComponent(u.email)}`}
                          className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[#00C2BB]/10 text-[#00C2BB] hover:bg-[#00C2BB]/20 text-xs font-medium transition-colors"
                        >
                          View runs →
                        </Link>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
