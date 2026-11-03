import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  getCatalogStatus,
  triggerCatalogRefresh,
  type CatalogStatus,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { parseAdminApiError, type AdminApiErrorState } from '../components/adminError'
import { BackButton } from '../components/BackButton'

export function AdminCatalog({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Catalog — admin')
  const nav = useNavigate()
  const [status, setStatus] = useState<CatalogStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<AdminApiErrorState | null>(null)

  const refresh = async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const s = await getCatalogStatus()
      setStatus(s)
    } catch (err) {
      const parsed = parseAdminApiError(err, 'Catalog Status', () => nav('/login'))
      setLoadError(parsed)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const handleRefresh = async () => {
    setRefreshing(true)
    setActionError(null)
    try {
      await triggerCatalogRefresh()
      setTimeout(refresh, 3000)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <Layout user={user}>
      <div className="max-w-4xl mx-auto px-6 py-10">
        <BackButton fallback="/admin" />
        <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">GCP Pricing Catalog</h1>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-2 mb-8 max-w-2xl leading-relaxed">
          Freshness of the GCP SKU pricing catalog used for every projection. Refresh pulls the latest public
          Google Cloud rates.
        </p>

        {loading && <p className="text-sm text-gray-500 py-12 text-center">Loading…</p>}

        {loadError && (
          <div className="p-4 rounded-xl bg-orange-950/40 border border-orange-500/40 text-orange-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">
              {loadError.status === 403 ? 'Permission Denied' : 'Failed to load catalog status'}
            </strong>
            <p className="text-xs text-orange-300">{loadError.message}</p>
            <button
              onClick={refresh}
              className="mt-3 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white text-xs font-medium border border-white/10 transition-colors cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}

        {status && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
                <div className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1.5">Last refreshed</div>
                <div className="text-xl font-semibold tabular-nums text-[color:var(--fcp-text-primary)]">
                  {status.age_days >= 9999 ? 'unknown' : `${status.age_days} day${status.age_days === 1 ? '' : 's'} ago`}
                </div>
              </div>
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up" style={{ animationDelay: '60ms' }}>
                <div className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1.5">Status</div>
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${status.stale ? 'bg-orange-400' : 'bg-[#00C2BB]'}`} />
                  <span className={`text-xl font-semibold ${status.stale ? 'text-orange-400' : 'text-[#00C2BB]'}`}>
                    {status.stale ? 'Stale' : 'Fresh'}
                  </span>
                </div>
              </div>
            </div>
            {actionError && <p className="text-xs text-orange-400">{actionError}</p>}
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="btn-shimmer w-full sm:w-auto px-6 py-2.5 rounded-lg font-medium text-white text-sm cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {refreshing ? 'Starting refresh…' : 'Refresh now'}
            </button>
          </div>
        )}
      </div>
    </Layout>
  )
}
