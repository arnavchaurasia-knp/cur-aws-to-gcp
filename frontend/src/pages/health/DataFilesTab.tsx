import { Link } from 'react-router-dom'
import type { StoreHealth } from '../../api/health'
import { StatusBadge } from './StatusBadge'
import { formatBytes } from './format'

interface DataFilesTabProps {
  stores: StoreHealth[]
  onOpenStore: (key: string) => void
}

export function DataFilesTab({ stores, onOpenStore }: Readonly<DataFilesTabProps>) {
  return (
    <div className="space-y-6">
      {/* Intro box */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          What this is
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          The pipeline relies on several configuration, mapping, and price catalog data files. File-based datastores
          can be edited directly with automatic version backups; database-backed settings link to their dedicated
          management pages.
        </p>
      </div>

      <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] overflow-x-auto shadow-2xs">
        <table className="w-full text-left text-xs">
          <thead className="bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-muted)] border-b border-[var(--fcp-border-subtle)]">
            <tr>
              <th className="py-3 px-4 font-medium">Data File</th>
              <th className="py-3 px-4 font-medium">Status</th>
              <th className="py-3 px-4 font-medium">Items / Records</th>
              <th className="py-3 px-4 font-medium">Size</th>
              <th className="py-3 px-4 font-medium">Last Changed</th>
              <th className="py-3 px-4 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
            {stores.map((s) => (
              <tr key={s.key} className="hover:bg-[var(--fcp-hover-overlay)]">
                <td className="py-3 px-4 max-w-sm">
                  <div className="font-semibold text-sm text-[color:var(--fcp-text-primary)]">
                    {s.title || s.key}
                  </div>
                  <div className="text-[11px] font-mono text-[color:var(--fcp-text-muted)] pt-0.5">
                    {s.file}
                  </div>
                  {s.description && (
                    <p className="text-[11px] text-[color:var(--fcp-text-secondary)] pt-1 leading-snug">
                      {s.description}
                    </p>
                  )}
                </td>

                <td className="py-3 px-4 align-top">
                  <StatusBadge status={s.status} />
                  {s.detail && s.status !== 'ok' && (
                    <div className="text-[11px] text-[color:var(--fcp-text-muted)] pt-1">{s.detail}</div>
                  )}
                </td>

                <td className="py-3 px-4 align-top font-mono">
                  {s.count > 0 ? s.count.toLocaleString() : '—'}
                </td>

                <td className="py-3 px-4 align-top font-mono">
                  {s.size_bytes > 0 ? formatBytes(s.size_bytes) : '—'}
                </td>

                <td className="py-3 px-4 align-top text-[color:var(--fcp-text-muted)]">
                  {s.age_days >= 0 ? `${s.age_days}d ago` : '—'}
                </td>

                <td className="py-3 px-4 align-top text-right">
                  {s.is_db_backed && s.manage_url ? (
                    <Link
                      to={s.manage_url}
                      className="px-3 py-1 rounded-lg bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-hover-overlay)] text-indigo-600 dark:text-indigo-400 font-medium inline-block"
                    >
                      Manage →
                    </Link>
                  ) : (
                    <button
                      onClick={() => onOpenStore(s.key)}
                      className="px-3 py-1 rounded-lg bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-primary)] font-medium cursor-pointer"
                    >
                      {s.editable ? 'Edit' : 'View'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
