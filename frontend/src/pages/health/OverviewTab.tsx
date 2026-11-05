import type { HealthResponse } from '../../api/health'
import { StatusBadge } from './StatusBadge'

interface OverviewTabProps {
  health: HealthResponse
  onSelectTab: (tab: string) => void
}

export function OverviewTab({ health, onSelectTab }: Readonly<OverviewTabProps>) {
  const cards = [
    {
      id: 'engine',
      title: 'AI Engine',
      status: health.engine.status,
      summary: health.engine.summary,
      details: `${health.engine.model} (${health.engine.cli_found ? 'agy installed' : 'agy missing'})`,
    },
    {
      id: 'catalog',
      title: 'Pricing Catalog',
      status: health.catalog.status,
      summary: health.catalog.summary,
      details: `${health.catalog.sku_count.toLocaleString()} SKUs across ${health.catalog.service_count} GCP services`,
    },
    {
      id: 'jobs',
      title: 'Jobs & Pipeline',
      status: health.jobs.status,
      summary: health.jobs.summary,
      details: `${health.jobs.running} running · ${health.jobs.queued} queued · limit ${health.jobs.concurrency_limit}`,
    },
    {
      id: 'data',
      title: 'Data Files',
      status: health.data.status,
      summary: health.data.summary,
      details: `${health.data.stores.length} configuration & pricing files monitored`,
    },
    {
      id: 'settings',
      title: 'Settings & Secrets',
      status: health.settings.status,
      summary: health.settings.summary,
      details: `${health.settings.override_count} overrides active · encryption: ${health.settings.encryption_source}`,
    },
    {
      id: 'runtime',
      title: 'Server Tools',
      status: health.runtime.status,
      summary: health.runtime.summary,
      details: `${health.runtime.checks.length} host dependencies checked`,
    },
  ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {cards.map((c) => (
        <div
          key={c.id}
          className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-2xs hover:border-[var(--fcp-border-strong)] transition-all flex flex-col justify-between space-y-4"
        >
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-semibold text-sm text-[color:var(--fcp-text-primary)]">{c.title}</h3>
              <StatusBadge status={c.status} />
            </div>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">{c.summary}</p>
            <div className="text-[11px] font-mono text-[color:var(--fcp-text-muted)] pt-1">{c.details}</div>
          </div>

          <div className="pt-2 border-t border-[var(--fcp-border-subtle)]">
            <button
              onClick={() => onSelectTab(c.id)}
              className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 flex items-center gap-1 cursor-pointer"
            >
              Open {c.title} →
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
