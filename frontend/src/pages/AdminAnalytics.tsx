import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { getAnalytics } from '../api/admin'
import type { UsageStats } from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, Cell,
} from 'recharts'
import { parseAdminApiError, type AdminApiErrorState } from '../components/adminError'
import { BackButton } from '../components/BackButton'

/* ── Brand palette (house tokens) ── */
const AWS_COLOR = '#F6943D'   // amber — source cloud
const GCP_COLOR = '#00C2BB'   // teal — target cloud
const SAVE_COLOR = '#645DF6'  // purple — savings / accent

/* ── Formatting ── */
function fmtMoney(n: number): string {
  const sign = n < 0 ? '-' : ''
  const a = Math.abs(n)
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 1)}M`
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(a >= 10_000 ? 0 : 1)}K`
  return `${sign}$${a.toFixed(0)}`
}
function fmtMoneyFull(n: number): string {
  return `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

const tooltipStyle = {
  background: '#0d0d14',
  border: '1px solid rgba(255,255,255,0.15)',
  borderRadius: '8px',
  fontSize: '12px',
  color: '#fff',
}

export function AdminAnalytics({ user }: Readonly<{ user: UserInfo }>) {
  const axisColor = 'var(--fcp-text-tertiary)'
  const gridColor = 'var(--fcp-border-subtle)'

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
      .catch(err => setLoadError(parseAdminApiError(err, 'Platform Analytics', () => nav('/login'))))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadData() }, [])

  const volumeData = (stats?.jobs_by_day ?? []).map(d => ({ day: d.day.slice(5), count: d.count }))
  const spendData = (stats?.spend_by_day ?? []).map(d => ({ day: d.day.slice(5), AWS: d.aws, GCP: d.gcp }))
  const buckets = stats?.savings_buckets ?? []
  const prospects = stats?.top_prospects ?? []
  const hasEconomics = (stats?.analyzed_jobs ?? 0) > 0

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-10">
        <BackButton fallback="/admin" />
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-semibold mb-1">Migration Analytics</h1>
            <p className="text-sm text-gray-500">
              Portfolio-wide AWS→GCP savings opportunity and pipeline throughput across all analyzed bills.
            </p>
          </div>
          <button
            onClick={loadData}
            disabled={loading}
            className="self-start sm:self-auto inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-200 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RefreshIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        {loading && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-500 gap-3">
            <div className="w-6 h-6 border-2 border-[#00C2BB] border-t-transparent rounded-full animate-spin" />
            <p className="text-xs">Loading analytics data…</p>
          </div>
        )}

        {loadError && (
          <div className="p-4 rounded-xl bg-red-950/30 border border-red-500/30 text-red-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">
              {loadError.status === 403 ? 'Permission Denied' : 'Failed to load analytics'}
            </strong>
            <p className="text-xs text-red-300/80">{loadError.message}</p>
            <button
              onClick={loadData}
              className="mt-3 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white text-xs font-medium border border-white/10 transition-colors cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && stats && stats.total_jobs === 0 && (
          <div className="p-12 text-center border border-dashed border-white/10 rounded-xl bg-white/[0.01]">
            <LayersIcon className="w-8 h-8 text-gray-500 mx-auto mb-2 opacity-60" />
            <p className="text-sm text-gray-400 font-medium">No projection jobs found</p>
            <p className="text-xs text-gray-500 mt-1">Savings analytics will appear here once bills are analyzed.</p>
          </div>
        )}

        {stats && !loading && !loadError && stats.total_jobs > 0 && (
          <>
            {/* ───────── Migration economics (the headline) ───────── */}
            <SectionHeading icon={<TrendingDownIcon className="w-4 h-4 text-[#00C2BB]" />} title="Savings Opportunity" />

            {hasEconomics ? (
              <>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                  <BigStat
                    label="AWS Spend Analyzed"
                    value={fmtMoney(stats.total_aws_spend)}
                    helper={`Across ${stats.analyzed_jobs} priced bill${stats.analyzed_jobs === 1 ? '' : 's'}`}
                    color="text-[#F6943D]"
                    icon={<CloudIcon className="w-4 h-4 text-[#F6943D]" />}
                  />
                  <BigStat
                    label="Projected GCP Cost"
                    value={fmtMoney(stats.total_gcp_od)}
                    helper="On-demand equivalent"
                    color="text-[#00C2BB]"
                    icon={<CloudIcon className="w-4 h-4 text-[#00C2BB]" />}
                  />
                  <BigStat
                    label="Total Savings"
                    value={fmtMoney(stats.total_savings)}
                    helper={`${stats.savings_pct.toFixed(1)}% portfolio reduction`}
                    color={stats.total_savings >= 0 ? 'text-[#645DF6]' : 'text-orange-400'}
                    icon={<PiggyIcon className="w-4 h-4 text-[#645DF6]" />}
                    emphasis
                  />
                  <BigStat
                    label="Avg. Savings / Bill"
                    value={`${stats.avg_savings_pct.toFixed(1)}%`}
                    helper={`Avg bill ${fmtMoney(stats.avg_bill_size)}`}
                    color="text-white"
                    icon={<PercentIcon className="w-4 h-4 text-gray-300" />}
                  />
                </div>

                <div className="grid gap-6 lg:grid-cols-3 mb-8">
                  {/* AWS vs GCP spend over time */}
                  <ChartCard
                    className="lg:col-span-2"
                    icon={<ActivityIcon className="w-4 h-4 text-[#00C2BB]" />}
                    title="AWS vs Projected GCP Spend"
                    badge="Last 30 Days"
                  >
                    {spendData.length === 0 ? (
                      <EmptyChart text="No priced bills in the last 30 days." />
                    ) : (
                      <div style={{ width: '100%', height: 260 }}>
                        <ResponsiveContainer>
                          <BarChart data={spendData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                            <XAxis dataKey="day" stroke={axisColor} fontSize={11} tickLine={false} axisLine={{ stroke: gridColor }} />
                            <YAxis stroke={axisColor} fontSize={11} tickLine={false} axisLine={{ stroke: gridColor }} tickFormatter={(v: number) => fmtMoney(v)} />
                            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} contentStyle={tooltipStyle} formatter={(v: any) => fmtMoneyFull(Number(v))} />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <Bar dataKey="AWS" fill={AWS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} />
                            <Bar dataKey="GCP" fill={GCP_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </ChartCard>

                  {/* Savings % distribution */}
                  <ChartCard
                    icon={<BarsIcon className="w-4 h-4 text-[#645DF6]" />}
                    title="Savings Distribution"
                    badge="All bills"
                  >
                    <div style={{ width: '100%', height: 260 }}>
                      <ResponsiveContainer>
                        <BarChart data={buckets} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={false} />
                          <XAxis type="number" stroke={axisColor} fontSize={11} allowDecimals={false} tickLine={false} axisLine={{ stroke: gridColor }} />
                          <YAxis type="category" dataKey="label" stroke={axisColor} fontSize={10} width={96} tickLine={false} axisLine={false} />
                          <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} contentStyle={tooltipStyle} formatter={(v: any) => [`${Number(v)} bill${Number(v) === 1 ? '' : 's'}`, 'Count']} />
                          <Bar dataKey="count" radius={[0, 3, 3, 0]} maxBarSize={22}>
                            {buckets.map((b, i) => (
                              <Cell key={b.label} fill={b.label.startsWith('<0') ? '#F6943D' : SAVE_COLOR} fillOpacity={0.55 + 0.1 * i} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </ChartCard>
                </div>

                {/* Top prospects leaderboard */}
                <ChartCard
                  icon={<TrophyIcon className="w-4 h-4 text-[#00C2BB]" />}
                  title="Top Savings Opportunities"
                  badge={`${prospects.length} prospect${prospects.length === 1 ? '' : 's'}`}
                  className="mb-10"
                >
                  <div className="overflow-x-auto -mx-1">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-[11px] uppercase tracking-wider text-gray-500 border-b border-white/10">
                          <th className="text-left font-medium py-2 px-1">Prospect</th>
                          <th className="text-right font-medium py-2 px-1">AWS</th>
                          <th className="text-right font-medium py-2 px-1">GCP</th>
                          <th className="text-right font-medium py-2 px-1">Savings</th>
                          <th className="text-right font-medium py-2 px-1 w-40">Reduction</th>
                        </tr>
                      </thead>
                      <tbody>
                        {prospects.map(p => (
                          <tr key={p.prospect + p.created_at} className="border-b border-white/5 hover:bg-white/[0.02]">
                            <td className="py-2.5 px-1 font-medium text-gray-200 truncate max-w-[220px]">{p.prospect || '—'}</td>
                            <td className="py-2.5 px-1 text-right tabular-nums text-gray-400">{fmtMoney(p.aws_spend)}</td>
                            <td className="py-2.5 px-1 text-right tabular-nums text-gray-400">{fmtMoney(p.gcp_od)}</td>
                            <td className={`py-2.5 px-1 text-right tabular-nums font-semibold ${p.savings >= 0 ? 'text-[#645DF6]' : 'text-orange-400'}`}>{fmtMoney(p.savings)}</td>
                            <td className="py-2.5 px-1">
                              <div className="flex items-center justify-end gap-2">
                                <div className="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden max-w-[80px]">
                                  <div
                                    className="h-full rounded-full"
                                    style={{
                                      width: `${Math.max(0, Math.min(100, p.savings_pct))}%`,
                                      background: p.savings >= 0 ? SAVE_COLOR : AWS_COLOR,
                                    }}
                                  />
                                </div>
                                <span className="tabular-nums text-xs text-gray-300 w-12 text-right">{p.savings_pct.toFixed(0)}%</span>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </ChartCard>
              </>
            ) : (
              <div className="p-8 text-center border border-dashed border-white/10 rounded-xl bg-white/[0.01] mb-10">
                <PiggyIcon className="w-7 h-7 text-gray-500 mx-auto mb-2 opacity-60" />
                <p className="text-sm text-gray-400 font-medium">No priced bills yet</p>
                <p className="text-xs text-gray-500 mt-1">
                  Savings economics appear once jobs complete with both an AWS spend and a GCP projection.
                </p>
              </div>
            )}

            {/* ───────── Operational / pipeline health (secondary) ───────── */}
            <SectionHeading icon={<ServerIcon className="w-4 h-4 text-gray-400" />} title="Pipeline Health" />

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
              <BigStat label="Total Estimations" value={stats.total_jobs} color="text-white" icon={<LayersIcon className="w-4 h-4 text-gray-300" />} helper="All bills submitted" />
              <BigStat label="Successful" value={stats.done_jobs} color="text-[#00C2BB]" icon={<CheckCircleIcon className="w-4 h-4 text-[#00C2BB]" />} helper="Reports generated" />
              <BigStat label="Failed" value={stats.failed_jobs} color="text-orange-400" icon={<AlertTriangleIcon className="w-4 h-4 text-orange-400" />} helper="Validation or run errors" />
              <BigStat label="Failure Rate" value={`${Math.round(stats.failure_rate * 100)}%`} color="text-[#645DF6]" icon={<PercentIcon className="w-4 h-4 text-[#645DF6]" />} helper="Across all jobs" />
            </div>

            <div className="grid gap-6 lg:grid-cols-3 mb-8">
              <ChartCard className="lg:col-span-2" icon={<ActivityIcon className="w-4 h-4 text-[#00C2BB]" />} title="Daily Projection Volume" badge="Last 30 Days">
                {volumeData.length === 0 ? (
                  <EmptyChart text="No jobs in the last 30 days." />
                ) : (
                  <div style={{ width: '100%', height: 220 }}>
                    <ResponsiveContainer>
                      <BarChart data={volumeData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                        <XAxis dataKey="day" stroke={axisColor} fontSize={11} tickLine={false} axisLine={{ stroke: gridColor }} />
                        <YAxis stroke={axisColor} fontSize={11} allowDecimals={false} tickLine={false} axisLine={{ stroke: gridColor }} />
                        <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} contentStyle={tooltipStyle} />
                        <Bar dataKey="count" fill={SAVE_COLOR} radius={[4, 4, 0, 0]} maxBarSize={36} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </ChartCard>

              <div className="bg-white/[0.02] border border-white/10 rounded-xl p-5 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-4">
                    <ClockIcon className="w-4 h-4 text-[#645DF6]" />
                    <h3 className="text-xs font-semibold text-gray-200 uppercase tracking-wider">Avg. Processing Time</h3>
                  </div>
                  <p className="text-3xl font-bold tabular-nums text-white">
                    {Math.round(stats.avg_processing_seconds / 60)} <span className="text-sm font-normal text-gray-400">minutes</span>
                  </p>
                  <p className="text-xs text-gray-500 mt-2 leading-relaxed">
                    Ingestion through SKU normalization, AI agent mapping, and 6-phase validation.
                  </p>
                </div>
                <div className="pt-4 border-t border-white/10 mt-6 flex items-center justify-between text-xs">
                  <span className="text-gray-400">Execution Mode</span>
                  <span className="font-mono text-[#00C2BB] bg-[#00C2BB]/10 px-2 py-0.5 rounded border border-[#00C2BB]/20">6-Phase Pipeline</span>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </Layout>
  )
}

/* ── Building blocks ── */

function SectionHeading({ icon, title }: Readonly<{ icon: React.ReactNode; title: string }>) {
  return (
    <div className="flex items-center gap-2 mb-4">
      {icon}
      <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">{title}</h2>
      <div className="flex-1 h-px bg-white/10" />
    </div>
  )
}

function BigStat({
  label, value, icon, color = 'text-white', helper, emphasis,
}: Readonly<{ label: string; value: string | number; icon: React.ReactNode; color?: string; helper?: string; emphasis?: boolean }>) {
  return (
    <div className={`rounded-xl p-4 card-lift shadow-sm border ${emphasis ? 'bg-[#645DF6]/[0.07] border-[#645DF6]/25' : 'bg-white/[0.02] border-white/10'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wider text-gray-400 font-medium">{label}</span>
        <div className="w-7 h-7 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">{icon}</div>
      </div>
      <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
      {helper && <p className="text-[11px] text-gray-500 mt-1">{helper}</p>}
    </div>
  )
}

