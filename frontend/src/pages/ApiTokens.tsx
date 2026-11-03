import { useCallback, useEffect, useState } from 'react'
import { Layout } from '../components/Layout'
import {
  createUserToken,
  getUserTokens,
  revokeUserToken,
  deleteUserToken,
  type APIToken,
  type CreateUserTokenRequest,
} from '../api/auth'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

const EXPIRY_OPTIONS: { label: string; value: number }[] = [
  { label: '30 days', value: 30 },
  { label: '90 days (default)', value: 90 },
  { label: '365 days', value: 365 },
  { label: 'Never', value: 0 },
]

const SCOPE_OPTIONS: { value: string; label: string; desc: string }[] = [
  {
    value: 'jobs:read',
    label: 'Jobs: read',
    desc: 'List your own jobs and read your own reports',
  },
  {
    value: 'jobs:write',
    label: 'Jobs: write',
    desc: 'Create and cancel your own jobs',
  },
  {
    value: 'reports:read',
    label: 'Reports: read',
    desc: 'Download your own report artifacts',
  },
]

function relativeTime(iso: string | undefined): string {
  if (!iso) return 'Never used'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Never used'
  const now = new Date()
  const seconds = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`
  return d.toLocaleDateString()
}

function statusBadge(token: APIToken): React.ReactNode {
  if (token.revoked_at) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-red-500/50 bg-red-500/10 text-red-400">
        Revoked
      </span>
    )
  }
  if (token.expires_at && new Date(token.expires_at).getTime() < Date.now()) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-orange-500/50 bg-orange-500/10 text-orange-400">
        Expired
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-[#00C2BB]/50 bg-[#00C2BB]/10 text-[#00C2BB]">
      Active
    </span>
  )
}

export function ApiTokens({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('API Tokens')
  const [tokens, setTokens] = useState<APIToken[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState(false)
  const [revealing, setRevealing] = useState(false)
  const [revealedToken, setRevealedToken] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<{ keyId: string; tokenName: string } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{ keyId: string; tokenName: string } | null>(null)
  const [deletingTok, setDeletingTok] = useState(false)
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>(['jobs:read'])
  const [ttlDays, setTtlDays] = useState(90)
  const [createError, setCreateError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getUserTokens()
      setTokens(data.tokens ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load tokens')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleToggleScope = (value: string) => {
    setScopes((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value],
    )
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreateError(null)
    if (!name.trim()) {
      setCreateError('a name is required')
      return
    }
    setCreating(true)
    try {
      const req: CreateUserTokenRequest = {
        name: name.trim(),
        scopes: scopes.length > 0 ? scopes : ['jobs:read'],
        ttl_days: ttlDays,
      }
      const res = await createUserToken(req)
      setName('')
      setScopes(['jobs:read'])
      setTtlDays(90)
      setOpen(false)
      setRevealedToken(res.token)
      setRevealing(true)
      await refresh()
      triggerNotificationRefresh()
      toast.success('Token created')
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'create failed')
    } finally {
      setCreating(false)
    }
  }

  const confirmRevoke = (keyId: string, tokenName: string) => {
    setRevokeTarget({ keyId, tokenName })
  }

  const handleRevoke = async () => {
    if (!revokeTarget) return
    const { keyId, tokenName } = revokeTarget
    const prev = tokens.find((t) => t.key_id === keyId)
    setRevoking(keyId)
    setTokens(
      tokens.map((t) =>
        t.key_id === keyId ? { ...t, revoked_at: new Date().toISOString() } : t,
      ),
    )
    try {
      await revokeUserToken(keyId)
      triggerNotificationRefresh()
      toast.success(`Revoked "${tokenName}"`)
      setRevokeTarget(null)
    } catch (err) {
      setTokens(
        tokens.map((t) => (t.key_id === keyId ? (prev ?? t) : t)),
      )
      toast.error(err instanceof Error ? err.message : 'revoke failed')
    } finally {
      setRevoking(null)
    }
  }

  const handleDeleteToken = async () => {
    if (!deleteTarget) return
    setDeletingTok(true)
    try {
      await deleteUserToken(deleteTarget.keyId)
      setTokens(prev => prev.filter(t => t.key_id !== deleteTarget.keyId))
      triggerNotificationRefresh()
      toast.success(`Deleted "${deleteTarget.tokenName}"`)
      setDeleteTarget(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'delete failed')
    } finally {
      setDeletingTok(false)
    }
  }

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text)
    toast.success('Token copied to clipboard')
  }

  if (loading) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10 text-center">
          <div className="flex items-center justify-center h-32">
            <div className="flex gap-2">
              {[0, 1, 2, 3].map((i, idx) => (
                <div
                  key={i}
                  className="w-2 h-2 rounded-full bg-[#00C2BB] animate-pulse"
                  style={{ animationDelay: `${idx * 100}ms` }}
                />
              ))}
            </div>
          </div>
          <p className="text-sm text-gray-500 text-center mt-2">Loading tokens…</p>
        </div>
      </Layout>
    )
  }

  if (error) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10 text-center">
          <p className="text-xs text-orange-400">{error}</p>
          <button
            onClick={() => {
              setError(null)
              refresh()
            }}
            className="mt-3 text-xs text-[#00C2BB] hover:text-white underline"
          >
            Retry
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">API Tokens</h1>
            <p className="text-sm text-gray-400 mt-1">
              Personal access tokens authenticate REST API calls without a
              session cookie. Use them to connect scripts, CI, or other tools
              to the Cost Engine. The full token is shown only once — copy it
              when it is created.
            </p>
          </div>
          <button
            onClick={() => {
              setCreateError(null)
              setOpen(true)
            }}
            className="inline-flex items-center justify-center gap-2 btn-shimmer px-5 py-2.5 rounded-lg text-sm font-medium text-white cursor-pointer whitespace-nowrap"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg> Create token
          </button>
        </div>

        {error && (
          <div className="mb-6 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        <div className="border border-white/10 rounded-lg overflow-hidden bg-white/[0.01]">
          <div className="bg-white/[0.02] border-b border-white/10 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
              Your tokens
            </h2>
          </div>
          <div className="overflow-x-auto xl:overflow-x-visible">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Name</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Token</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Scopes</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Created</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Last used</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4">Expires</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4 text-right">
                    Status
                  </th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-4 text-right">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {tokens.length === 0 ? (
                  <tr>
                    <td className="px-4 py-6 text-center text-gray-500 text-xs" colSpan={8}>
                      No API tokens yet
                    </td>
                  </tr>
                ) : (
                  tokens.map((token) => {
                    const revoked = !!token.revoked_at
                    const expired =
                      !!token.expires_at &&
                      new Date(token.expires_at).getTime() < Date.now()
                    return (
                      <tr
                        key={token.key_id}
                        className={`hover:bg-white/[0.01] transition-colors ${
                          revoked ? 'opacity-60' : ''
                        }`}
                      >
                        <td className="px-4 py-4">
                          <div className="font-semibold text-white">{token.name}</div>
                          <div className="text-xs text-gray-500 mt-0.5 font-mono">
                            {token.key_id}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          <span className="font-mono text-xs text-[#00C2BB]">
                            {token.display}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-gray-400 text-xs">
                          {token.scopes.join(', ')}
                        </td>
                        <td className="px-4 py-4 text-gray-400 text-xs">
                          {token.created_at}
                        </td>
                        <td className="px-4 py-4 text-gray-400 text-xs">
                          {relativeTime(token.last_used_at)}
                        </td>
                        <td className="px-4 py-4 text-gray-400 text-xs">
                          {token.expires_at || 'Never'}
                        </td>
                        <td className="px-4 py-4 text-right">
                          {statusBadge(token)}
                        </td>
                        <td className="px-4 py-4 text-right">
                          <div className="inline-flex items-center gap-2 justify-end">
                            {!revoked && !expired && (
                              <button
                                type="button"
                                onClick={() => confirmRevoke(token.key_id, token.name)}
                                disabled={revoking === token.key_id}
                                className="text-[10px] text-orange-400 hover:text-orange-300 underline cursor-pointer disabled:opacity-40"
                              >
                                Revoke
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => setDeleteTarget({ keyId: token.key_id, tokenName: token.name })}
                              title="Delete token permanently"
                              className="inline-flex items-center justify-center w-7 h-7 rounded border border-white/10 text-gray-400 hover:text-red-400 hover:border-red-500/40 transition-colors cursor-pointer"
                            >
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
          {tokens.length > 0 && (
            <div className="px-5 py-4 border-t border-white/10 text-xs text-gray-500">
              Revoked and expired tokens remain in the list so you can see what
              they were and when they stopped working.
            </div>
          )}
        </div>

        {open && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center">
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" />
            <div className="relative w-full max-w-md m-4 bg-[#13131a] border border-white/10 rounded-xl p-6 shadow-2xl">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold">Create API token</h2>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded p-1 hover:bg-white/5 text-gray-400 hover:text-white cursor-pointer"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Praxis laptop"
                    className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 focus:border-[#645DF6] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
                    Scopes
                  </label>
                  <p className="text-xs text-gray-500 mb-2">
                    Scopes narrow what the token can do within your own access.
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {SCOPE_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => handleToggleScope(opt.value)}
                        className={`px-3 py-2 rounded border text-xs text-left transition-colors ${
                          scopes.includes(opt.value)
                            ? 'border-[#00C2BB] bg-[#00C2BB]/20 text-[#00C2BB]'
                            : 'border-white/10 text-gray-400 hover:border-white/30 hover:text-white'
                        }`}
                      >
                        <span className="font-medium">{opt.label}</span>
                        <span className="ml-2 text-[10px] opacity-70">
                          {opt.desc}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
                    Expires
                  </label>
                  <select
                    value={ttlDays}
                    onChange={(e) => setTtlDays(Number(e.target.value))}
                    className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-3 text-sm text-white focus:border-[#645DF6] transition-colors"
                  >
                    {EXPIRY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                {createError && (
                  <p className="text-xs text-red-400">{createError}</p>
                )}
                <div className="flex items-center justify-end gap-3 mt-6">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    disabled={creating}
                    className="px-4 py-2 rounded text-xs text-gray-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-40"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={creating}
                    className="px-4 py-2 rounded bg-[#645DF6] text-white text-xs font-medium hover:bg-[#645DF6]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {creating ? 'Creating…' : 'Create token'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {revealing && revealedToken && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center">
            <div className="relative w-full max-w-md m-4 bg-[#13131a] border border-white/10 rounded-xl p-6 shadow-2xl">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold">Copy your token</h2>
              </div>
              <p className="text-xs text-orange-400 mb-4">
                Copy this now. You will not be able to see it again.
              </p>
              <div className="mb-4">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={revealedToken}
                    className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-3 text-sm font-mono text-[#00C2BB] select-all outline-none"
                  />
                  <button
                    onClick={() => handleCopy(revealedToken)}
                    className="px-3 py-2 rounded bg-[#00C2BB]/20 text-[#00C2BB] hover:bg-[#00C2BB]/40 transition-colors"
                    title="Copy"
                  >
                    Copy
                  </button>
                </div>
              </div>
              <button
                onClick={() => {
                  setRevealing(false)
                  setRevealedToken(null)
                }}
                className="w-full py-2 rounded border border-white/10 text-xs text-white hover:bg-white/5 transition-colors"
              >
                I've copied it
              </button>
            </div>
          </div>
        )}

        <ConfirmDialog
          open={!!revokeTarget}
          onOpenChange={(o) => { if (!o) setRevokeTarget(null) }}
          title={`Revoke "${revokeTarget?.tokenName ?? ''}"?`}
          description="Any integration using this token will stop working immediately. This cannot be undone."
          confirmLabel="Revoke token"
          destructive
          loading={!!revoking}
          onConfirm={handleRevoke}
        />

        <ConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
          title={`Delete "${deleteTarget?.tokenName ?? ''}" permanently?`}
          description="This removes the token from your list entirely. Any integration still using it will stop working. This cannot be undone."
          confirmLabel="Delete token"
          destructive
          loading={deletingTok}
          onConfirm={handleDeleteToken}
        />
      </div>
    </Layout>
  )
}
