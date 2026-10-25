import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { StatCard } from './AdminJobs'
import { getAnalytics } from '../api/admin'
import type { UsageStats } from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { parseAdminApiError, type AdminApiErrorState } from '../components/adminError'

export function AdminAnalytics({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Analytics — admin')
  const nav = useNavigate()
  const [stats, setStats] = useState<UsageStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<AdminApiErrorState | null>(null)

  const loadData = () => {
    setLoading(true)
    setLoadError(null)
    getAnalytics()
      .then(res => setStats(res))
      .catch(err => {
        const parsed = parseAdminApiError(err, 'Platform Analytics', () => nav('/login'))
        setLoadError(parsed)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadData()
  }, [])

  const chartData = (stats?.jobs_by_day ?? []).map(d => ({ day: d.day.slice(5), count: d.count }))

  return (
    <Layout user={user}>
      <div className="max-w-5xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-semibold mb-1">Analytics</h1>
        <p className="text-sm text-gray-500 mb-8">Platform-wide usage over the last 30 days.</p>

        {loading && <p className="text-sm text-gray-500 py-12 text-center">Loading…</p>}

        {loadError && (
          <div className="p-4 rounded-xl bg-orange-950/40 border border-orange-500/40 text-orange-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">
              {loadError.status === 403 ? 'Permission Denied' : 'Failed to load analytics'}
            </strong>
            <p className="text-xs text-orange-300">{loadError.message}</p>
            <button
              onClick={loadData}
              className="mt-3 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white text-xs font-medium border border-white/10 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && stats && stats.total_jobs === 0 && (
          <p className="text-sm text-gray-500 py-12 text-center">No jobs found in the last 30 days.</p>
        )}

        {stats && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-10">
              <StatCard label="Total jobs" value={stats.total_jobs} color="text-white" />
              <StatCard label="Done" value={stats.done_jobs} color="text-[#00C2BB]" />
              <StatCard label="Failed" value={stats.failed_jobs} color="text-orange-400" />
              <StatCard label="Failure rate" value={Math.round(stats.failure_rate * 100)} color="text-[#645DF6]" />
            </div>

            <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5 mb-8">
              <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-4">
                Jobs per day (last 30 days)
              </h3>
              <div style={{ width: '100%', height: 260 }}>
                <ResponsiveContainer>
                  <BarChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                    <XAxis dataKey="day" stroke="rgba(255,255,255,0.4)" fontSize={11} />
                    <YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} allowDecimals={false} />
                    <Tooltip contentStyle={{ background: '#111118', border: '1px solid rgba(255,255,255,0.1)' }} />
                    <Bar dataKey="count" fill="#645DF6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
              <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-2">
                Avg. processing time
              </h3>
              <p className="text-2xl font-semibold tabular-nums">
                {Math.round(stats.avg_processing_seconds / 60)} min
              </p>
            </div>
          </>
        )}
      </div>
    </Layout>
  )
}