function ChartCard({
  icon, title, badge, children, className = '',
}: Readonly<{ icon: React.ReactNode; title: string; badge?: string; children: React.ReactNode; className?: string }>) {
  return (
    <div className={`bg-white/[0.02] border border-white/10 rounded-xl p-5 shadow-sm ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          {icon}
          <h3 className="text-xs font-semibold text-gray-200 uppercase tracking-wider">{title}</h3>
        </div>
        {badge && <span className="text-[11px] text-gray-500 font-mono">{badge}</span>}
      </div>
      {children}
    </div>
  )
}

function EmptyChart({ text }: Readonly<{ text: string }>) {
  return <div className="h-[220px] flex items-center justify-center text-xs text-gray-500">{text}</div>
}

/* ── Icons (Lucide-standard) ── */
function LayersIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>
}
function CheckCircleIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
}
function AlertTriangleIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
}
function PercentIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 7a2 2 0 10-4 0 2 2 0 004 0zM19 17a2 2 0 10-4 0 2 2 0 004 0zM19 5L5 19" /></svg>
}
function ActivityIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>
}
function ClockIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
}
function RefreshIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
}
function TrendingDownIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M13 17h8m0 0v-8m0 8l-8-8-4 4-6-6" /></svg>
}
function CloudIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 15a4 4 0 004 4h9a5 5 0 10-.1-9.999 5.5 5.5 0 00-10.797 1.5A4 4 0 003 15z" /></svg>
}
function PiggyIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 8c-3.866 0-7 2.239-7 5 0 1.44.85 2.74 2.22 3.66L7 19l2.5-1a9 9 0 002.5.35c3.866 0 7-2.24 7-5s-3.134-5-7-5z M8.5 12h.01 M16 10l1-2" /></svg>
}
function BarsIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 6h10M4 12h16M4 18h7" /></svg>
}
function TrophyIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M8 21h8m-4-4v4m-5-16h10v3a5 5 0 01-10 0V5zM7 5H4v2a3 3 0 003 3m10-5h3v2a3 3 0 01-3 3" /></svg>
}
function ServerIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M5 12V7a2 2 0 012-2h10a2 2 0 012 2v5M5 12h14M5 12a2 2 0 00-2 2v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 00-2-2M8 16h.01" /></svg>
}
