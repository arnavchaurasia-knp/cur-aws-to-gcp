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
import { BackButton } from '../components/BackButton'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

const EXPIRY_OPTIONS: { label: string; value: number }[] = [
  { label: '30 days', value: 30 },
  { label: '90 days (Recommended)', value: 90 },
  { label: '365 days', value: 365 },
  { label: 'Never expires', value: 0 },
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
    desc: 'Create, run, and cancel your own projection jobs',
  },
  {
    value: 'reports:read',
    label: 'Reports: read',
    desc: 'Download your own report artifacts and pricing datasets',
  },
]

function relativeTime(iso: string | undefined): string {
  if (!iso) return 'Never used'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Never used'
  const now = new Date()
  const seconds = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (seconds < 60) return 'Just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return d.toLocaleDateString()
}

function statusBadge(token: APIToken): React.ReactNode {
  if (token.revoked_at) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-500 border border-rose-500/20">
        Revoked
      </span>
    )
  }
  if (token.expires_at && new Date(token.expires_at).getTime() < Date.now()) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-500 border border-amber-500/20">
        Expired
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#00C2BB]/10 text-[#00C2BB] border border-[#00C2BB]/30">
      Active
    </span>
  )
}

export function ApiTokens({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('API Tokens — Facets')
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
  const [scopes, setScopes] = useState<string[]>(['jobs:read', 'jobs:write'])
  const [ttlDays, setTtlDays] = useState(90)
  const [createError, setCreateError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getUserTokens()
      setTokens(data.tokens || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tokens')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleToggleScope = (scope: string) => {
    if (scopes.includes(scope)) {
      setScopes(scopes.filter((s) => s !== scope))
    } else {
      setScopes([...scopes, scope])
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) {
      setCreateError('Please give this token a recognizable name or purpose')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      const payload: CreateUserTokenRequest = {
        name: name.trim(),
        scopes: scopes.length ? scopes : undefined,
        ttl_days: ttlDays,
      }
      const res = await createUserToken(payload)
      setRevealedToken(res.token)
      setRevealing(true)
      setOpen(false)
      setName('')
      setScopes(['jobs:read', 'jobs:write'])
      setTtlDays(90)
      refresh()
      triggerNotificationRefresh()
      toast.success('API token generated successfully')
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Creation failed')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async () => {
    if (!revokeTarget) return
    const { keyId, tokenName } = revokeTarget
    setRevoking(keyId)
    try {
      await revokeUserToken(keyId)
      setTokens(prev =>
        prev.map(t => (t.key_id === keyId ? { ...t, revoked_at: new Date().toISOString() } : t))
      )
      triggerNotificationRefresh()
      toast.success(`Revoked "${tokenName}"`)
      setRevokeTarget(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Revoke failed')
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
      toast.success(`Deleted token "${deleteTarget.tokenName}"`)
      setDeleteTarget(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeletingTok(false)
    }
  }

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text)
    toast.success('Token secret copied to clipboard')
  }

  return (
    <Layout user={user}>
      <div className="w-full max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <BackButton fallback="/profile" />

        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">API Tokens</h1>
            <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
              Personal access tokens authenticate programmatic REST API requests and Model Context Protocol (MCP) clients.
            </p>
          </div>
          <button
            onClick={() => {
              setCreateError(null)
              setOpen(true)
            }}
            className="inline-flex items-center justify-center gap-2 btn-shimmer px-5 py-2.5 rounded-xl text-sm font-semibold text-white cursor-pointer shrink-0 shadow-sm"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Generate New Token
          </button>
        </div>

        {error && (
          <div className="p-4 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 text-rose-800 dark:text-rose-200 text-xs">
            <strong className="block font-semibold mb-1">Failed to load API tokens</strong>
            <p>{error}</p>
          </div>
        )}

        {/* Secret Display Banner (revealed after creation) */}
        {revealing && revealedToken && (
          <div className="rounded-2xl border border-emerald-500/40 bg-emerald-950/30 dark:bg-emerald-950/40 p-5 space-y-3 anim-scale-in shadow-lg">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-xs uppercase font-bold tracking-wider text-emerald-400">
                  New API Token Generated
                </span>
              </div>
              <button
                onClick={() => {
                  setRevealing(false)
                  setRevealedToken(null)
                }}
                className="text-xs text-gray-400 hover:text-white cursor-pointer px-2 py-1 rounded hover:bg-white/5"
              >
                ✕ Dismiss
              </button>
            </div>
            <p className="text-xs text-gray-200">
              Make sure to copy your personal access token now. For security purposes,{' '}
              <strong className="text-white">it will never be displayed again</strong>.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded-xl bg-black/60 border border-emerald-500/30 px-3.5 py-2.5 font-mono text-xs text-emerald-300 break-all select-all">
                {revealedToken}
              </code>
              <button
                onClick={() => handleCopy(revealedToken)}
                className="btn-shimmer px-4 py-2.5 rounded-xl text-xs font-semibold text-white shrink-0 cursor-pointer flex items-center gap-1.5"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                </svg>
                Copy Secret
              </button>
            </div>
          </div>
        )}

        {/* Tokens List Table Card */}
        <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] overflow-hidden shadow-sm">
          <div className="px-6 py-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Active &amp; Historic Tokens</h2>
              <p className="text-xs text-[var(--fcp-text-secondary)]">Tokens authorized for your user account.</p>
            </div>
            <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)]">
              {tokens.length} {tokens.length === 1 ? 'Token' : 'Tokens'}
            </span>
          </div>

          {loading ? (
            <div className="p-12 text-center text-sm text-[var(--fcp-text-secondary)]">
              <div className="inline-block w-5 h-5 rounded-full border-2 border-[#00C2BB] border-t-transparent animate-spin mb-2" />
              <p>Loading personal access tokens…</p>
            </div>
          ) : tokens.length === 0 ? (
            <div className="p-12 text-center space-y-3">
              <div className="w-12 h-12 rounded-2xl bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-tertiary)] mx-auto flex items-center justify-center">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
              </div>
              <h3 className="text-sm font-medium text-[var(--fcp-text-primary)]">No API tokens generated yet</h3>
              <p className="text-xs text-[var(--fcp-text-secondary)] max-w-sm mx-auto">
                Generate an API token to interact with the Cost Engine through CLI tools, scripts, or automated pipelines.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] text-[var(--fcp-text-secondary)]">
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Token Name</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Prefix</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Scopes</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Created</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Last Used</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px]">Status</th>
                    <th className="px-5 py-3 font-semibold uppercase tracking-wider text-[11px] text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                  {tokens.map((token) => {
                    const isRevoked = Boolean(token.revoked_at)
                    const isExpired = Boolean(token.expires_at && new Date(token.expires_at).getTime() < Date.now())

                    return (
                      <tr
                        key={token.key_id}
                        className={`hover:bg-[var(--fcp-bg-surface-hover)]/60 transition-colors ${
                          isRevoked ? 'opacity-60 bg-black/5 dark:bg-white/[0.01]' : ''
                        }`}
                      >
                        <td className="px-5 py-3.5">
                          <div className="font-semibold text-[var(--fcp-text-primary)] text-sm">{token.name}</div>
                          <div className="font-mono text-[10px] text-[var(--fcp-text-tertiary)] mt-0.5">{token.key_id}</div>
                        </td>
                        <td className="px-5 py-3.5">
                          <code className="px-2 py-1 rounded bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] font-mono text-[11px] text-[#00C2BB]">
                            {token.display}
                          </code>
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="flex flex-wrap gap-1 max-w-xs">
                            {token.scopes.map((s) => (
                              <span
                                key={s}
                                className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/5 border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)]"
                              >
                                {s}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-5 py-3.5 text-[var(--fcp-text-secondary)]">
                          {new Date(token.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-5 py-3.5 text-[var(--fcp-text-secondary)]">
                          {relativeTime(token.last_used_at)}
                        </td>
                        <td className="px-5 py-3.5">
                          {statusBadge(token)}
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          <div className="inline-flex items-center gap-2 justify-end">
                            {/* Revoke button if active */}
                            {!isRevoked && !isExpired && (
                              <button
                                type="button"
                                onClick={() => setRevokeTarget({ keyId: token.key_id, tokenName: token.name })}
                                disabled={revoking === token.key_id}
                                className="px-2.5 py-1 rounded-lg text-xs font-medium text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 border border-amber-500/20 transition-colors cursor-pointer disabled:opacity-40"
                              >
                                Revoke
                              </button>
                            )}

                            {/* DELETE BUTTON for API: Allows purging/deleting tokens */}
                            <button
                              type="button"
                              onClick={() => setDeleteTarget({ keyId: token.key_id, tokenName: token.name })}
                              title="Delete token permanently"
                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 border border-rose-500/20 transition-colors cursor-pointer"
                            >
                              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                              <span>Delete</span>
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Modal: Generate New Token */}
        {open && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
            <div className="relative w-full max-w-lg rounded-2xl bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] p-6 shadow-2xl space-y-5 anim-scale-in">
              <div className="flex items-center justify-between pb-3 border-b border-[var(--fcp-border-subtle)]">
                <div>
                  <h2 className="text-base font-bold text-[var(--fcp-text-primary)]">Generate Personal API Token</h2>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">Configure token description and access privileges.</p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-lg p-1.5 text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] cursor-pointer"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                    Token Purpose / Name
                  </label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. CI/CD Deployment Pipeline, MCP Assistant"
                    className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20 transition-all"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                    Expiration Duration
                  </label>
                  <select
                    value={ttlDays}
                    onChange={(e) => setTtlDays(Number(e.target.value))}
                    className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20 transition-all cursor-pointer"
                  >
                    {EXPIRY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value} className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                    Permission Scopes
                  </label>
                  <div className="space-y-2">
                    {SCOPE_OPTIONS.map((opt) => {
                      const isSelected = scopes.includes(opt.value)
                      return (
                        <div
                          key={opt.value}
                          onClick={() => handleToggleScope(opt.value)}
                          className={`flex items-start gap-3 p-3 rounded-xl border text-xs cursor-pointer transition-all select-none ${
                            isSelected
                              ? 'border-[#00C2BB]/60 bg-[#00C2BB]/10 text-[var(--fcp-text-primary)]'
                              : 'border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] text-[var(--fcp-text-secondary)] hover:border-gray-400/50'
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {}}
                            className="mt-0.5 rounded text-[#00C2BB] focus:ring-0"
                          />
                          <div>
                            <span className="font-semibold block text-[var(--fcp-text-primary)]">{opt.label}</span>
                            <span className="text-[11px] text-[var(--fcp-text-secondary)] mt-0.5 block">{opt.desc}</span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {createError && (
                  <p className="text-xs text-rose-500 font-medium">{createError}</p>
                )}

                <div className="flex items-center justify-end gap-3 pt-3 border-t border-[var(--fcp-border-subtle)]">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    disabled={creating}
                    className="px-4 py-2.5 rounded-xl text-xs font-medium border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={creating}
                    className="btn-shimmer px-5 py-2.5 rounded-xl text-xs font-semibold text-white cursor-pointer disabled:opacity-40"
                  >
                    {creating ? 'Generating…' : 'Generate Token'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Revoke Confirmation Dialog */}
        <ConfirmDialog
          open={Boolean(revokeTarget)}
          onOpenChange={(o) => { if (!o) setRevokeTarget(null) }}
          title={`Revoke API Token "${revokeTarget?.tokenName ?? ''}"?`}
          description="Any script, MCP server, or integration relying on this token will immediately cease to authenticate. This token cannot be reactivated."
          confirmLabel="Revoke Token"
          destructive
          loading={Boolean(revoking)}
          onConfirm={handleRevoke}
        />

        {/* Delete Confirmation Dialog */}
        <ConfirmDialog
          open={Boolean(deleteTarget)}
          onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
          title={`Permanently Delete "${deleteTarget?.tokenName ?? ''}"?`}
          description="This will permanently purge the token record from your account history and database. Any associated credentials will be permanently destroyed."
          confirmLabel="Delete Permanently"
          destructive
          loading={deletingTok}
          onConfirm={handleDeleteToken}
        />
      </div>
    </Layout>
  )
}
