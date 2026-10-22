import { useEffect, useState } from 'react'
import { Nav } from '../components/Nav'
import {
  getAssumptions,
  putAssumption,
  getAssumptionHistory,
  type AssumptionHistory,
  type AssumptionsResponse,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

export function AdminAssumptions({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Assumptions — admin')
  const [data, setData] = useState<AssumptionsResponse | null>(null)
  const [history, setHistory] = useState<AssumptionHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null) // key being saved
  const [editReason, setEditReason] = useState('')

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getAssumptions()
      setData(d)
      const h = await getAssumptionHistory()
      setHistory(h)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load assumptions')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const handleSave = async (key: string, value: string) => {
    const num = Number(value)
    if (!Number.isFinite(num)) {
      setError('value must be a number')
      return
    }
    const reason = editReason.trim()
    if (!reason) {
      setError('a reason is required to change an assumption')
      return
    }
    setSaving(key)
    setError(null)
    try {
      const result = await putAssumption(key, { value: num, reason })
      if (result) {
        setEditReason('')
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

  if (loading) {
    return (
      <div className="min-h-screen text-white">
        <Nav user={user} />
        <div className="max-w-2xl mx-auto px-6 py-10">
          <p className="text-sm text-gray-500">Loading…</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen text-white">
        <Nav user={user} />
        <div className="max-w-2xl mx-auto px-6 py-10">
          <p className="text-xs text-orange-400">{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen text-white">
      <Nav user={user} />
      <div className="max-w-4xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-semibold mb-1">Pricing assumptions</h1>
        <p className="text-sm text-gray-500 mb-8">
          Human-chosen conversion values used by the projection pipeline. Each
          assumption is versioned; editing one changes the version hash for all
          subsequent jobs. Values are clamped to the allowed range.
        </p>

        {data && (
          <>
            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 mb-8">
              <div className="flex items-center justify-between mb-4">
                <span className="text-gray-400 text-sm">Version</span>
                <span className="font-mono font-semibold text-[#00C2BB]">{data.version}</span>
              </div>
              <p className="text-xs text-gray-500">
                SHA-256 of sorted key=value pairs (first 12 hex chars). Changes
                when any assumption value changes.
              </p>
            </div>

            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6 mb-8">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
                Current values
              </h2>
              <table className="w-full text-left text-sm border-collapse">
                <thead>
                  <tr className="border-b border-white/10">
                    <th className="pb-3 text-gray-400">Title</th>
                    <th className="pb-3 text-gray-400">Value</th>
                    <th className="pb-3 text-gray-400">Unit</th>
                    <th className="pb-3 text-gray-400">Range</th>
                    <th className="pb-3 text-gray-400">Last changed</th>
                    <th className="pb-3 text-gray-400">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.assumptions.map((a) => (
                    <tr key={a.key} className="border-b border-white/5">
                      <td className="py-3">
                        <div className="font-semibold text-white">{a.title}</div>
                        <div className="text-xs text-gray-500">{a.key}</div>
                        <div className="text-xs text-gray-500 mt-1">{a.rationale}</div>
                      </td>
                      <td className="py-3">
                        {saving === a.key ? (
                          <input
                            type="number"
                            step="any"
                            min={a.min_value}
                            max={a.max_value}
                            value={a.value}
                            className="bg-white/[0.05] border border-white/10 rounded px-2 py-1 text-white w-24"
                            onChange={(e) => setEditReason(e.target.value)}
                            disabled
                          />
                        ) : (
                          <span className="font-mono tabular-nums text-[#00C2BB]">{a.value}</span>
                        )}
                      </td>
                      <td className="py-3 text-gray-400">{a.unit}</td>
                      <td className="py-3 text-gray-400 text-xs">
                        [{a.min_value} – {a.max_value}]
                      </td>
                      <td className="py-3 text-gray-400 text-xs">
                        {a.updated_by || '—'}<br />
                        {a.updated_at || '—'}
                      </td>
                      <td className="py-3">
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            step="any"
                            min={a.min_value}
                            max={a.max_value}
                            defaultValue={a.value}
                            className="bg-white/[0.05] border border-white/10 rounded px-2 py-1 text-white w-24"
                          />
                          <button
                            onClick={(e) => {
                              const btn = e.currentTarget as HTMLButtonElement
                              const input =
                                btn.parentElement?.querySelector(
                                  'input[type="number"]'
                                ) as HTMLInputElement | null
                              const val = input?.value
                              if (val != null) {
                                handleSave(a.key, val)
                              }
                            }}
                            disabled={saving !== null}
                            className="px-3 py-1.5 rounded bg-[#645DF6] text-white text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[#645DF6]/80"
                          >
                            Save
                          </button>
                          <input
                            type="text"
                            placeholder="reason…"
                            value={editReason}
                            onChange={(e) => setEditReason(e.target.value)}
                            className="bg-white/[0.05] border border-white/10 rounded px-2 py-1 text-white w-32"
                          />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="bg-white/[0.03] border border-white/10 rounded-lg p-6">
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
                      <th className="pb-3 text-gray-400">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.id} className="border-b border-white/5">
                        <td className="py-3 text-gray-400 text-xs">
                          {h.changed_at}
                        </td>
                        <td className="py-3 font-mono text-xs text-gray-300">
                          {h.key}
                        </td>
                        <td className="py-3 text-xs">
                          <span className="text-gray-500">{h.old_value}</span>
                          {' → '}
                          <span className="text-[#00C2BB]">{h.new_value}</span>
                        </td>
                        <td className="py-3 text-gray-400 text-xs">
                          {h.changed_by}
                        </td>
                        <td className="py-3 text-gray-300 text-xs truncate max-w-[200px]">
                          {h.reason}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}