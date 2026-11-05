import { useEffect, useRef, useState } from 'react'
import { Layout } from '../components/Layout'
import {
  getAdminHealth,
  getCatalogRefreshStatus,
  triggerCatalogRefresh,
  type CatalogRefreshRun,
  type HealthSummary,
  type StoreHealth,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'

export function AdminHealth({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('System Health — Facets Admin')
  const [health, setHealth] = useState<HealthSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [refreshRun, setRefreshRun] = useState<CatalogRefreshRun | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  const pollTimerRef = useRef<number | null>(null)
  const elapsedTimerRef = useRef<number | null>(null)

  const stopPolling = () => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    if (elapsedTimerRef.current !== null) {
      clearInterval(elapsedTimerRef.current)
      elapsedTimerRef.current = null
    }
  }

  const startPolling = (initialStartedAt?: string) => {
    stopPolling()

    const startTime = initialStartedAt ? new Date(initialStartedAt).getTime() : Date.now()
    const updateElapsed = () => {
      const diffSec = Math.max(0, Math.floor((Date.now() - startTime) / 1000))
      setElapsedSeconds(diffSec)
    }
    updateElapsed()
    elapsedTimerRef.current = window.setInterval(updateElapsed, 1000)

    const checkStatus = async () => {
      try {
        const run = await getCatalogRefreshStatus()
        setRefreshRun(run)
        if (run.state !== 'running') {
          stopPolling()
          setRefreshing(false)
          // Refresh telemetry metrics after terminal refresh state
          loadData(false)
        }
      } catch (err) {
        console.error('Failed to poll refresh status', err)
      }
    }

    // Poll every 2 seconds
    pollTimerRef.current = window.setInterval(checkStatus, 2000)
  }

  const loadData = async (showLoadingState = true) => {
    if (showLoadingState) setLoading(true)
    setError(null)
    try {
      const data = await getAdminHealth()
      setHealth(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load system health')
    } finally {
      if (showLoadingState) setLoading(false)
    }
  }

  useEffect(() => {
    loadData()

    // Check refresh state on mount to resume polling if a refresh is active
    getCatalogRefreshStatus()
      .then((run) => {
        setRefreshRun(run)
        if (run.state === 'running') {
          setRefreshing(true)
          startPolling(run.started_at)
        }
      })
      .catch((err) => {
        console.error('Failed to get initial refresh status', err)
      })

    return () => {
      stopPolling()
    }
  }, [])

  const handleRefreshCatalog = async () => {
    setRefreshing(true)
    setRefreshError(null)
    setShowLogs(false)
    setElapsedSeconds(0)

    try {
      await triggerCatalogRefresh()
      startPolling()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Refresh request failed'
      setRefreshError(msg)
      setRefreshing(false)
      stopPolling()
      // Still fetch status to display failure/skipped record if created
      try {
        const run = await getCatalogRefreshStatus()
        setRefreshRun(run)
      } catch {
        // ignore
      }
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes <= 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(1024))
    const formatted = (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)
    return `${formatted} ${units[i]}`
  }

  const statusPillClass = (status: string) => {
    switch (status) {
      case 'ok':
        return 'text-emerald-500 bg-emerald-500/10 border-emerald-500/30'
      case 'warn':
      case 'stale':
        return 'text-amber-500 bg-amber-500/10 border-amber-500/30'
      case 'fail':
        return 'text-rose-500 bg-rose-500/10 border-rose-500/30'
      default:
        return 'text-gray-400 bg-white/5 border-white/10'
    }
  }

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <BackButton fallback="/admin" />

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mt-2 mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
              System Health & Telemetry
            </h1>
            <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1 max-w-2xl">
              High-cardinality engine telemetry, live GCP catalog refresh monitoring, data dependency integrity, and aggregate AI token usage.
            </p>
          </div>
          <button
            onClick={() => loadData(true)}
            disabled={loading}
            className="self-start sm:self-auto px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer flex items-center gap-2"
          >
            <span className={`inline-block ${loading ? 'animate-spin' : ''}`}>↻</span>
            Refresh Telemetry
          </button>
        </div>

        {error && (
          <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">Telemetry Error</strong>
            <p className="text-xs text-rose-300">{error}</p>
          </div>
        )}

        {loading && !health && (
          <div className="py-20 text-center text-sm text-[color:var(--fcp-text-tertiary)]">
            Loading system health telemetry...
          </div>
        )}

        {health && (
          <div className="space-y-6">
            {/* Top Summary KPI Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Reasoning Engine
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] font-mono truncate" title={health.engine.active_engine}>
                  {health.engine.active_engine}
                </div>
                <div className="text-xs text-emerald-500 font-medium mt-1 flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block animate-pulse" />
                  Healthy (v{health.engine.version})
                </div>
              </div>

              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Catalog Cardinality
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] tabular-nums">
                  {health.catalog.sku_count > 0 ? health.catalog.sku_count.toLocaleString() : 'N/A'} SKUs
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1">
                  Across {health.catalog.service_count} GCP Services
                </div>
              </div>

              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Data Dependencies
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] flex items-center gap-2">
                  <span className={`text-xs px-2 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(health.data_status)}`}>
                    {health.data_status}
                  </span>
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1">
                  {health.stores ? `${health.stores.filter(s => s.status === 'ok').length}/${health.stores.length} stores healthy` : 'Checking stores'}
                </div>
              </div>

              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Pipeline Health
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] tabular-nums">
                  {health.pipeline.failure_rate_pct.toFixed(1)}% Failure Rate
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1">
                  {health.pipeline.completed_jobs} completed, {health.pipeline.failed_jobs} failed
                </div>
              </div>

              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Estimated AI Spend
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] tabular-nums">
                  ${health.tokens.estimated_cost_usd.toFixed(2)} USD
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1">
                  ~{(health.tokens.total_tokens_est / 1000).toFixed(0)}k tokens consumed
                </div>
              </div>
            </div>

            {/* Deep Breakdown Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Engine & Pipeline Architecture */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)] flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                      Engine Architecture & Pipeline
                    </h2>
                    <span className="text-xs font-mono px-2 py-0.5 rounded bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-secondary)]">
                      Concurrency: {health.engine.concurrency_limit}
                    </span>
                  </div>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mb-4 leading-relaxed">
                    Every bill undergoes a 6-phase transformation pipeline combining deterministic normalizers, DuckDB analytics, and LLM SKU classification.
                  </p>
                  <div className="space-y-2">
                    {health.engine.phases.map((phase, idx) => (
                      <div
                        key={phase}
                        className="flex items-center justify-between text-xs px-3 py-2 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]"
                      >
                        <span className="text-[color:var(--fcp-text-primary)] font-medium">{phase}</span>
                        <span className="text-[11px] text-[color:var(--fcp-text-tertiary)] font-mono">Phase {idx + 1}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="mt-6 pt-4 border-t border-[var(--fcp-border-subtle)] flex items-center justify-between text-xs">
                  <span className="text-[color:var(--fcp-text-tertiary)]">Job execution queue depth</span>
                  <span className="font-semibold text-[color:var(--fcp-text-primary)]">{health.pipeline.queue_depth} jobs queued</span>
                </div>
              </div>

              {/* GCP Catalog Status & Live Refresh Tracker */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)] flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                      GCP Pricing Catalog Status & Refresh
                    </h2>
                    <span
                      className={`text-xs px-2.5 py-0.5 rounded-full border font-medium ${statusPillClass(
                        health.catalog.status
                      )}`}
                    >
                      {health.catalog.status === 'stale'
                        ? 'Stale Catalog'
                        : health.catalog.status === 'ok'
                        ? 'Catalog Synchronized'
                        : health.catalog.status.toUpperCase()}
                    </span>
                  </div>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mb-4 leading-relaxed">
                    Deterministic pricing queries local indexed GCP SKUs. Refresh queries Google Cloud Pricing API, integrity-gates changes, and updates local DuckDB tables.
                  </p>

                  <div className="grid grid-cols-2 gap-3 mb-4">
                    <div className="p-3 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                      <div className="text-[10px] text-[color:var(--fcp-text-tertiary)] uppercase font-semibold">Total SKUs</div>
                      <div className="text-base font-bold text-[color:var(--fcp-text-primary)] mt-0.5">
                        {health.catalog.sku_count > 0 ? health.catalog.sku_count.toLocaleString() : 'Pending'}
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                      <div className="text-[10px] text-[color:var(--fcp-text-tertiary)] uppercase font-semibold">Tracked Services</div>
                      <div className="text-base font-bold text-[color:var(--fcp-text-primary)] mt-0.5">
                        {health.catalog.service_count}
                      </div>
                    </div>
                  </div>

                  <div className="text-xs text-[color:var(--fcp-text-secondary)] space-y-1 mb-4 font-mono text-[11px]">
                    <div>Catalog Stamp: {health.catalog.vintage || 'Local Build'}</div>
                    <div>Vintage Age: {health.catalog.age_days >= 0 ? `${health.catalog.age_days} day(s) old` : 'Unknown'}</div>
                  </div>

                  {/* Live Refresh Status Banner */}
                  {refreshError && (
                    <div className="text-xs p-3 rounded-lg bg-rose-950/40 border border-rose-500/40 text-rose-300 mb-3">
                      <strong className="block font-semibold mb-0.5">Trigger Failed</strong>
                      {refreshError}
                    </div>
                  )}

                  {refreshRun && refreshRun.state !== 'idle' && (
                    <div className="mb-4 p-3.5 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-xs">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-semibold text-[color:var(--fcp-text-primary)] flex items-center gap-2">
                          {refreshRun.state === 'running' && (
                            <>
                              <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-ping inline-block" />
                              Refresh In Progress
                            </>
                          )}
                          {refreshRun.state === 'success' && (
                            <>
                              <span className="text-emerald-500 font-bold">✓</span>
                              Catalog Refresh Succeeded
                            </>
                          )}
                          {refreshRun.state === 'failed' && (
                            <>
                              <span className="text-rose-500 font-bold">✕</span>
                              Catalog Refresh Failed
                            </>
                          )}
                          {refreshRun.state === 'skipped' && (
                            <>
                              <span className="text-amber-500 font-bold">⚠</span>
                              Catalog Refresh Skipped
                            </>
                          )}
                        </span>
                        <span className="font-mono text-[11px] text-[color:var(--fcp-text-tertiary)]">
                          {refreshRun.state === 'running'
                            ? `Elapsed: ${Math.floor(elapsedSeconds / 60)}:${(elapsedSeconds % 60).toString().padStart(2, '0')}`
                            : refreshRun.duration_seconds !== undefined
                            ? `Duration: ${refreshRun.duration_seconds}s`
                            : ''}
                        </span>
                      </div>

                      {refreshRun.actor && (
                        <div className="text-[11px] text-[color:var(--fcp-text-secondary)] mb-1">
                          Initiated by: <span className="font-mono text-[color:var(--fcp-text-primary)]">{refreshRun.actor}</span> ({refreshRun.trigger || 'manual'})
                        </div>
                      )}

                      {refreshRun.reason && (
                        <div className="text-[11px] text-amber-400 mb-2 font-mono">
                          Note: {refreshRun.reason}
                        </div>
                      )}

                      {refreshRun.log_tail && refreshRun.log_tail.length > 0 && (
                        <div className="mt-2.5 pt-2 border-t border-[var(--fcp-border-subtle)]">
                          <button
                            onClick={() => setShowLogs((v) => !v)}
                            className="text-[11px] font-medium text-[color:var(--fcp-accent)] hover:underline cursor-pointer flex items-center gap-1 mb-1.5"
                          >
                            <span>{showLogs ? '▼ Hide subprocess log output' : '▶ Show subprocess log tail'}</span>
                            <span className="text-[color:var(--fcp-text-tertiary)]">({refreshRun.log_tail.length} lines)</span>
                          </button>
                          {showLogs && (
                            <pre className="p-2.5 rounded bg-black/60 border border-white/10 text-[10px] font-mono text-gray-300 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text">
                              {refreshRun.log_tail.join('\n')}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="pt-4 border-t border-[var(--fcp-border-subtle)] flex items-center justify-between">
                  <span className="text-xs text-[color:var(--fcp-text-secondary)] font-medium">
                    Integrated GCP Pricing Catalog
                  </span>
                  <button
                    onClick={handleRefreshCatalog}
                    disabled={refreshing || refreshRun?.state === 'running'}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:bg-emerald-800 disabled:opacity-50 text-white text-xs font-medium transition-colors cursor-pointer flex items-center gap-2"
                  >
                    {(refreshing || refreshRun?.state === 'running') && (
                      <span className="inline-block w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    )}
                    {refreshing || refreshRun?.state === 'running' ? 'Refreshing Catalog...' : 'Trigger Catalog Refresh'}
                  </button>
                </div>
              </div>
            </div>

            {/* Data Dependencies Panel */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)]">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
                <div>
                  <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                    Data Stores &amp; Dependencies Health
                  </h2>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                    Critical JSON configuration registries, SKU archives, exchange rates, and DuckDB pricing artifacts verified on disk.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-[color:var(--fcp-text-tertiary)]">Roll-up:</span>
                  <span className={`text-xs px-2.5 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(health.data_status)}`}>
                    {health.data_status}
                  </span>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-tertiary)] font-semibold uppercase text-[10px] tracking-wider">
                      <th className="pb-2.5">Store Key</th>
                      <th className="pb-2.5">File Path</th>
                      <th className="pb-2.5">Status</th>
                      <th className="pb-2.5 text-right">Items / Rows</th>
                      <th className="pb-2.5 text-right">Size</th>
                      <th className="pb-2.5 text-right">Age</th>
                      <th className="pb-2.5 pl-4">Integrity Detail</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                    {health.stores && health.stores.map((store: StoreHealth) => (
                      <tr key={store.key} className="hover:bg-[var(--fcp-hover-overlay)] transition-colors">
                        <td className="py-2.5 font-mono text-[11px] text-[color:var(--fcp-text-primary)] font-medium">
                          {store.key}
                        </td>
                        <td className="py-2.5 text-[color:var(--fcp-text-secondary)] font-mono text-[11px]">
                          data/{store.file}
                        </td>
                        <td className="py-2.5">
                          <span className={`inline-block text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(store.status)}`}>
                            {store.status}
                          </span>
                        </td>
                        <td className="py-2.5 text-right font-mono text-[11px] tabular-nums text-[color:var(--fcp-text-primary)]">
                          {store.count > 0 ? store.count.toLocaleString() : '—'}
                        </td>
                        <td className="py-2.5 text-right font-mono text-[11px] tabular-nums text-[color:var(--fcp-text-secondary)]">
                          {store.size_bytes > 0 ? formatBytes(store.size_bytes) : '—'}
                        </td>
                        <td className="py-2.5 text-right font-mono text-[11px] tabular-nums text-[color:var(--fcp-text-secondary)]">
                          {store.age_days >= 0 ? `${store.age_days}d` : '—'}
                        </td>
                        <td className="py-2.5 pl-4 text-[11px] text-[color:var(--fcp-text-secondary)]">
                          {store.detail}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
