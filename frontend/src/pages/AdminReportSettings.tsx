import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Layout } from '../components/Layout'
import {
  getReportSettings,
  putReportSetting,
  getReportSettingHistory,
  resetReportSettings,
  type ReportSetting,
  type ReportSettingChange,
  type ReportSettingsResponse,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { BackButton } from '../components/BackButton'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

function formatReportTimestamp(raw: string): { dateStr: string; timeStr: string; relative: string } {
  if (!raw) return { dateStr: '—', timeStr: '', relative: '' }
  try {
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return { dateStr: raw, timeStr: '', relative: '' }
    const dateStr = d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    const timeStr = d.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000)
    let relative = 'just now'
    if (diffSec >= 60 && diffSec < 3600) relative = `${Math.floor(diffSec / 60)}m ago`
    else if (diffSec >= 3600 && diffSec < 86400) relative = `${Math.floor(diffSec / 3600)}h ago`
    else if (diffSec >= 86400) relative = `${Math.floor(diffSec / 86400)}d ago`
    return { dateStr, timeStr, relative }
  } catch {
    return { dateStr: raw, timeStr: '', relative: '' }
  }
}

export function AdminReportSettings({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Report settings — admin')
  const [data, setData] = useState<ReportSettingsResponse | null>(null)
  const [history, setHistory] = useState<ReportSettingChange[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null) // key being saved
  const [edits, setEdits] = useState<Record<string, { value: string; reason: string }>>({})
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false)
  const [resetting, setResetting] = useState(false)

  const groups = ['sections', 'columns', 'rows'] as const

  const refresh = async (silent = false) => {
    // Full-page spinner only on the initial load. A toggle/save re-fetch must be
    // silent, otherwise swapping the list for a spinner collapses the layout and
    // jerks the scroll position to the top on every toggle.
    if (!silent) setLoading(true)
    setError(null)
    try {
      const d = await getReportSettings()
      setData(d)
      const h = await getReportSettingHistory()
      setHistory(h)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load report settings')
    } finally {
      if (!silent) setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const handleSave = async (setting: ReportSetting, value: string, customReason?: string) => {
    // Validate numeric
    if (setting.value_type === 'number') {
      const num = Number(value)
      if (!Number.isFinite(num)) {
        setError('Value must be a valid number')
        return
      }
      if (setting.min_value != null && num < setting.min_value) {
        setError(`Value must be >= ${setting.min_value}`)
        return
      }
      if (setting.max_value != null && num > setting.max_value) {
        setError(`Value must be <= ${setting.max_value}`)
        return
      }
    } else if (setting.value_type === 'bool') {
      const lower = value.trim().toLowerCase()
      if (lower !== 'true' && lower !== 'false' && lower !== '1' && lower !== '0' && lower !== 'yes' && lower !== 'no') {
        setError('Value must be true or false')
        return
      }
    }

    let reason = (customReason || '').trim()
    if (!reason) {
      if (setting.value_type === 'bool') {
        reason = `Toggled ${setting.key} to ${value}`
      } else {
        setError('A reason is required to change a setting')
        return
      }
    }

    setSaving(setting.key)
    setError(null)
    try {
      // Normalize bool to true/false
      let normalized = value
      if (setting.value_type === 'bool') {
        const lower = value.trim().toLowerCase()
        normalized = lower === 'true' || lower === '1' || lower === 'yes' ? 'true' : 'false'
      }
      const result = await putReportSetting(setting.key, { value: normalized, reason })
      if (result) {
        setEdits(prev => {
          const next = { ...prev }
          delete next[setting.key]
          return next
        })
        toast.success(`Updated ${setting.key}`)
        await refresh(true)
        triggerNotificationRefresh()
      } else {
        toast.error(`Failed to update ${setting.key}`)
        setError('Save failed')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Save failed'
      toast.error(msg)
      setError(msg)
    } finally {
      setSaving(null)
    }
  }

  const handleReset = async () => {
    setResetting(true)
    setError(null)
    try {
      const result = await resetReportSettings()
      if (result) {
        toast.success('Report settings reset to defaults')
        await refresh(true)
        triggerNotificationRefresh()
        setResetConfirmOpen(false)
      } else {
        toast.error('Reset failed')
        setError('reset failed')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'reset failed'
      toast.error(msg)
      setError(msg)
    } finally {
      setResetting(false)
    }
  }

  if (loading) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10">
          <p className="text-sm text-gray-500">Loading…</p>
        </div>
      </Layout>
    )
  }

  if (!data && error) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10">
          <div className="p-4 rounded-xl bg-orange-950/30 border border-orange-500/30 text-orange-400 text-xs">
            {error}
          </div>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="max-w-5xl mx-auto px-6 py-10">
        <BackButton fallback="/admin" />
        {error && (
          <div className="mb-6 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-orange-400 hover:text-orange-200 cursor-pointer text-sm font-bold ml-2">✕</button>
          </div>
        )}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-semibold mb-1">Report Settings</h1>
            <p className="text-sm text-gray-500 max-w-2xl">
              Configure sections, columns, and line-item filters included in customer-facing cost projection reports.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => refresh()}
              disabled={loading}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-200 transition-colors cursor-pointer disabled:opacity-50"
            >
              <RefreshIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {data && (
          <div className="space-y-8">
            {/* Version & Info Card */}
            <div className="bg-white/[0.02] border border-white/10 rounded-xl p-5 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#00C2BB]/10 border border-[#00C2BB]/20 flex items-center justify-center shrink-0 mt-0.5">
                  <ShieldCheckIcon className="w-4 h-4 text-[#00C2BB]" />
                </div>
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-300">Active Settings Configuration</h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Settings apply instantly to all subsequent report generations. Totals are preserved across all line items.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0 self-start sm:self-auto">
                <span className="text-xs text-gray-400">Fingerprint:</span>
                <span className="font-mono text-xs font-semibold text-[#00C2BB] bg-[#00C2BB]/10 px-2.5 py-1 rounded border border-[#00C2BB]/20">
                  {data.version.slice(0, 16)}…
                </span>
              </div>
            </div>

            {/* Settings Sections */}
            {groups.map((group) => {
              const settingsInGroup = data.settings.filter((s) => s.group_name === group)
              const groupLabel = group === 'sections' ? 'Report Sections' : group === 'columns' ? 'Data Columns' : 'Row Visibility Filters'
              const groupDesc = group === 'sections'
                ? 'Control top-level summaries, breakdowns, and appendix visibility'
                : group === 'columns'
                ? 'Select pricing, discount, and unit metric columns shown in tables'
                : 'Define filtering thresholds and exclusion rules for micro-charges'

              return (
                <div key={group} className="bg-white/[0.02] border border-white/10 rounded-xl overflow-hidden shadow-sm">
                  <div className="px-6 py-4 border-b border-white/10 bg-white/[0.01] flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      {group === 'sections' && <LayoutIcon className="w-4 h-4 text-[#645DF6]" />}
                      {group === 'columns' && <ColumnsIcon className="w-4 h-4 text-[#00C2BB]" />}
                      {group === 'rows' && <FilterIcon className="w-4 h-4 text-amber-400" />}
                      <div>
                        <h2 className="text-xs font-bold text-gray-200 uppercase tracking-wider">
                          {groupLabel}
                        </h2>
                        <p className="text-[11px] text-gray-500 mt-0.5">{groupDesc}</p>
                      </div>
                    </div>
                    <span className="text-[11px] font-mono text-gray-500">
                      {settingsInGroup.length} items
                    </span>
                  </div>

                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-white/10 bg-white/[0.01]">
                        <th className="py-3 px-6 text-gray-400 font-medium w-1/2">Setting &amp; Description</th>
                        <th className="py-3 px-4 text-gray-400 font-medium w-1/4">Status / Value</th>
                        <th className="py-3 px-6 text-gray-400 font-medium text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {settingsInGroup.map((s) => {
                        const isBool = s.value_type === 'bool'
                        const isChecked = s.value === 'true'
                        const edit = edits[s.key]
                        const isEditing = Boolean(edit)
                        const inRange = !edit || edit.value === '' || (() => {
                          const n = Number(edit.value)
                          return Number.isFinite(n) && (s.min_value == null || n >= s.min_value) && (s.max_value == null || n <= s.max_value)
                        })()

                        return (
                          <tr key={s.key} className="hover:bg-white/[0.02] transition-colors">
                            <td className="py-3.5 px-6">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-semibold text-[color:var(--fcp-text-primary)] text-xs">{s.title}</span>
                                <span className="font-mono text-[9px] text-[color:var(--fcp-text-tertiary)] bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] px-1.5 py-0.5 rounded">{s.key}</span>
                              </div>
                              <div className="text-[11px] text-[color:var(--fcp-text-secondary)] mt-1.5 leading-relaxed max-w-md">{s.description}</div>
                            </td>
                            <td className="py-3.5 px-4">
                              {isBool ? (
                                <button
                                  type="button"
                                  onClick={() => handleSave(s, isChecked ? 'false' : 'true', `Toggled ${s.key} to ${isChecked ? 'false' : 'true'}`)}
                                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 transition-colors duration-200 ease-in-out focus:outline-none shadow-sm ${
                                    isChecked
                                      ? 'bg-[#00C2BB] border-[#009b95]'
                                      : 'bg-slate-200 border-slate-400 dark:bg-slate-800 dark:border-slate-600'
                                  }`}
                                  role="switch"
                                  aria-checked={isChecked}
                                  title={isChecked ? 'Click to disable' : 'Click to enable'}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4.5 w-4.5 transform rounded-full bg-white shadow-md border border-slate-300 dark:border-transparent transition duration-200 ease-in-out mt-0.5 ${
                                      isChecked ? 'translate-x-5' : 'translate-x-0.5'
                                    }`}
                                  />
                                </button>
                              ) : isEditing ? (
                                <div className="flex flex-col gap-1.5 py-1">
                                  <div className="flex items-center gap-2">
                                    <input
                                      type="number"
                                      step="any"
                                      min={s.min_value ?? undefined}
                                      max={s.max_value ?? undefined}
                                      value={edit.value}
                                      onChange={(e) => setEdits(prev => ({ ...prev, [s.key]: { ...prev[s.key], value: e.target.value } }))}
                                      className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded px-2.5 py-1.5 text-[var(--fcp-text-primary)] text-xs w-28 focus:border-[#645DF6] outline-none"
                                      placeholder="Value"
                                    />
                                    {edit.value !== '' && !inRange && (
                                      <span className="text-[11px] text-red-400">out of range</span>
                                    )}
                                  </div>
                                  <input
                                    type="text"
                                    placeholder="Reason for change…"
                                    value={edit.reason}
                                    onChange={(e) => setEdits(prev => ({ ...prev, [s.key]: { ...prev[s.key], reason: e.target.value } }))}
                                    className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded px-2 py-1 text-[var(--fcp-text-primary)] text-xs placeholder-[var(--fcp-text-tertiary)] focus:border-[#645DF6] outline-none w-56"
                                  />
                                  <div className="text-[10px] text-[var(--fcp-text-tertiary)] font-mono">
                                    Range: [{s.min_value ?? '–'} to {s.max_value ?? '–'}]
                                  </div>
                                </div>
                              ) : (
                                <div className="space-y-0.5">
                                  <span className="font-mono tabular-nums text-xs font-semibold text-[#00C2BB]">
                                    {s.value}
                                  </span>
                                  <div className="text-[10px] text-[var(--fcp-text-tertiary)] font-mono">
                                    Range: [{s.min_value ?? '–'} to {s.max_value ?? '–'}]
                                  </div>
                                </div>
                              )}
                            </td>
                            <td className="py-3.5 px-6 text-right">
                              {saving === s.key ? (
                                <span className="text-[11px] text-[#00C2BB] font-medium animate-pulse inline-flex items-center gap-1.5">
                                  <RefreshIcon className="w-3 h-3 animate-spin" /> Saving…
                                </span>
                              ) : isBool ? (
                                <span className={`inline-block text-[11px] font-semibold px-2.5 py-0.5 rounded-full border ${
                                  isChecked
                                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                                    : 'bg-slate-200 dark:bg-white/5 text-slate-600 dark:text-slate-400 border-slate-300 dark:border-white/10'
                                }`}>
                                  {isChecked ? 'Enabled' : 'Disabled'}
                                </span>
                              ) : isEditing ? (
                                <div className="flex items-center justify-end gap-2">
                                  <button
                                    type="button"
                                    onClick={() => handleSave(s, edit.value, edit.reason)}
                                    disabled={!edit.value || !edit.reason.trim() || !inRange || saving === s.key}
                                    className="px-2.5 py-1.5 rounded bg-[#00C2BB] text-black font-semibold text-xs hover:bg-[#00C2BB]/90 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    {saving === s.key ? 'Saving…' : 'Save'}
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setEdits(prev => { const next = { ...prev }; delete next[s.key]; return next })}
                                    className="px-2.5 py-1.5 rounded text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-hover)] text-xs transition-colors cursor-pointer border border-[var(--fcp-border-subtle)]"
                                  >
                                    Cancel
                                  </button>
                                </div>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => setEdits(prev => ({ ...prev, [s.key]: { value: s.value, reason: '' } }))}
                                  className="px-2.5 py-1.5 rounded border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:border-[var(--fcp-border-default)] text-xs transition-colors cursor-pointer"
                                >
                                  Edit
                                </button>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )
            })}

            {/* Change History */}
            <div className="bg-white/[0.02] border border-white/10 rounded-xl p-6 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <HistoryIcon className="w-4 h-4 text-gray-400" />
                  <h2 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                    Change Audit Log
                  </h2>
                </div>
                <span className="text-[11px] text-gray-500 font-mono">
                  {history.length > 0 ? `Showing top ${Math.min(history.length, 10)} records (rolling limit: 10)` : '0 records'}
                </span>
              </div>

              {history.length === 0 ? (
                <p className="text-xs text-gray-500 py-4 text-center">No setting changes recorded yet.</p>
              ) : (
                <div className="overflow-x-auto border border-[var(--fcp-border-subtle)] rounded-xl">
                  <table className="w-full text-left text-xs border-collapse table-fixed min-w-[720px]">
                    <colgroup>
                      <col className="w-56" />
                      <col className="w-60" />
                      <col />
                      <col className="w-52" />
                    </colgroup>
                    <thead>
                      <tr className="border-b border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)] bg-[var(--fcp-table-header-bg)]">
                        <th className="py-3 px-4 font-semibold text-left">Timestamp</th>
                        <th className="py-3 px-4 font-semibold text-left">Setting Key</th>
                        <th className="py-3 px-4 font-semibold text-left">Transition</th>
                        <th className="py-3 px-4 font-semibold text-right">Modified By</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                      {history.slice(0, 10).map((h) => {
                        const ts = formatReportTimestamp(h.changed_at)
                        return (
                          <tr key={h.id} className="hover:bg-[var(--fcp-bg-hover)] transition-colors group">
                            <td className="py-3 px-4 align-top">
                              <div className="font-semibold text-[var(--fcp-text-primary)] text-xs">{ts.dateStr}</div>
                              <div className="text-[11px] font-mono text-[var(--fcp-text-tertiary)] flex items-center gap-1.5 mt-0.5">
                                <span>{ts.timeStr}</span>
                                {ts.relative && (
                                  <>
                                    <span>•</span>
                                    <span className="text-[#00C2BB] font-sans font-medium">{ts.relative}</span>
                                  </>
                                )}
                              </div>
                            </td>
                            <td className="py-3 px-4 align-top font-mono text-xs">
                              <span className="inline-block bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-primary)] px-2.5 py-1 rounded-md text-[11px] font-semibold break-all">
                                {h.key}
                              </span>
                            </td>
                            <td className="py-3 px-4 align-top text-xs">
                              <div className="flex items-center gap-2 flex-wrap break-all leading-relaxed">
                                <span className="bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-0.5 rounded text-[11px] font-mono break-all line-through">
                                  {h.old_value}
                                </span>
                                <span className="text-[var(--fcp-text-secondary)] font-bold">→</span>
                                <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded text-[11px] font-mono font-bold break-all">
                                  {h.new_value}
                                </span>
                              </div>
                            </td>
                            <td className="py-3 px-4 align-top text-right text-xs text-[var(--fcp-text-secondary)] font-medium whitespace-nowrap truncate" title={h.changed_by}>
                              {h.changed_by}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Danger Zone: Reset to Defaults */}
            <div className="bg-red-950/10 border border-red-500/20 rounded-xl p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-red-400">Reset All Settings</h3>
                <p className="text-xs text-gray-400 mt-1">Revert all report configuration switches and parameters back to system defaults.</p>
              </div>
              <button
                onClick={() => setResetConfirmOpen(true)}
                className="px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 hover:text-red-300 text-xs font-medium transition-colors cursor-pointer shrink-0 self-start sm:self-auto"
              >
                Reset to Defaults
              </button>
            </div>

            <ConfirmDialog
              open={resetConfirmOpen}
              onOpenChange={setResetConfirmOpen}
              title="Reset report settings"
              description="Reset all report settings to their defaults? This cannot be undone."
              confirmLabel="Reset settings"
              destructive
              loading={resetting}
              onConfirm={handleReset}
            />
          </div>
        )}
      </div>
    </Layout>
  )
}

/* ── Standard Professional Icons ── */

function ShieldCheckIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
    </svg>
  )
}

function LayoutIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
    </svg>
  )
}

function ColumnsIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 4v16m6-16v16M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1z" />
    </svg>
  )
}

function FilterIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
    </svg>
  )
}

function HistoryIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function RefreshIcon({ className = 'w-4 h-4' }: Readonly<{ className?: string }>) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
    </svg>
  )
}