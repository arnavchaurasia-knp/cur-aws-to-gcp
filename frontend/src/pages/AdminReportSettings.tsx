import { useEffect, useState } from 'react'
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

export function AdminReportSettings({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Report settings — admin')
  const [data, setData] = useState<ReportSettingsResponse | null>(null)
  const [history, setHistory] = useState<ReportSettingChange[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null) // key being saved
  const [editingValue, setEditingValue] = useState<string>('')
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false)
  const [resetting, setResetting] = useState(false)

  const groups = ['sections', 'columns', 'rows'] as const

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getReportSettings()
      setData(d)
      const h = await getReportSettingHistory()
      setHistory(h)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load report settings')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const handleSave = async (setting: ReportSetting, value: string) => {
    // Validate numeric
    if (setting.value_type === 'number') {
      const num = Number(value)
      if (!Number.isFinite(num)) {
        setError('value must be a number')
        return
      }
      if (setting.min_value != null && num < setting.min_value) {
        setError(`value must be >= ${setting.min_value}`)
        return
      }
      if (setting.max_value != null && num > setting.max_value) {
        setError(`value must be <= ${setting.max_value}`)
        return
      }
    } else if (setting.value_type === 'bool') {
      const lower = value.trim().toLowerCase()
      if (lower !== 'true' && lower !== 'false' && lower !== '1' && lower !== '0' && lower !== 'yes' && lower !== 'no') {
        setError('value must be true or false')
        return
      }
    }

    const reason = editingValue.trim()
    if (!reason) {
      setError('a reason is required to change a setting')
      return
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
        setEditingValue('')
        await refresh()
      } else {
        setError('save failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'save failed')
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
        await refresh()
        setResetConfirmOpen(false)
      } else {
        setError('reset failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'reset failed')
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

  if (error) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10">
          <p className="text-xs text-orange-400">{error}</p>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <Nav user={user} />
      <div className="max-w-4xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-semibold mb-1">Report settings</h1>
        <p className="text-sm text-gray-500 mb-8">
          Toggle which sections, columns, and row filters appear in the generated
          cost comparison report. Changes apply to the next report generation
          (including "Regenerate report"). Totals are always computed over all
          rows — settings only change what is displayed.
        </p>

        {data && (
          <>
            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 mb-8">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm">Settings version</span>
                <span className="font-mono font-semibold text-[#00C2BB]">{data.version}</span>
              </div>
              <p className="text-xs text-gray-500">
                SHA-256 of sorted key=value pairs. Changes when any setting value changes.
              </p>
            </div>

            {groups.map((group) => {
              const settingsInGroup = data.settings.filter((s) => s.group_name === group)
              const groupLabel = group === 'sections' ? 'Sections' : group === 'columns' ? 'Columns' : 'Rows'
              return (
                <div key={group} className="bg-white/[0.03] border border-white/10 rounded-lg p-6 mb-8">
                  <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                    {groupLabel}
                  </h2>
                  <table className="w-full text-left text-sm border-collapse">
                    <thead>
                      <tr className="border-b border-white/10">
                        <th className="pb-3 text-gray-400 w-1/3">Setting</th>
                        <th className="pb-3 text-gray-400 w-1/6">Value</th>
                        <th className="pb-3 text-gray-400 w-1/6">Range</th>
                        <th className="pb-3 text-gray-400 w-1/3">Description</th>
                        <th className="pb-3 text-gray-400">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {settingsInGroup.map((s) => {
                        const isBool = s.value_type === 'bool'
                        const isChecked = s.value === 'true'
                        return (
                          <tr key={s.key} className="border-b border-white/5">
                            <td className="py-3">
                              <div className="font-semibold text-white">{s.title}</div>
                              <div className="text-xs text-gray-500">{s.key}</div>
                              <div className="text-xs text-gray-500 mt-1">{s.description}</div>
                            </td>
                            <td className="py-3">
                              {isBool ? (
                                <input
                                  type="checkbox"
                                  checked={isChecked}
                                  onChange={(e) => {
                                    const val = e.target.checked ? 'true' : 'false'
                                    handleSave(s, val)
                                  }}
                                  className="rounded bg-white/[0.05] border border-white/10 text-[#00C2BB] focus:ring-0"
                                />
                              ) : (
                                <input
                                  type="text"
                                  value={editingValue === s.key ? editingValue : s.value}
                                  onChange={(e) => setEditingValue(e.target.value)}
                                  onBlur={() => {
                                    if (editingValue === s.key) {
                                      handleSave(s, editingValue)
                                      setEditingValue('')
                                    }
                                  }}
                                  className="bg-white/[0.05] border border-white/10 rounded px-2 py-1 text-white w-24"
                                />
                              )}
                            </td>
                            <td className="py-3 text-gray-400 text-xs">
                              {isBool ? 'true / false' : `[${s.min_value ?? '–'} – ${s.max_value ?? '–'}]`}
                            </td>
                            <td className="py-3">{s.value_type}</td>
                            <td className="py-3">
                              {saving === s.key ? (
                                <button
                                  disabled
                                  className="px-3 py-1.5 rounded bg-[#645DF6]/50 text-white text-xs font-medium"
                                >
                                  Saving…
                                </button>
                              ) : (
                                <>
                                  {isBool ? null : (
                                    <button
                                      onClick={() => {
                                        setEditingValue(s.value)
                                      }}
                                      className="px-3 py-1.5 rounded bg-[#645DF6] text-white text-xs font-medium hover:bg-[#645DF6]/80"
                                    >
                                      Edit
                                    </button>
                                  )}
                                  {editingValue === s.key ? (
                                    <button
                                      onClick={() => {
                                        handleSave(s, editingValue)
                                        setEditingValue('')
                                      }}
                                      className="ml-2 px-3 py-1.5 rounded bg-[#00C2BB] text-white text-xs font-medium hover:bg-[#00C2BB]/80"
                                    >
                                      Save
                                    </button>
                                  ) : null}
                                </>
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

            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 mb-8">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                Change history
              </h2>
              {history.length === 0 ? (
                <p className="text-sm text-gray-500">No changes yet.</p>
              ) : (
                <table className="w-full text-left text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="pb-3 text-gray-400">When</th>
                      <th className="pb-3 text-gray-400">Key</th>
                      <th className="pb-3 text-gray-400">Old → New</th>
                      <th className="pb-3 text-gray-400">Changed by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.id} className="border-b border-white/5">
                        <td className="py-3 text-gray-400 text-xs">{h.changed_at}</td>
                        <td className="py-3 font-mono text-xs text-gray-300">{h.key}</td>
                        <td className="py-3 text-xs">
                          <span className="text-gray-500">{h.old_value}</span>
                          {' → '}
                          <span className="text-[#00C2BB]">{h.new_value}</span>
                        </td>
                        <td className="py-3 text-gray-400 text-xs">{h.changed_by}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6">
              <button
                onClick={() => setResetConfirmOpen(true)}
                className="w-full py-2.5 rounded bg-red-900/30 border border-red-900/50 text-red-400 text-xs font-medium hover:bg-red-900/50 hover:text-red-300 cursor-pointer"
              >
                Reset all settings to defaults
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
          </> 
        )}
      </div>
    </Layout>
  )
}