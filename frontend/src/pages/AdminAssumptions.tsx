import { useEffect, useState, useMemo } from 'react'
import { Layout } from '../components/Layout'
import {
  getAssumptions,
  putAssumption,
  getAssumptionHistory,
  type AssumptionHistory,
  type AssumptionsResponse,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'

export function AdminAssumptions({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Assumptions — admin')
  const [data, setData] = useState<AssumptionsResponse | null>(null)
  const [history, setHistory] = useState<AssumptionHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null) // key being saved
  // Per-row edit state: key -> { value, reason }
  const [edits, setEdits] = useState<Record<string, { value: string; reason: string }>>({})

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

  const getEdit = (key: string) => edits[key] ?? { value: '', reason: '' }

  const handleValueChange = (key: string, val: string) => {
    setEdits(prev => ({ ...prev, [key]: { ...prev[key], value: val } }))
  }

  const handleReasonChange = (key: string, val: string) => {
    setEdits(prev => ({ ...prev, [key]: { ...prev[key], reason: val } }))
  }

  const handleSave = async (key: string) => {
    const edit = getEdit(key)
    const num = Number(edit.value)
    if (!Number.isFinite(num)) {
      setError('value must be a number')
      return
    }
    if (!edit.reason.trim()) {
      setError('a reason is required to change an assumption')
      return
    }
    const assumption = data?.assumptions.find(a => a.key === key)
    if (!assumption) return
    if (num < assumption.min_value || num > assumption.max_value) {
      setError(`value must be between ${assumption.min_value} and ${assumption.max_value}`)
      return
    }
    setSaving(key)
    setError(null)
    try {
      await putAssumption(key, { value: num, reason: edit.reason.trim() })
      setEdits(prev => { const next = { ...prev }; delete next[key]; return next })
      await refresh()
      toast.success('Assumption updated')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'save failed')
    } finally {
      setSaving(null)
    }
  }

  if (loading) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10">
          <div className="flex items-center justify-center h-32">
            <div className="flex gap-2">
              {[0,1,2,3].map((i, idx) => (
                <div key={i} className="w-2 h-2 rounded-full bg-[#00C2BB] animate-pulse" style={{ animationDelay: `${idx * 100}ms` }} />
              ))}
            </div>
          </div>
          <p className="text-sm text-gray-500 text-center mt-2">Loading assumptions…</p>
        </div>
      </Layout>
    )
  }

  if (error) {
    return (
      <Layout user={user}>
        <div className="max-w-2xl mx-auto px-6 py-10 text-center">
          <p className="text-xs text-orange-400">{error}</p>
          <button
            onClick={() => { setError(null); refresh() }}
            className="mt-3 text-xs text-[#00C2BB] hover:text-white underline"
          >
            Retry
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Pricing Assumptions</h1>
            <p className="text-sm text-gray-400 mt-1">
              Human-chosen conversion values used by the projection pipeline. Each
              assumption is versioned — editing one changes the version hash for all
              subsequent jobs. Values are clamped to the allowed range.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-[#00C2BB] bg-[#00C2BB]/10 px-2 py-1 rounded">
              v{data.version}
            </span>
            <p className="text-xs text-gray-500 hidden sm:block">Version hash of all assumption values</p>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        {/* Current Values */}
        <div className="mb-8">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Current Values</h2>
            <p className="text-xs text-gray-500 mt-1">
              Click a value to edit. All changes require a reason and are recorded in the history below.
            </p>
          </div>

          <div className="overflow-x-auto border border-white/10 rounded-lg">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Assumption</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Current Value</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Unit</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Allowed Range</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Last Updated</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {data.assumptions.map((a) => {
                  const isEditing = saving === a.key
                  const edit = getEdit(a.key)
                  const inRange = !edit.value || (Number(edit.value) >= a.min_value && Number(edit.value) <= a.max_value)
                  return (
                    <tr key={a.key} className="hover:bg-white/[0.01] transition-colors group">
                      <td className="px-5 py-4">
                        <div>
                          <div className="font-semibold text-white">{a.title}</div>
                          <div className="text-xs text-gray-500 mt-0.5">{a.key}</div>
                          <div className="text-xs text-gray-500 mt-1 max-w-xs truncate" title={a.rationale}>{a.rationale}</div>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        {isEditing ? (
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              step="any"
                              min={a.min_value}
                              max={a.max_value}
                              value={edit.value}
                              onChange={e => handleValueChange(a.key, e.target.value)}
                              className="bg-white/[0.05] border border-white/10 rounded px-2.5 py-1.5 text-white w-24 text-sm focus:border-[#645DF6] outline-none"
                            />
                            {edit.value && !inRange && (
                              <span className="text-xs text-red-400">out of range</span>
                            )}
                          </div>
                        ) : (
                          <span className="font-mono tabular-nums text-[#00C2BB]">{a.value}</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-gray-400 text-xs">{a.unit}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs">
                        [{a.min_value} – {a.max_value}]
                      </td>
                      <td className="px-5 py-4 text-gray-400 text-xs">
                        <div>{a.updated_by || '—'}</div>
                        <div className="text-[10px] text-gray-500">{a.updated_at || '—'}</div>
                      </td>
                      <td className="px-5 py-4 text-right">
                        {isEditing ? (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => handleSave(a.key)}
                              disabled={!edit.value || !edit.reason.trim() || !inRange}
                              className="px-2.5 py-1.5 rounded bg-[#645DF6] text-white text-[10px] font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[#645DF6]/80 transition-colors"
                            >
                              Save
                            </button>
                            <button
                              onClick={() => setEdits(prev => { const next = { ...prev }; delete next[a.key]; return next })}
                              className="px-2.5 py-1.5 rounded text-gray-400 hover:text-white hover:bg-white/5 text-[10px] transition-colors"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setEdits(prev => ({ ...prev, [a.key]: { value: String(a.value), reason: '' } }))}
                            className="px-2.5 py-1.5 rounded border border-white/10 text-gray-300 hover:text-white hover:border-white/30 text-[10px] transition-colors"
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
        </div>

        {/* Change History */}
        <div className="border border-white/10 rounded-lg overflow-hidden">
          <div className="bg-white/[0.02] border-b border-white/10 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Change History</h2>
            <p className="text-xs text-gray-500 mt-1">
              {history.length} change{history.length !== 1 ? 's' : ''} recorded
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">When</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Assumption</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Old → New</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Changed By</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {history.length === 0 ? (
                  <tr>
                    <td className="px-5 py-6 text-center text-gray-500 text-xs">No changes yet.</td>
                  </tr>
                ) : (
                  history.map((h) => (
                    <tr key={h.id} className="hover:bg-white/[0.01] transition-colors">
                      <td className="px-5 py-3 text-gray-400 text-xs whitespace-nowrap">{h.changed_at}</td>
                      <td className="px-5 py-3 font-mono text-xs text-gray-300">{h.key}</td>
                      <td className="px-5 py-3 text-xs">
                        <span className="text-gray-500">{h.old_value}</span>{' '}
                        <span className="text-[#00C2BB] font-medium">{'→ ' + h.new_value}</span>
                      </td>
                      <td className="px-5 py-3 text-gray-400 text-xs">{h.changed_by}</td>
                      <td className="px-5 py-3 text-gray-300 text-xs truncate max-w-[250px]" title={h.reason}>{h.reason}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Layout>
  )
}
