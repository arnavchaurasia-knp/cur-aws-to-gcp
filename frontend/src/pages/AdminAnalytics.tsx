import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  getAnalytics,
  getAnalyticsComposition,
  getAnalyticsQuality,
  getAnalyticsMethodology,
  rebuildAnalytics,
} from '../api/admin'
import type {
  UsageStats,
  CompositionResult,
  QualityResult,
  MethodologyResult,
  AnalyticsFilter,
} from '../api/admin'
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
const PASS_COLOR = '#EAB308'  // yellow-amber — passthrough
const MUTED_COLOR = '#64748B' // slate — ignored/other

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

type TabType = 'economics' | 'composition' | 'quality' | 'methodology'
type BasisType = 'od' | '1yr' | '3yr'
type WindowType = '30d' | '90d' | 'all'
type DimensionType = 'service' | 'charge_type' | 'decision_type' | 'region'

export function AdminAnalytics({ user }: Readonly<{ user: UserInfo }>) {
  const axisColor = 'var(--fcp-text-tertiary)'
  const gridColor = 'var(--fcp-border-subtle)'

  useTitle('Corpus Analytics — admin')
  const nav = useNavigate()

  // Controls state
  const [activeTab, setActiveTab] = useState<TabType>('economics')
  const [basis, setBasis] = useState<BasisType>('od')
  const [windowVal, setWindowVal] = useState<WindowType>('30d')
  const [minBill, setMinBill] = useState<number>(0)
  const [dimension, setDimension] = useState<DimensionType>('service')

  // Data states
  const [stats, setStats] = useState<UsageStats | null>(null)
  const [composition, setComposition] = useState<CompositionResult | null>(null)
  const [quality, setQuality] = useState<QualityResult | null>(null)
  const [methodology, setMethodology] = useState<MethodologyResult | null>(null)

  const [loading, setLoading] = useState(true)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<AdminApiErrorState | null>(null)

  const getFilter = (): AnalyticsFilter => ({
    window: windowVal,
    min_bill: minBill > 0 ? minBill : undefined,
  })

  const loadData = () => {
    setLoading(true)
    setLoadError(null)
    const filter = getFilter()

    Promise.all([
      getAnalytics(filter),
      getAnalyticsComposition(dimension, filter),
      getAnalyticsQuality(filter),
      getAnalyticsMethodology(filter),
    ])
      .then(([s, comp, q, m]) => {
        setStats(s)
        setComposition(comp)
        setQuality(q)
        setMethodology(m)
      })
      .catch(err => setLoadError(parseAdminApiError(err, 'Corpus Analytics', () => nav('/login'))))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadData()
  }, [windowVal, minBill])

  useEffect(() => {
    if (activeTab === 'composition') {
      getAnalyticsComposition(dimension, getFilter())
        .then(res => setComposition(res))
        .catch(console.error)
    }
  }, [dimension])

  const handleRebuild = async () => {
    if (rebuilding) return
    setRebuilding(true)
    setRebuildMsg(null)
    try {
      const res = await rebuildAnalytics()
      setRebuildMsg(`Corpus rebuild complete: ${res.rebuilt_jobs} bills materialized.`)
      loadData()
    } catch (e: any) {
      setRebuildMsg(`Rebuild error: ${e?.message ?? 'unknown'}`)
    } finally {
      setRebuilding(false)
    }
  }

  // Chart data formatting
  const volumeData = (stats?.jobs_by_day ?? []).map(d => ({ day: d.day.slice(5), count: d.count }))
  const spendData = (stats?.spend_by_day ?? []).map(d => ({ day: d.day.slice(5), AWS: d.aws, GCP: d.gcp }))
  const buckets = stats?.savings_buckets ?? []
  const prospects = stats?.top_prospects ?? []
  const hasEconomics = (stats?.analyzed_jobs ?? 0) > 0

  // Basis-dependent calculations
  const effectiveGCP = basis === '1yr'
    ? (stats?.total_gcp_1yr_cud ?? stats?.total_gcp_od ?? 0)
    : basis === '3yr'
    ? (stats?.total_gcp_3yr_cud ?? stats?.total_gcp_od ?? 0)
    : (stats?.total_gcp_od ?? 0)

  const effectiveSavings = (stats?.total_aws_spend ?? 0) - effectiveGCP
  const effectiveSavingsPct = (stats?.total_aws_spend ?? 0) > 0
    ? (effectiveSavings / (stats?.total_aws_spend ?? 1)) * 100
    : 0

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-10">
        <BackButton fallback="/admin" />

        {/* Top Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-semibold mb-1">Corpus Analytics Platform</h1>
            <p className="text-sm text-gray-500">
              Cross-bill intelligence: portfolio deal economics, spend composition, accuracy quality, and methodology.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRebuild}
              disabled={rebuilding || loading}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-200 transition-colors cursor-pointer disabled:opacity-50"
              title="Re-read duckdb and rebuild all rollups"
            >
              <DatabaseIcon className={`w-3.5 h-3.5 ${rebuilding ? 'animate-spin' : ''}`} />
              {rebuilding ? 'Rebuilding…' : 'Rebuild Corpus'}
            </button>
            <button
              onClick={loadData}
              disabled={loading}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-200 transition-colors cursor-pointer disabled:opacity-50"
            >
              <RefreshIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {rebuildMsg && (
          <div className="mb-6 p-3 rounded-xl bg-[#00C2BB]/10 border border-[#00C2BB]/30 text-xs text-[#00C2BB] flex items-center justify-between">
            <span>{rebuildMsg}</span>
            <button onClick={() => setRebuildMsg(null)} className="text-gray-400 hover:text-white ml-2">✕</button>
          </div>
        )}

        {/* Global Controls Row */}
        <div className="flex flex-wrap items-center justify-between gap-3 p-3 mb-6 bg-white/[0.02] border border-white/10 rounded-xl">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium text-gray-400 uppercase tracking-wider mr-1">Time Window:</span>
            {(['30d', '90d', 'all'] as WindowType[]).map(w => (
              <button
                key={w}
                onClick={() => setWindowVal(w)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  windowVal === w
                    ? 'bg-[#645DF6] text-white shadow-sm'
                    : 'bg-white/5 hover:bg-white/10 text-gray-300'
                }`}
              >
                {w === '30d' ? 'Last 30 Days' : w === '90d' ? 'Last 90 Days' : 'All Time'}
              </button>
            ))}

            <div className="h-4 w-px bg-white/10 mx-2" />

            <span className="text-[11px] font-medium text-gray-400 uppercase tracking-wider mr-1">Min Bill:</span>
            {[
              { label: 'All', val: 0 },
              { label: '≥ $1K', val: 1000 },
              { label: '≥ $10K', val: 10000 },
              { label: '≥ $50K', val: 50000 },
            ].map(m => (
              <button
                key={m.label}
                onClick={() => setMinBill(m.val)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  minBill === m.val
                    ? 'bg-white/20 text-white font-semibold'
                    : 'bg-white/5 hover:bg-white/10 text-gray-400'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-gray-400 uppercase tracking-wider mr-1">Basis:</span>
            {[
              { key: 'od', label: 'On-Demand' },
              { key: '1yr', label: '1-Yr CUD' },
              { key: '3yr', label: '3-Yr CUD' },
            ].map(b => (
              <button
                key={b.key}
                onClick={() => setBasis(b.key as BasisType)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  basis === b.key
                    ? 'bg-[#00C2BB] text-black font-semibold'
                    : 'bg-white/5 hover:bg-white/10 text-gray-300'
                }`}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>

        {/* Four Lens Tabs */}
        <div className="flex border-b border-white/10 mb-8 gap-2">
          {[
            { id: 'economics', label: 'Deal Economics', icon: <TrendingDownIcon className="w-4 h-4" /> },
            { id: 'composition', label: 'Spend Composition', icon: <LayersIcon className="w-4 h-4" /> },
            { id: 'quality', label: 'Projection Quality', icon: <CheckCircleIcon className="w-4 h-4" /> },
            { id: 'methodology', label: 'Methodology & Trust', icon: <ShieldIcon className="w-4 h-4" /> },
          ].map(t => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id as TabType)}
              className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all cursor-pointer ${
                activeTab === t.id
                  ? 'border-[#00C2BB] text-[#00C2BB] bg-[#00C2BB]/5 rounded-t-lg'
                  : 'border-transparent text-gray-400 hover:text-gray-200 hover:border-white/20'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
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
            <p className="text-xs text-gray-500 mt-1">Analytics will appear once bills are ingested and projected.</p>
          </div>
        )}

        {!loading && !loadError && stats && stats.total_jobs > 0 && (
          <>
            {/* ───────── Lens A: Deal Economics ───────── */}
            {activeTab === 'economics' && (
              <div>
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
                        label={basis === '1yr' ? 'Projected GCP (1-Yr CUD)' : basis === '3yr' ? 'Projected GCP (3-Yr CUD)' : 'Projected GCP (On-Demand)'}
                        value={fmtMoney(effectiveGCP)}
                        helper={basis === 'od' ? 'List rate equivalent' : `${basis.toUpperCase()} commitment basis`}
                        color="text-[#00C2BB]"
                        icon={<CloudIcon className="w-4 h-4 text-[#00C2BB]" />}
                      />
                      <BigStat
                        label="Portfolio Savings"
                        value={fmtMoney(effectiveSavings)}
                        helper={`${effectiveSavingsPct.toFixed(1)}% reduction (${basis.toUpperCase()})`}
                        color={effectiveSavings >= 0 ? 'text-[#645DF6]' : 'text-orange-400'}
                        icon={<PiggyIcon className="w-4 h-4 text-[#645DF6]" />}
                        emphasis
                      />
                      <BigStat
                        label="Win-Rate (Savings > 0)"
                        value={`${(stats.win_rate ?? 0).toFixed(0)}%`}
                        helper={`${(stats.win_rate_20 ?? 0).toFixed(0)}% bills save ≥ 20%`}
                        color="text-white"
                        icon={<TrophyIcon className="w-4 h-4 text-emerald-400" />}
                      />
                    </div>

                    {/* Secondary Metrics Bar */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8 p-3 bg-white/[0.02] border border-white/10 rounded-xl text-xs">
                      <div>
                        <span className="text-gray-500 block mb-0.5">Avg. Savings / Bill</span>
                        <span className="text-white font-semibold tabular-nums">{stats.avg_savings_pct.toFixed(1)}%</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-0.5">Avg. Bill Size</span>
                        <span className="text-white font-semibold tabular-nums">{fmtMoney(stats.avg_bill_size)}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-0.5">Portfolio Coverage</span>
                        <span className="text-teal-400 font-semibold tabular-nums">{(stats.portfolio_coverage_pct ?? 100).toFixed(1)}%</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block mb-0.5">Unmapped / Review Spend</span>
                        <span className="text-amber-400 font-semibold tabular-nums">{fmtMoney(stats.total_unmapped_usd ?? 0)}</span>
                      </div>
                    </div>

                    <div className="grid gap-6 lg:grid-cols-3 mb-8">
                      {/* AWS vs GCP spend over time */}
                      <ChartCard
                        className="lg:col-span-2"
                        icon={<ActivityIcon className="w-4 h-4 text-[#00C2BB]" />}
                        title="AWS vs Projected GCP Spend"
                        badge={windowVal === 'all' ? 'All Time' : windowVal.toUpperCase()}
                      >
                        {spendData.length === 0 ? (
                          <EmptyChart text="No priced bills in this window." />
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
                        badge="Histogram"
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
                              <th className="text-right font-medium py-2 px-1">GCP ({basis.toUpperCase()})</th>
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

                {/* Pipeline Health footnote */}
                <SectionHeading icon={<ServerIcon className="w-4 h-4 text-gray-400" />} title="Pipeline Volume Footnote" />
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
                      <div style={{ width: '100%', height: 200 }}>
                        <ResponsiveContainer>
                          <BarChart data={volumeData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                            <XAxis dataKey="day" stroke={axisColor} fontSize={11} tickLine={false} axisLine={{ stroke: gridColor }} />
                            <YAxis stroke={axisColor} fontSize={11} allowDecimals={false} tickLine={false} axisLine={{ stroke: gridColor }} />
                            <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} contentStyle={tooltipStyle} />
                            <Bar dataKey="count" fill={SAVE_COLOR} radius={[4, 4, 0, 0]} maxBarSize={32} />
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
              </div>
            )}

            {/* ───────── Lens B: Spend Composition ───────── */}
            {activeTab === 'composition' && (
              <div>
                <div className="flex items-center justify-between gap-3 mb-6">
                  <div className="flex items-center gap-2">
                    {[
                      { key: 'service', label: 'By Service' },
                      { key: 'charge_type', label: 'By Charge Type' },
                      { key: 'decision_type', label: 'By Strategy' },
                      { key: 'region', label: 'By Region' },
                    ].map(d => (
                      <button
                        key={d.key}
                        onClick={() => setDimension(d.key as DimensionType)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
                          dimension === d.key
                            ? 'bg-[#00C2BB] text-black font-semibold'
                            : 'bg-white/5 hover:bg-white/10 text-gray-300'
                        }`}
                      >
                        {d.label}
                      </button>
                    ))}
                  </div>
                  <span className="text-xs text-gray-500">
                    Showing {composition?.items.length ?? 0} breakdown categories
                  </span>
                </div>

                {/* Top Composition Bar Chart */}
                <ChartCard
                  icon={<BarsIcon className="w-4 h-4 text-[#00C2BB]" />}
                  title={`Top Spend & Savings (${dimension.replace('_', ' ').toUpperCase()})`}
                  className="mb-8"
                >
                  {(!composition || composition.items.length === 0) ? (
                    <EmptyChart text="No composition data found." />
                  ) : (
                    <div style={{ width: '100%', height: 280 }}>
                      <ResponsiveContainer>
                        <BarChart
                          data={composition.items.slice(0, 10).map(it => ({
                            name: it.key.length > 20 ? it.key.slice(0, 20) + '…' : it.key,
                            AWS: it.aws_usd,
                            GCP: it.gcp_usd,
                            Savings: it.savings_usd,
                          }))}
                          margin={{ top: 10, right: 10, left: 0, bottom: 25 }}
                        >
                          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                          <XAxis dataKey="name" stroke={axisColor} fontSize={10} tickLine={false} interval={0} angle={-15} textAnchor="end" />
                          <YAxis stroke={axisColor} fontSize={11} tickLine={false} tickFormatter={(v: number) => fmtMoney(v)} />
                          <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => fmtMoneyFull(Number(v))} />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          <Bar dataKey="AWS" fill={AWS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={20} />
                          <Bar dataKey="GCP" fill={GCP_COLOR} radius={[3, 3, 0, 0]} maxBarSize={20} />
                          <Bar dataKey="Savings" fill={SAVE_COLOR} radius={[3, 3, 0, 0]} maxBarSize={20} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </ChartCard>

                {/* Composition Table */}
                <div className="bg-white/[0.02] border border-white/10 rounded-xl p-5 mb-8">
                  <h3 className="text-xs font-semibold text-gray-200 uppercase tracking-wider mb-4">Detailed Breakdown</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-[11px] uppercase tracking-wider text-gray-500 border-b border-white/10">
                          <th className="text-left font-medium py-2 px-2">Category / Key</th>
                          <th className="text-right font-medium py-2 px-2">AWS Spend</th>
                          <th className="text-right font-medium py-2 px-2">Share of AWS</th>
                          <th className="text-right font-medium py-2 px-2">Projected GCP</th>
                          <th className="text-right font-medium py-2 px-2">Savings ($)</th>
                          <th className="text-right font-medium py-2 px-2">Savings (%)</th>
                          <th className="text-right font-medium py-2 px-2">Items</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(composition?.items ?? []).map(it => (
                          <tr key={it.key} className="border-b border-white/5 hover:bg-white/[0.02]">
                            <td className="py-2.5 px-2 font-medium text-gray-200">{it.key || '—'}</td>
                            <td className="py-2.5 px-2 text-right tabular-nums text-gray-300 font-semibold">{fmtMoney(it.aws_usd)}</td>
                            <td className="py-2.5 px-2 text-right tabular-nums text-gray-400">{it.pct_of_aws.toFixed(1)}%</td>
                            <td className="py-2.5 px-2 text-right tabular-nums text-gray-400">{fmtMoney(it.gcp_usd)}</td>
                            <td className={`py-2.5 px-2 text-right tabular-nums font-semibold ${it.savings_usd >= 0 ? 'text-[#645DF6]' : 'text-orange-400'}`}>
                              {fmtMoney(it.savings_usd)}
                            </td>
                            <td className={`py-2.5 px-2 text-right tabular-nums ${it.savings_pct >= 0 ? 'text-teal-400' : 'text-orange-400'}`}>
                              {it.savings_pct.toFixed(1)}%
                            </td>
                            <td className="py-2.5 px-2 text-right tabular-nums text-gray-500">{it.rows}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Negative Savings Callout */}
                {composition && composition.negative_savings && composition.negative_savings.length > 0 && (
                  <div className="bg-orange-950/20 border border-orange-500/30 rounded-xl p-5 mb-8">
                    <div className="flex items-center gap-2 mb-3">
                      <AlertTriangleIcon className="w-4 h-4 text-orange-400" />
                      <h3 className="text-xs font-semibold text-orange-400 uppercase tracking-wider">
                        Services Costing More on GCP (Negative Savings)
                      </h3>
                    </div>
                    <p className="text-xs text-orange-300/80 mb-4">
                      These services cost more on GCP than on AWS under current mappings. Review to determine if alternative GCP SKUs, commitments, or architecture optimizations apply.
                    </p>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-400 border-b border-orange-500/20">
                            <th className="text-left py-1.5">Service</th>
                            <th className="text-right py-1.5">AWS Cost</th>
                            <th className="text-right py-1.5">GCP Cost</th>
                            <th className="text-right py-1.5">Premium / Delta</th>
                            <th className="text-right py-1.5">Ratio</th>
                          </tr>
                        </thead>
                        <tbody>
                          {composition.negative_savings.map(neg => (
                            <tr key={neg.key} className="border-b border-orange-500/10">
                              <td className="py-2 text-gray-200 font-medium">{neg.key}</td>
                              <td className="py-2 text-right tabular-nums text-gray-400">{fmtMoney(neg.aws_usd)}</td>
                              <td className="py-2 text-right tabular-nums text-gray-300">{fmtMoney(neg.gcp_usd)}</td>
                              <td className="py-2 text-right tabular-nums text-orange-400 font-semibold">{fmtMoney(neg.savings_usd)}</td>
                              <td className="py-2 text-right tabular-nums text-orange-300 font-mono">
                                {neg.aws_usd > 0 ? (neg.gcp_usd / neg.aws_usd).toFixed(2) : '—'}x
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ───────── Lens C: Projection Quality ───────── */}
            {activeTab === 'quality' && (
              <div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                  <BigStat
                    label="Mapping Coverage"
                    value={`${(quality?.coverage_pct ?? 100).toFixed(1)}%`}
                    helper="Identified GCP equivalents"
                    color="text-[#00C2BB]"
                    icon={<CheckCircleIcon className="w-4 h-4 text-[#00C2BB]" />}
                  />
                  <BigStat
                    label="Deterministic Pricing"
                    value={`${(quality?.deterministic_pct ?? 0).toFixed(1)}%`}
                    helper={`${quality?.deterministic_rows ?? 0} static/formula rows`}
                    color="text-teal-400"
                    icon={<ShieldIcon className="w-4 h-4 text-teal-400" />}
                  />
                  <BigStat
                    label="AI / LLM Assisted"
                    value={`${(quality?.llm_pct ?? 0).toFixed(1)}%`}
                    helper={`${quality?.llm_rows ?? 0} dynamically priced rows`}
                    color="text-[#645DF6]"
                    icon={<SparklesIcon className="w-4 h-4 text-[#645DF6]" />}
                  />
                  <BigStat
                    label="Unmapped Spend"
                    value={fmtMoney(quality?.total_unmapped_usd ?? 0)}
                    helper="Manual review or NULL"
                    color="text-amber-400"
                    icon={<AlertTriangleIcon className="w-4 h-4 text-amber-400" />}
                  />
                </div>

                {/* Strategy Distribution Progress Bar */}
                <div className="bg-white/[0.02] border border-white/10 rounded-xl p-5 mb-8">
                  <h3 className="text-xs font-semibold text-gray-200 uppercase tracking-wider mb-2">Strategy Row Share</h3>
                  <div className="flex h-3 rounded-full overflow-hidden bg-white/5 my-3">
                    <div style={{ width: `${quality?.deterministic_pct ?? 0}%`, background: GCP_COLOR }} title="Deterministic" />
                    <div style={{ width: `${quality?.llm_pct ?? 0}%`, background: SAVE_COLOR }} title="LLM" />
                    <div style={{ width: `${quality?.passthrough_pct ?? 0}%`, background: PASS_COLOR }} title="Passthrough" />
                    <div style={{ width: `${quality?.ignored_pct ?? 0}%`, background: MUTED_COLOR }} title="Ignored" />
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs text-gray-400 mt-2">
                    <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: GCP_COLOR }} /> Deterministic: {(quality?.deterministic_pct ?? 0).toFixed(1)}%</span>
                    <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: SAVE_COLOR }} /> LLM: {(quality?.llm_pct ?? 0).toFixed(1)}%</span>
                    <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: PASS_COLOR }} /> Passthrough: {(quality?.passthrough_pct ?? 0).toFixed(1)}%</span>
                    <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: MUTED_COLOR }} /> Ignored ($0): {(quality?.ignored_pct ?? 0).toFixed(1)}%</span>
                  </div>
                </div>

                <div className="grid gap-6 lg:grid-cols-2 mb-8">
                  {/* Confidence Distribution */}
                  <ChartCard icon={<ActivityIcon className="w-4 h-4 text-[#645DF6]" />} title="Confidence Weighted by Spend">
                    <div className="space-y-3">
                      {(quality?.confidence_distribution ?? []).map(c => (
                        <div key={c.band} className="p-3 bg-white/[0.02] rounded-lg border border-white/5 flex items-center justify-between">
                          <div>
                            <span className="capitalize font-medium text-gray-200 text-sm block">{c.band} Confidence</span>
                            <span className="text-xs text-gray-500">{c.count} bills</span>
                          </div>
                          <div className="text-right">
                            <span className="text-sm font-semibold tabular-nums text-white block">{fmtMoney(c.aws_usd)}</span>
                            <span className="text-xs text-gray-400">{c.pct_of_usd.toFixed(1)}% of spend</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </ChartCard>

                  {/* Findings Breakdown */}
                  <ChartCard icon={<AlertTriangleIcon className="w-4 h-4 text-amber-400 text-sm" />} title="Advisory Review Findings">
                    <div className="grid grid-cols-3 gap-3 mb-4">
                      <div className="p-3 rounded-lg bg-red-950/20 border border-red-500/20 text-center">
                        <span className="text-[11px] text-red-400 block font-medium">Critical / High</span>
                        <span className="text-2xl font-bold text-red-300">{quality?.findings_summary.high ?? 0}</span>
                      </div>
                      <div className="p-3 rounded-lg bg-amber-950/20 border border-amber-500/20 text-center">
                        <span className="text-[11px] text-amber-400 block font-medium">Medium</span>
                        <span className="text-2xl font-bold text-amber-300">{quality?.findings_summary.med ?? 0}</span>
                      </div>
                      <div className="p-3 rounded-lg bg-white/5 border border-white/10 text-center">
                        <span className="text-[11px] text-gray-400 block font-medium">Low</span>
                        <span className="text-2xl font-bold text-gray-300">{quality?.findings_summary.low ?? 0}</span>
                      </div>
                    </div>
                    <p className="text-xs text-gray-500">
                      Findings raised by the Phase-6 post-projection advisory reviewer across the corpus.
                    </p>
                  </ChartCard>
                </div>
              </div>
            )}

            {/* ───────── Lens D: Methodology & Trust ───────── */}
            {activeTab === 'methodology' && (
              <div>
                <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
                  <BigStat
                    label="Pricing Matrix Coverage"
                    value={`${(methodology?.matrix_coverage_pct ?? 0).toFixed(1)}%`}
                    helper="Audit-grade v1 rules"
                    color="text-[#00C2BB]"
                    icon={<ShieldIcon className="w-4 h-4 text-[#00C2BB]" />}
                  />
                  <BigStat
                    label="SP/RI Netting Exposure"
                    value={fmtMoney(methodology?.sp_ri_exposed_usd ?? 0)}
                    helper={`${(methodology?.sp_ri_exposed_pct ?? 0).toFixed(1)}% of analyzed spend`}
                    color="text-amber-400"
                    icon={<AlertTriangleIcon className="w-4 h-4 text-amber-400" />}
                  />
                  <BigStat
                    label="Review Loop Triage"
                    value={methodology?.review_loop_throughput.total_findings ?? 0}
                    helper={`${methodology?.review_loop_throughput.accepted ?? 0} accepted / resolved`}
                    color="text-[#645DF6]"
                    icon={<CheckCircleIcon className="w-4 h-4 text-[#645DF6]" />}
                  />
                </div>

                {/* SP/RI Netting Disclosure Card */}
                <div className="bg-amber-950/20 border border-amber-500/30 rounded-xl p-5 mb-8">
                  <div className="flex items-center gap-2 mb-2">
                    <ShieldIcon className="w-4 h-4 text-amber-400" />
                    <h3 className="text-xs font-semibold text-amber-300 uppercase tracking-wider">
                      Savings Plans & Reserved Instance Netting Exposure
                    </h3>
                  </div>
                  <p className="text-xs text-amber-200/80 leading-relaxed mb-3">
                    On AWS bills with heavy Savings Plans or Reserved Instances, the amortized cost reflects discounted rates that may underestimate true On-Demand equivalent usage. Across the analyzed portfolio, <strong className="text-white">{fmtMoney(methodology?.sp_ri_exposed_usd ?? 0)}</strong> ({((methodology?.sp_ri_exposed_pct ?? 0)).toFixed(1)}%) of spend originates from lines where amortized cost is lower than public list prices.
                  </p>
                  <div className="text-[11px] font-mono text-amber-300/70 bg-amber-950/40 p-2.5 rounded border border-amber-500/20">
                    Formula: Σ aws_amortized_cost WHERE (public_on_demand_cost &gt; aws_amortized_cost OR sp_ri_discount_pct &gt; 0)
                  </div>
                </div>

                <div className="grid gap-6 lg:grid-cols-2 mb-8">
                  {/* Self-Healing Review Loop Stats */}
                  <ChartCard icon={<ActivityIcon className="w-4 h-4 text-[#00C2BB]" />} title="Self-Healing Loop Throughput">
                    <div className="space-y-2.5 text-xs">
                      <div className="flex justify-between items-center p-2.5 bg-white/5 rounded-lg">
                        <span className="text-gray-300">Total Advisory Findings Raised</span>
                        <span className="font-semibold text-white tabular-nums">{methodology?.review_loop_throughput.total_findings ?? 0}</span>
                      </div>
                      <div className="flex justify-between items-center p-2.5 bg-teal-950/20 rounded-lg border border-teal-500/20">
                        <span className="text-teal-300">Accepted & Applied by Engine</span>
                        <span className="font-semibold text-teal-300 tabular-nums">{methodology?.review_loop_throughput.accepted ?? 0}</span>
                      </div>
                      <div className="flex justify-between items-center p-2.5 bg-amber-950/20 rounded-lg border border-amber-500/20">
                        <span className="text-amber-300">Pending Review / Triage</span>
                        <span className="font-semibold text-amber-300 tabular-nums">{methodology?.review_loop_throughput.pending ?? 0}</span>
                      </div>
                      <div className="flex justify-between items-center p-2.5 bg-white/5 rounded-lg">
                        <span className="text-gray-400">Rejected / Dismissed</span>
                        <span className="font-semibold text-gray-400 tabular-nums">
                          {(methodology?.review_loop_throughput.rejected ?? 0) + (methodology?.review_loop_throughput.dismissed ?? 0)}
                        </span>
                      </div>
                    </div>
                  </ChartCard>

                  {/* Engine & Assumptions Vintage */}
                  <ChartCard icon={<LayersIcon className="w-4 h-4 text-gray-400" />} title="Assumptions & Engine Vintages">
                    <div className="space-y-4">
                      <div>
                        <span className="text-[11px] uppercase tracking-wider text-gray-400 font-medium block mb-2">Engine Versions</span>
                        <div className="flex flex-wrap gap-2">
                          {(methodology?.engine_versions ?? []).map(ev => (
                            <span key={ev.version} className="px-2.5 py-1 bg-white/5 border border-white/10 rounded-md text-xs font-mono text-gray-300">
                              {ev.version}: <strong className="text-white">{ev.count}</strong>
                            </span>
                          ))}
                        </div>
                      </div>
                      <div>
                        <span className="text-[11px] uppercase tracking-wider text-gray-400 font-medium block mb-2">Assumptions Snapshots</span>
                        <div className="flex flex-wrap gap-2">
                          {(methodology?.assumptions_versions ?? []).map(av => (
                            <span key={av.version} className="px-2.5 py-1 bg-white/5 border border-white/10 rounded-md text-xs font-mono text-gray-300">
                              {av.version.slice(0, 10)}: <strong className="text-white">{av.count}</strong>
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  </ChartCard>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Layout>
  )
}

/* ── UI Building Blocks ── */

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

/* ── Icons ── */
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
function ShieldIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
}
function SparklesIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" /></svg>
}
function DatabaseIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" /></svg>
}
function ClockIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
}
