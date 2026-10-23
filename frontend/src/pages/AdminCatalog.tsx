import { useEffect, useState } from 'react'
import { Nav } from '../components/Nav'
import {
  getCatalogStatus,
  triggerCatalogRefresh,
  getNotifyStatus,
  notifyTest,
  type CatalogStatus,
  type NotifyStatus,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

export function AdminCatalog({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Catalog — admin')
  const [status, setStatus] = useState<CatalogStatus | null>(null)
  const [notifyStatus, setNotifyStatus] = useState<NotifyStatus | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [notifying, setNotifying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    const [s, n] = await Promise.all([getCatalogStatus(), getNotifyStatus()])
    setStatus(s)
    setNotifyStatus(n)
  }
  useEffect(() => { refresh() }, [])

  const handleRefresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      await triggerCatalogRefresh()
      setTimeout(refresh, 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const handleNotifyTest = async () => {
    setNotifying(true)
    setError(null)
    try {
      await notifyTest()
      setTimeout(refresh, 1000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'test notification failed')
    } finally {
      setNotifying(false)
    }
  }

  return (
    <div className="min-h-screen text-white">
      <Nav user={user} />
      <div className="max-w-2xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-semibold mb-1">GCP catalog</h1>
        <p className="text-sm text-gray-500 mb-8">Freshness of the GCP SKU pricing catalog used for projections.</p>

        {status === null && <p className="text-sm text-gray-500">Loading…</p>}

        {status && (
          <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-gray-400 text-sm">Last refreshed</span>
              <span className="font-semibold tabular-nums">
                {status.age_days >= 9999 ? 'unknown' : `${status.age_days} day${status.age_days === 1 ? '' : 's'} ago`}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-400 text-sm">Status</span>
              <span className={`font-semibold ${status.stale ? 'text-orange-400' : 'text-[#00C2BB]'}`}>
                {status.stale ? 'Stale' : 'Fresh'}
              </span>
            </div>
            {error && <p className="text-xs text-orange-400">{error}</p>}
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="btn-shimmer w-full py-2.5 rounded-lg font-medium text-white text-sm
                disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {refreshing ? 'Starting refresh…' : 'Refresh now'}
            </button>
          </div>
        )}

        {/* Notification health panel */}
        <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 space-y-4 mt-8">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Notification health
          </h2>
          {notifyStatus && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="px-4 py-3 rounded bg-white/[0.05] border border-white/10">
                  <div className="text-xs text-gray-400 uppercase tracking-wider mb-1">
                    Email notifications
                  </div>
                  <div className="flex items-center justify-between">
                    <span className={`text-sm ${notifyStatus.email.configured ? 'text-[#00C2BB]' : 'text-orange-400'}`}>
                      {notifyStatus.email.configured ? 'Configured' : 'Not configured'}
                    </span>
                  </div>
                </div>
                <div className="px-4 py-3 rounded bg-white/[0.05] border border-white/10">
                  <div className="text-xs text-gray-400 uppercase tracking-wider mb-1">
                    Slack notifications
                  </div>
                  <div className="flex items-center justify-between">
                    <span className={`text-sm ${notifyStatus.slack.configured ? 'text-[#00C2BB]' : 'text-orange-400'}`}>
                      {notifyStatus.slack.configured ? 'Configured' : 'Not configured'}
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={handleNotifyTest}
                  disabled={notifying}
                  className="px-4 py-2 rounded text-sm text-xs font-medium text-[#645DF6] hover:bg-[#645DF6]/10 disabled:opacity-40 transition-colors"
                >
                  {notifying ? 'Testing…' : 'Send test notification'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}