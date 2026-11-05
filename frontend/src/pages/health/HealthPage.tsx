import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import type { EnvListResponse, HealthResponse, IssueAction } from '../../api/health'
import { getEnv, getHealth } from '../../api/health'
import type { UserInfo } from '../../api/auth'
import { BackButton } from '../../components/BackButton'
import { Layout } from '../../components/Layout'
import { useTitle } from '../../lib/useTitle'
import { CatalogTab } from './CatalogTab'
import { DataFilesTab } from './DataFilesTab'
import { EngineTab } from './EngineTab'
import { GuideTab } from './GuideTab'
import { IssuesList } from './IssuesList'
import { JobsTab } from './JobsTab'
import { OverviewTab } from './OverviewTab'
import { ServerMismatchBanner } from './ServerMismatchBanner'
import { SettingsTab } from './SettingsTab'
import { StatusHero } from './StatusHero'
import { StoreEditor } from './StoreEditor'

const AUTO_REFRESH_STORAGE_KEY = 'fcp_health_auto_refresh'

export function HealthPage({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('System Health Console — Facets Admin')
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const activeTab = searchParams.get('tab') || 'overview'

  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Settings & Secrets data
  const [envData, setEnvData] = useState<EnvListResponse | null>(null)
  const [loadingEnv, setLoadingEnv] = useState(false)

  // Modals & sub-editors
  const [activeStoreKey, setActiveStoreKey] = useState<string | null>(null)

  // Auto-refresh state (persisted)
  const [autoRefresh, setAutoRefresh] = useState<boolean>(() => {
    try {
      return localStorage.getItem(AUTO_REFRESH_STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })

  const refreshIntervalRef = useRef<number | null>(null)

  const handleToggleAutoRefresh = () => {
    const next = !autoRefresh
    setAutoRefresh(next)
    try {
      localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(next))
    } catch {
      // ignore localStorage errors
    }
  }

  const loadHealthData = async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const data = await getHealth()
      setHealth(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const loadEnvData = async () => {
    setLoadingEnv(true)
    try {
      const data = await getEnv()
      setEnvData(data)
    } catch {
      // handled
    } finally {
      setLoadingEnv(false)
    }
  }

  useEffect(() => {
    loadHealthData()
  }, [])

  useEffect(() => {
    if (activeTab === 'settings' && !envData) {
      loadEnvData()
    }
  }, [activeTab, envData])

  // Auto-refresh interval
  useEffect(() => {
    if (!autoRefresh) {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current)
      return
    }

    refreshIntervalRef.current = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        loadHealthData(true)
        if (activeTab === 'settings') {
          loadEnvData()
        }
      }
    }, 30000)

    return () => {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current)
    }
  }, [autoRefresh, activeTab])

  const setTab = (tab: string) => {
    setSearchParams({ tab })
  }

  const handleAction = (action: IssueAction) => {
    switch (action.kind) {
      case 'test_engine':
        setTab('engine')
        break
      case 'refresh_catalog':
        setTab('catalog')
        break
      case 'edit_env':
        setTab('settings')
        break
      case 'open_store':
        if (action.target) setActiveStoreKey(action.target)
        setTab('data')
        break
      case 'open_tab':
        if (action.target) setTab(action.target)
        break
      case 'link':
        if (action.target) navigate(action.target)
        break
    }
  }

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'engine', label: 'AI Engine' },
    { id: 'catalog', label: 'Pricing Catalog' },
    { id: 'jobs', label: 'Jobs' },
    { id: 'data', label: 'Data Files' },
    { id: 'settings', label: 'Settings & Secrets' },
    { id: 'guide', label: 'Guide' },
  ]

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-4 py-8 space-y-6">
        <BackButton fallback="/admin" />

        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
              System Health & Diagnostics
            </h1>
            <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1 max-w-2xl">
              Honest, real-time operating console and configuration manager.
            </p>
          </div>
        </div>

        {/* Server build / schema mismatch banner */}
        {health && <ServerMismatchBanner serverSchemaVersion={health.schema_version} />}

        {error && (
          <div className="p-4 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800 text-rose-800 dark:text-rose-200 text-sm">
            {error}
          </div>
        )}

        {/* Overall Status Hero */}
        {health && (
          <StatusHero
            health={health}
            onRefresh={() => loadHealthData(false)}
            loading={loading}
            autoRefresh={autoRefresh}
            onToggleAutoRefresh={handleToggleAutoRefresh}
          />
        )}

        {/* Urgent Issues List */}
        {health && <IssuesList issues={health.issues} onAction={handleAction} />}

        {/* Navigation Tabs */}
        <div className="border-b border-[var(--fcp-border-subtle)]">
          <nav className="flex space-x-2 sm:space-x-4 overflow-x-auto" aria-label="Tabs">
            {tabs.map((tab) => {
              const active = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setTab(tab.id)}
                  className={`py-2.5 px-3 border-b-2 text-xs font-semibold whitespace-nowrap cursor-pointer transition-colors ${
                    active
                      ? 'border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
                      : 'border-transparent text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)] hover:border-[var(--fcp-border-subtle)]'
                  }`}
                >
                  {tab.label}
                </button>
              )
            })}
          </nav>
        </div>

        {/* Tab Content */}
        {loading && !health ? (
          <div className="py-20 text-center text-xs text-[color:var(--fcp-text-muted)]">
            Checking the system…
          </div>
        ) : (
          health && (
            <div>
              {activeTab === 'overview' && (
                <OverviewTab health={health} onSelectTab={setTab} />
              )}
              {activeTab === 'engine' && (
                <EngineTab
                  engine={health.engine}
                  onSelectTab={setTab}
                  onRefreshHealth={() => loadHealthData(true)}
                />
              )}
              {activeTab === 'catalog' && (
                <CatalogTab
                  catalog={health.catalog}
                  onSelectTab={setTab}
                  onRefreshHealth={() => loadHealthData(true)}
                />
              )}
              {activeTab === 'jobs' && <JobsTab jobs={health.jobs} />}
              {activeTab === 'data' && (
                <DataFilesTab
                  stores={health.data.stores}
                  onOpenStore={(k) => setActiveStoreKey(k)}
                />
              )}
              {activeTab === 'settings' && (
                <SettingsTab
                  envData={envData}
                  loading={loadingEnv}
                  onRefreshEnv={loadEnvData}
                />
              )}
              {activeTab === 'guide' && <GuideTab />}
            </div>
          )
        )}

        {/* Store Editor Modal */}
        {activeStoreKey && (
          <StoreEditor
            storeKey={activeStoreKey}
            onClose={() => setActiveStoreKey(null)}
            onSaved={() => loadHealthData(true)}
          />
        )}
      </div>
    </Layout>
  )
}
