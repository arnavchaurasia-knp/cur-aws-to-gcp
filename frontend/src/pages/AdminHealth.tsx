import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  getAdminHealth,
  getCatalogRefreshStatus,
  triggerCatalogRefresh,
  testEngine,
  getDataStore,
  updateDataStore,
  revertDataStore,
  type CatalogRefreshRun,
  type HealthSummary,
  type StoreHealth,
  type EngineProbe,
  type DataStoreDetail,
  type DepHealth,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'
import { ConfirmDialog } from '../components/ConfirmDialog'

export function AdminHealth({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('System Health & Data Console — Facets Admin')
  const [health, setHealth] = useState<HealthSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Catalog refresh state
  const [refreshRun, setRefreshRun] = useState<CatalogRefreshRun | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  // Live engine test state
  const [testingEngine, setTestingEngine] = useState(false)
  const [engineTestResult, setEngineTestResult] = useState<EngineProbe | null>(null)
  const [engineTestError, setEngineTestError] = useState<string | null>(null)

  // Data Store Editor modal state
  const [activeStoreKey, setActiveStoreKey] = useState<string | null>(null)
  const [storeDetail, setStoreDetail] = useState<DataStoreDetail | null>(null)
  const [storeLoading, setStoreLoading] = useState(false)
  const [storeDraft, setStoreDraft] = useState('')
  const [storeSaveError, setStoreSaveError] = useState<string | null>(null)
  const [storeSaveSuccess, setStoreSaveSuccess] = useState<string | null>(null)
  const [storeSaving, setStoreSaving] = useState(false)
  const [confirmSaveOpen, setConfirmSaveOpen] = useState(false)
  const [confirmRevertOpen, setConfirmRevertOpen] = useState(false)
  const [storeReverting, setStoreReverting] = useState(false)

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
          loadData(false)
        }
      } catch (err) {
        console.error('Failed to poll refresh status', err)
      }
    }

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
      try {
        const run = await getCatalogRefreshStatus()
        setRefreshRun(run)
      } catch {
        // ignore
      }
    }
  }

  const handleTestEngine = async () => {
    setTestingEngine(true)
    setEngineTestError(null)
    setEngineTestResult(null)

    try {
      const probe = await testEngine()
      setEngineTestResult(probe)
      // Background reload telemetry to update cached health state
      loadData(false)
    } catch (err) {
      setEngineTestError(err instanceof Error ? err.message : 'Engine test round-trip failed')
    } finally {
      setTestingEngine(false)
    }
  }

  const handleOpenStore = async (key: string) => {
    setActiveStoreKey(key)
    setStoreLoading(true)
    setStoreSaveError(null)
    setStoreSaveSuccess(null)
    try {
      const detail = await getDataStore(key)
      setStoreDetail(detail)
      setStoreDraft(detail.content || '')
    } catch (err) {
      setStoreSaveError(err instanceof Error ? err.message : 'Failed to fetch datastore content')
    } finally {
      setStoreLoading(false)
    }
  }

  const handleCloseStore = () => {
    setActiveStoreKey(null)
    setStoreDetail(null)
    setStoreDraft('')
    setStoreSaveError(null)
    setStoreSaveSuccess(null)
  }

  const handleSaveStore = async () => {
    if (!activeStoreKey) return
    setStoreSaving(true)
    setStoreSaveError(null)
    setStoreSaveSuccess(null)

    // Validate JSON client-side first
    try {
      JSON.parse(storeDraft)
    } catch (err) {
      setStoreSaveError(err instanceof Error ? `Client validation failed: ${err.message}` : 'Malformed JSON syntax')
      setStoreSaving(false)
      setConfirmSaveOpen(false)
      return
    }

    try {
      const res = await updateDataStore(activeStoreKey, storeDraft)
      setStoreSaveSuccess(res.message || 'Datastore successfully saved with backup')
      setConfirmSaveOpen(false)
      // Refetch store detail to update size/backup count
      const updated = await getDataStore(activeStoreKey)
      setStoreDetail(updated)
      setStoreDraft(updated.content || '')
      // Reload overall telemetry
      loadData(false)
    } catch (err) {
      setStoreSaveError(err instanceof Error ? err.message : 'Failed to save datastore')
    } finally {
      setStoreSaving(false)
    }
  }

  const handleRevertStore = async () => {
    if (!activeStoreKey) return
    setStoreReverting(true)
    setStoreSaveError(null)
    setStoreSaveSuccess(null)

    try {
      const res = await revertDataStore(activeStoreKey)
      setStoreSaveSuccess(res.message || 'Restored to previous backup')
      setConfirmRevertOpen(false)
      // Reload store detail
      const updated = await getDataStore(activeStoreKey)
      setStoreDetail(updated)
      setStoreDraft(updated.content || '')
      loadData(false)
    } catch (err) {
      setStoreSaveError(err instanceof Error ? err.message : 'Failed to revert datastore')
    } finally {
      setStoreReverting(false)
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

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mt-2 mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
              System Health &amp; Data Console
            </h1>
            <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1 max-w-2xl">
              End-to-end telemetry, live model liveness testing, runtime dependency checks, and audit-backed datastore management.
            </p>
          </div>
          <button
            onClick={() => loadData(true)}
            disabled={loading}
            className="self-start sm:self-auto px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer flex items-center gap-2"
          >
            <span className={`inline-block ${loading ? 'animate-spin' : ''}`}>↻</span>
            Refresh Console
          </button>
        </div>

        {/* Global Pipeline Halted Banner */}
        {health?.pipeline.halted && (
          <div className="p-4 rounded-xl bg-rose-950/70 border-2 border-rose-500 text-rose-200 text-sm mb-6 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="w-3.5 h-3.5 rounded-full bg-rose-500 animate-ping inline-block" />
              <div>
                <strong className="block font-bold text-rose-100 text-base">PIPELINE EXECUTION HALTED</strong>
                <p className="text-xs text-rose-300 mt-0.5">
                  All automated job orchestration is blocked via global STOP_ALL state. New uploads will not process until resumed.
                </p>
              </div>
            </div>
            <Link
              to="/admin/jobs"
              className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold"
            >
              Manage in Admin Jobs →
            </Link>
          </div>
        )}

        {/* Top-Level Overall Status Roll-Up Banner */}
        {health && (
          <div className={`p-4 rounded-xl border mb-6 flex items-center justify-between ${
            health.status === 'fail'
              ? 'bg-rose-950/30 border-rose-500/40 text-rose-300'
              : health.status === 'warn'
              ? 'bg-amber-950/30 border-amber-500/40 text-amber-300'
              : 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300'
          }`}>
            <div className="flex items-center gap-3">
              <span className={`w-3 h-3 rounded-full ${
                health.status === 'fail'
                  ? 'bg-rose-500'
                  : health.status === 'warn'
                  ? 'bg-amber-500'
                  : 'bg-emerald-500'
              }`} />
              <div>
                <span className="text-xs uppercase font-bold tracking-wider">
                  Overall System State: {health.status === 'ok' ? 'Operational' : health.status === 'warn' ? 'Degraded' : 'Attention Required'}
                </span>
                <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                  {health.overall_reason || 'All pipeline modules, credentials, and data stores operating within target parameters.'}
                </p>
              </div>
            </div>
            <span className={`text-xs px-2.5 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(health.status)}`}>
              {health.status}
            </span>
          </div>
        )}

        {error && (
          <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">Telemetry Error</strong>
            <p className="text-xs text-rose-300">{error}</p>
          </div>
        )}

        {loading && !health && (
          <div className="py-20 text-center text-sm text-[color:var(--fcp-text-tertiary)]">
            Inspecting system health, credentials, and data dependencies...
          </div>
        )}

        {health && (
          <div className="space-y-6">
            {/* Top Summary KPI Cards (REAL METRICS) */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
              {/* Card 1: Reasoning Engine */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Reasoning Engine
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] font-mono truncate" title={health.engine.active_engine}>
                  {health.engine.active_engine}
                </div>
                <div className="text-xs mt-1 flex items-center gap-1.5 font-medium">
                  <span className={`w-2 h-2 rounded-full ${
                    health.engine.status === 'ok'
                      ? 'bg-emerald-500'
                      : health.engine.status === 'warn'
                      ? 'bg-amber-500'
                      : health.engine.status === 'fail'
                      ? 'bg-rose-500'
                      : 'bg-gray-400'
                  }`} />
                  <span className="capitalize">{health.engine.status}</span>
                  {health.engine.version && <span className="text-[11px] font-mono text-[color:var(--fcp-text-tertiary)]">(v{health.engine.version})</span>}
                </div>
              </div>

              {/* Card 2: Catalog Cardinality */}
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

              {/* Card 3: Data Stores */}
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

              {/* Card 4: Pipeline Health */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Pipeline Health
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] tabular-nums">
                  {health.pipeline.failure_rate_pct.toFixed(1)}% Failure Rate
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1">
                  {health.pipeline.running_jobs} running, {health.pipeline.queue_depth} queued
                </div>
              </div>

              {/* Card 5: Pipeline Throughput (Honest metrics, no fake tokens) */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)]">
                <div className="text-[10px] uppercase font-semibold tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">
                  Pipeline Throughput
                </div>
                <div className="text-base font-bold text-[color:var(--fcp-text-primary)] tabular-nums">
                  {health.tokens.total_jobs_analyzed} Bills Analyzed
                </div>
                <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1 font-mono text-[11px]">
                  {health.tokens.avg_job_duration_seconds !== undefined && health.tokens.avg_job_duration_seconds > 0
                    ? `~${health.tokens.avg_job_duration_seconds.toFixed(1)}s avg execution`
                    : 'Duration telemetry pending'}
                </div>
              </div>
            </div>

            {/* Grid 1: Engine Liveness & Architecture + Catalog Status */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Engine Liveness & Architecture */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)] flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                        Reasoning Engine Liveness
                      </h2>
                      <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                        Live round-trip probe directly against active Gemini model via agy CLI.
                      </p>
                    </div>
                    <span className="text-xs font-mono px-2 py-0.5 rounded bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-secondary)]">
                      Concurrency Limit: {health.engine.concurrency_limit}
                    </span>
                  </div>

                  {/* Engine Details Card */}
                  <div className="p-3.5 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-xs mb-4 space-y-2 font-mono text-[11px]">
                    <div className="flex justify-between items-center">
                      <span className="text-[color:var(--fcp-text-tertiary)]">Binary Path:</span>
                      <span className="text-[color:var(--fcp-text-primary)] truncate max-w-xs">{health.engine.probe?.binary_path || 'Resolved at runtime'}</span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-[color:var(--fcp-text-tertiary)]">Model Auth Present:</span>
                      <span className={health.engine.probe?.auth_present ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
                        {health.engine.probe?.auth_present ? '✓ ANTIGRAVITY_API_KEY Configured' : '✕ Key Missing'}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-[color:var(--fcp-text-tertiary)]">Last Probe Latency:</span>
                      <span className="text-[color:var(--fcp-text-primary)]">
                        {health.engine.probe?.round_trip ? `${health.engine.probe.latency_ms} ms` : 'Not run'}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-[color:var(--fcp-text-tertiary)]">Status Detail:</span>
                      <span className="text-[color:var(--fcp-text-secondary)] truncate max-w-xs">{health.engine.probe?.detail || 'Waiting for check'}</span>
                    </div>
                  </div>

                  {/* Test Engine Result Banner */}
                  {engineTestError && (
                    <div className="text-xs p-3 rounded-lg bg-rose-950/40 border border-rose-500/40 text-rose-300 mb-3">
                      <strong className="block font-semibold mb-0.5">Engine Test Failed</strong>
                      {engineTestError}
                    </div>
                  )}

                  {engineTestResult && (
                    <div className={`text-xs p-3.5 rounded-lg border mb-3 ${
                      engineTestResult.state === 'ok'
                        ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-300'
                        : engineTestResult.state === 'warn'
                        ? 'bg-amber-950/30 border-amber-500/40 text-amber-300'
                        : 'bg-rose-950/30 border-rose-500/40 text-rose-300'
                    }`}>
                      <div className="flex items-center justify-between font-bold mb-1">
                        <span>Round-Trip Result: {engineTestResult.state.toUpperCase()}</span>
                        <span className="font-mono">{engineTestResult.latency_ms}ms</span>
                      </div>
                      <p className="text-[11px] font-mono leading-relaxed">{engineTestResult.detail}</p>
                    </div>
                  )}

                  {/* Static Pipeline Architecture Legend */}
                  <div className="mt-4 pt-4 border-t border-[var(--fcp-border-subtle)]">
                    <div className="text-[11px] uppercase tracking-wider font-semibold text-[color:var(--fcp-text-tertiary)] mb-2">
                      Pipeline Architecture Stages (Reference)
                    </div>
                    <div className="space-y-1.5">
                      {health.engine.phases.map((phase, idx) => (
                        <div
                          key={phase}
                          className="flex items-center justify-between text-xs px-2.5 py-1.5 rounded bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]"
                        >
                          <span className="text-[color:var(--fcp-text-primary)] font-medium text-[11px]">{phase}</span>
                          <span className="text-[10px] text-[color:var(--fcp-text-tertiary)] font-mono">Phase {idx + 1}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-6 pt-4 border-t border-[var(--fcp-border-subtle)] flex items-center justify-between">
                  <span className="text-xs text-[color:var(--fcp-text-secondary)] font-medium">
                    Explicit Live Gemini Call
                  </span>
                  <button
                    onClick={handleTestEngine}
                    disabled={testingEngine}
                    className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium transition-colors cursor-pointer flex items-center gap-2"
                  >
                    {testingEngine && (
                      <span className="inline-block w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    )}
                    {testingEngine ? 'Probing Engine...' : 'Test Engine (Live Call)'}
                  </button>
                </div>
              </div>

              {/* GCP Catalog Status & Live Refresh Tracker */}
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)] flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                        GCP Pricing Catalog Status &amp; Refresh
                      </h2>
                      <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                        Deterministic pricing queries local indexed GCP SKUs.
                      </p>
                    </div>
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

            {/* Grid 2: Environment & Credentials Panel */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)]">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
                <div>
                  <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                    Environment &amp; Credentials Dependencies
                  </h2>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                    Live system capabilities, API tokens, CLI binaries, and disk volume headroom verified at runtime.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-[color:var(--fcp-text-tertiary)]">Env Status:</span>
                  <span className={`text-xs px-2.5 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(health.env_status)}`}>
                    {health.env_status}
                  </span>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-tertiary)] font-semibold uppercase text-[10px] tracking-wider">
                      <th className="pb-2.5">Component / Variable</th>
                      <th className="pb-2.5">Present</th>
                      <th className="pb-2.5">Status</th>
                      <th className="pb-2.5 pl-4">Inspection Detail (Secrets Redacted)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                    {health.env && health.env.map((dep: DepHealth) => (
                      <tr key={dep.key} className="hover:bg-[var(--fcp-hover-overlay)] transition-colors">
                        <td className="py-2.5 font-mono text-[11px] text-[color:var(--fcp-text-primary)] font-semibold">
                          {dep.key}
                        </td>
                        <td className="py-2.5 font-mono text-[11px]">
                          {dep.present ? (
                            <span className="text-emerald-400 font-bold">✓ Yes</span>
                          ) : (
                            <span className="text-rose-400 font-bold">✕ No</span>
                          )}
                        </td>
                        <td className="py-2.5">
                          <span className={`inline-block text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase ${statusPillClass(dep.status)}`}>
                            {dep.status}
                          </span>
                        </td>
                        <td className="py-2.5 pl-4 text-[11px] text-[color:var(--fcp-text-secondary)] font-mono">
                          {dep.detail}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Grid 3: Data Stores Management Console */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-[var(--fcp-card-shadow)]">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
                <div>
                  <h2 className="text-base font-semibold text-[color:var(--fcp-text-primary)]">
                    Data Stores &amp; Dependencies Console
                  </h2>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
                    View critical JSON registries, verify integrity, edit configuration with automatic backups and audit trail.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-[color:var(--fcp-text-tertiary)]">Data Roll-up:</span>
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
                      <th className="pb-2.5">File / Target</th>
                      <th className="pb-2.5">Status</th>
                      <th className="pb-2.5 text-right">Items</th>
                      <th className="pb-2.5 text-right">Size</th>
                      <th className="pb-2.5 text-right">Age</th>
                      <th className="pb-2.5 pl-4">Integrity Detail</th>
                      <th className="pb-2.5 text-right pr-2">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                    {health.stores && health.stores.map((store: StoreHealth) => (
                      <tr key={store.key} className="hover:bg-[var(--fcp-hover-overlay)] transition-colors">
                        <td className="py-2.5 font-mono text-[11px] text-[color:var(--fcp-text-primary)] font-medium">
                          {store.key}
                        </td>
                        <td className="py-2.5 text-[color:var(--fcp-text-secondary)] font-mono text-[11px]">
                          {store.file}
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
                        <td className="py-2.5 text-right pr-2">
                          {store.is_db_backed && store.manage_url ? (
                            <Link
                              to={store.manage_url}
                              className="text-xs px-2.5 py-1 rounded bg-[var(--fcp-hover-overlay)] hover:bg-[var(--fcp-accent)] hover:text-white text-[color:var(--fcp-accent)] font-medium transition-colors inline-block"
                            >
                              Manage →
                            </Link>
                          ) : (
                            <button
                              onClick={() => handleOpenStore(store.key)}
                              className="text-xs px-2.5 py-1 rounded border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-primary)] font-medium transition-colors cursor-pointer"
                            >
                              {store.editable ? 'View / Edit' : 'View'}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* Slide-over / Modal for Datastore Editor */}
        {activeStoreKey && (
          <div className="fixed inset-0 z-40 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl text-[color:var(--fcp-text-primary)]">
              {/* Header */}
              <div className="p-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between">
                <div>
                  <h3 className="text-base font-bold flex items-center gap-2">
                    <span>Datastore: <span className="font-mono text-cyan-400">{activeStoreKey}</span></span>
                    {storeDetail?.editable && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 uppercase font-semibold">
                        Editable
                      </span>
                    )}
                  </h3>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5 font-mono">
                    {storeDetail?.file} — {storeDetail?.size_bytes ? formatBytes(storeDetail.size_bytes) : ''} ({storeDetail?.count} items)
                  </p>
                </div>
                <button
                  onClick={handleCloseStore}
                  className="text-gray-400 hover:text-white p-1 rounded-lg text-lg leading-none cursor-pointer"
                >
                  ✕
                </button>
              </div>

              {/* Status / Alerts */}
              {storeSaveError && (
                <div className="m-4 p-3 rounded-lg bg-rose-950/50 border border-rose-500/50 text-rose-300 text-xs">
                  <strong className="block font-semibold mb-0.5">Operation Error</strong>
                  {storeSaveError}
                </div>
              )}
              {storeSaveSuccess && (
                <div className="m-4 p-3 rounded-lg bg-emerald-950/50 border border-emerald-500/50 text-emerald-300 text-xs">
                  <strong className="block font-semibold mb-0.5">Success</strong>
                  {storeSaveSuccess}
                </div>
              )}

              {/* Body */}
              <div className="flex-1 p-4 overflow-y-auto">
                {storeLoading ? (
                  <div className="py-20 text-center text-xs text-[color:var(--fcp-text-tertiary)]">
                    Loading datastore content from disk...
                  </div>
                ) : storeDetail?.editable ? (
                  <div>
                    <label className="block text-xs font-semibold mb-1 text-[color:var(--fcp-text-secondary)]">
                      JSON Configuration Payload:
                    </label>
                    <textarea
                      value={storeDraft}
                      onChange={(e) => setStoreDraft(e.target.value)}
                      rows={20}
                      className="w-full p-3 font-mono text-xs rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:border-[var(--fcp-accent)] leading-relaxed select-text"
                      spellCheck={false}
                    />
                  </div>
                ) : (
                  <div>
                    <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-secondary)] space-y-2 mb-4">
                      <p className="font-semibold text-[color:var(--fcp-text-primary)]">
                        This store is a binary artifact or compressed archive and cannot be edited as inline JSON.
                      </p>
                      <p>{storeDetail?.detail}</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="p-4 border-t border-[var(--fcp-border-subtle)] flex items-center justify-between text-xs">
                <div className="text-[11px] text-[color:var(--fcp-text-tertiary)] font-mono">
                  {storeDetail?.last_backup_at ? (
                    <span>Last backup: {new Date(storeDetail.last_backup_at).toLocaleString()} ({storeDetail.backup_count} total)</span>
                  ) : (
                    <span>No backups on disk yet</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleCloseStore}
                    className="px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium cursor-pointer"
                  >
                    Close
                  </button>
                  {storeDetail?.editable && storeDetail.backup_count > 0 && (
                    <button
                      onClick={() => setConfirmRevertOpen(true)}
                      disabled={storeReverting || storeSaving}
                      className="px-3 py-1.5 rounded-lg bg-amber-600/30 hover:bg-amber-600/50 border border-amber-500/40 text-amber-200 text-xs font-medium cursor-pointer transition-colors"
                    >
                      {storeReverting ? 'Restoring...' : 'Revert to Backup'}
                    </button>
                  )}
                  {storeDetail?.editable && (
                    <button
                      onClick={() => setConfirmSaveOpen(true)}
                      disabled={storeSaving || storeReverting}
                      className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-semibold cursor-pointer transition-colors"
                    >
                      {storeSaving ? 'Saving...' : 'Validate & Save'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Confirm Save Dialog */}
        <ConfirmDialog
          open={confirmSaveOpen}
          onOpenChange={setConfirmSaveOpen}
          title={`Save Changes to ${activeStoreKey}?`}
          description={
            <div>
              <p className="mb-2">
                This file directly drives AWS-to-GCP pricing projections for all future jobs.
              </p>
              <p className="text-amber-300 font-semibold text-xs">
                An automatic backup will be created in data/_backups before writing the updated file.
              </p>
            </div>
          }
          confirmLabel="Save Datastore"
          onConfirm={handleSaveStore}
          loading={storeSaving}
        />

        {/* Confirm Revert Dialog */}
        <ConfirmDialog
          open={confirmRevertOpen}
          onOpenChange={setConfirmRevertOpen}
          title={`Revert ${activeStoreKey} to Previous Backup?`}
          description={
            <div>
              <p className="mb-2">
                Are you sure you want to discard current changes and restore the most recent backup?
              </p>
              <p className="text-rose-300 font-semibold text-xs">
                The current state will also be backed up before restoration.
              </p>
            </div>
          }
          destructive={true}
          confirmLabel="Confirm Revert"
          onConfirm={handleRevertStore}
          loading={storeReverting}
        />
      </div>
    </Layout>
  )
}
