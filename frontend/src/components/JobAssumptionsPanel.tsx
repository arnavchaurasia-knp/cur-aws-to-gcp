import { useEffect, useState } from 'react'
import {
  getJobAssumptions,
  rerunJobAssumptions,
  type JobAssumption,
  type JobAssumptionsResponse,
} from '../api/jobs'
import { CATEGORY_ORDER, CATEGORY_META } from '../lib/assumptionsConstants'
import { toast } from 'sonner'

interface Props {
  jobId: string
  canEdit: boolean
  onRerunStarted?: () => void
}

export function JobAssumptionsPanel({ jobId, canEdit, onRerunStarted }: Readonly<Props>) {
  const [data, setData] = useState<JobAssumptionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rerunning, setRerunning] = useState(false)
  const [reason, setReason] = useState('')
  // Modified values: key -> string representation
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggleCollapse = (cat: string) => {
    setCollapsed(prev => ({ ...prev, [cat]: !prev[cat] }))
  }

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await getJobAssumptions(jobId)
      setData(resp)
      // Initialize edits from current effective values so the inputs are populated
      const initial: Record<string, string> = {}
      for (const a of resp.assumptions) {
        initial[a.key] = String(a.value)
      }
      setEdits(initial)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load job assumptions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [jobId])

  if (loading) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <div className="flex items-center justify-center py-8">
          <div className="flex gap-2">
            {[0, 1, 2, 3].map((i, idx) => (
              <div
                key={i}
                className="w-2 h-2 rounded-full bg-[#00C2BB] animate-pulse"
                style={{ animationDelay: `${idx * 100}ms` }}
              />
            ))}
          </div>
        </div>
        <p className="text-xs text-gray-500 text-center">Loading job assumptions…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <div className="text-center py-4">
          <p className="text-xs text-orange-400">{error}</p>
          <button
            onClick={loadData}
            className="mt-2 text-xs text-[#00C2BB] hover:text-white underline cursor-pointer"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!data) return null

  const assumptionsList = data.assumptions || []
  const categoriesList = (data.categories && data.categories.length > 0) ? data.categories : CATEGORY_ORDER

  // Calculate which assumptions are currently modified relative to default values
  const dirtyKeys: string[] = []
  const overridesToSend: Record<string, number> = {}
  let validationError: string | null = null

  for (const a of assumptionsList) {
    const key = a.key
    const minVal = a.min_value
    const maxVal = a.max_value
    const defaultVal = a.default_value
    const editStr = edits[key]
    if (editStr !== undefined) {
      const num = Number(editStr)
      if (Number.isFinite(num)) {
        if (num < minVal || num > maxVal) {
          validationError = `${a.title}: value ${num} is out of allowed range [${minVal}, ${maxVal}]`
        }
        if (num !== defaultVal) {
          dirtyKeys.push(key)
        }
        // Always include modified values in overrides payload
        if (num !== a.value || a.is_overridden) {
          overridesToSend[key] = num
        }
      }
    }
  }

  const handleValueChange = (key: string, val: string) => {
    setEdits(prev => ({ ...prev, [key]: val }))
  }

  const handleResetToDefault = (a: JobAssumption) => {
    setEdits(prev => ({ ...prev, [a.key]: String(a.default_value) }))
  }

  const handleRerun = async () => {
    if (!canEdit) return
    if (validationError) {
      toast.error(validationError)
      return
    }
    setRerunning(true)
    setError(null)
    try {
      await rerunJobAssumptions(jobId, overridesToSend, reason.trim())
      toast.success('Re-run triggered! Pipeline is executing with your assumptions.')
      onRerunStarted?.()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Re-run failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setRerunning(false)
    }
  }

  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5 anim-fade-in-up">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4 pb-3 border-b border-white/10">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider">
              Pricing Assumptions &amp; Sizing Knobs
            </h3>
            {data.version && (
              <span className="text-[10px] font-mono text-gray-400 bg-white/5 px-2 py-0.5 rounded border border-white/10">
                v{data.version.slice(0, 14)}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1">
            Override conversion values (e.g. NAT VM-count, Glue vCPU, discount percentages) for <b>this job only</b>.
            Re-running will re-execute the pricing pipeline and regenerate the report.
          </p>
        </div>

        {canEdit && (
          <button
            onClick={handleRerun}
            disabled={rerunning || !!validationError}
            className="px-4 py-2 rounded-lg bg-[#645DF6] hover:bg-[#645DF6]/90 text-white text-xs font-medium disabled:opacity-40 transition-colors cursor-pointer shrink-0"
          >
            {rerunning ? 'Launching Re-run…' : 'Re-run with my assumptions'}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
          {error}
        </div>
      )}

      {validationError && (
        <div className="mb-4 p-2.5 rounded-lg bg-orange-950/30 border border-orange-500/30 text-orange-400 text-xs">
          ⚠️ {validationError}
        </div>
      )}

      {canEdit && (
        <div className="mb-4 bg-white/[0.01] border border-white/5 rounded-lg p-3">
          <label className="block text-xs text-gray-400 mb-1" htmlFor="rerun-reason">
            Optional rationale / notes for this re-run:
          </label>
          <input
            id="rerun-reason"
            type="text"
            placeholder="e.g. Adjusted NAT VM count to match 32 microservices topology"
            value={reason}
            onChange={e => setReason(e.target.value)}
            className="w-full bg-black/40 border border-white/10 rounded px-3 py-1.5 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-[#00C2BB]"
          />
        </div>
      )}

      <div className="space-y-4">
        {categoriesList.map((catKey) => {
          const meta = CATEGORY_META[catKey] ?? { label: catKey.replace('_', ' '), desc: '' }
          const groupAssumptions = assumptionsList.filter(
            a => (a.category || 'other') === catKey
          )
          if (groupAssumptions.length === 0) return null

          const isCollapsed = collapsed[catKey]

          return (
            <div key={catKey} className="border border-white/10 rounded-lg overflow-hidden bg-white/[0.01]">
              <button
                type="button"
                onClick={() => toggleCollapse(catKey)}
                className="w-full px-4 py-3 bg-white/[0.03] hover:bg-white/[0.05] border-b border-white/10 flex items-center justify-between text-left transition-colors cursor-pointer"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-xs text-[#00C2BB] font-mono">
                    {isCollapsed ? '▶' : '▼'}
                  </span>
                  <div>
                    <span className="text-xs font-semibold text-white tracking-wide">
                      {meta.label}
                    </span>
                    <span className="ml-2 text-[10px] text-gray-400 font-mono bg-white/5 px-1.5 py-0.5 rounded-full border border-white/10">
                      {groupAssumptions.length}
                    </span>
                    {meta.desc && (
                      <span className="ml-2 text-[11px] text-gray-500 hidden sm:inline">{meta.desc}</span>
                    )}
                  </div>
                </div>
                <span className="text-[10px] text-gray-500">
                  {isCollapsed ? 'Expand' : 'Collapse'}
                </span>
              </button>

              {!isCollapsed && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-white/10 bg-white/[0.01]">
                        <th className="py-2.5 px-4 text-gray-400 font-medium">Assumption</th>
                        <th className="py-2.5 px-4 text-gray-400 font-medium">Effective Value</th>
                        <th className="py-2.5 px-4 text-gray-400 font-medium">Default</th>
                        <th className="py-2.5 px-4 text-gray-400 font-medium">Allowed Range</th>
                        <th className="py-2.5 px-4 text-gray-400 font-medium">Unit</th>
                        {canEdit && <th className="py-2.5 px-4 text-gray-400 font-medium text-right">Action</th>}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {groupAssumptions.map((a) => {
                        const key = a.key
                        const title = a.title
                        const rationale = a.rationale
                        const minVal = a.min_value
                        const maxVal = a.max_value
                        const defaultVal = a.default_value
                        const unit = a.unit
                        const isOverridden = a.is_overridden
                        const currentValStr = edits[key] ?? String(a.value)
                        const num = Number(currentValStr)
                        const isOutOfRange = Number.isFinite(num) && (num < minVal || num > maxVal)
                        const isModified = Number.isFinite(num) && num !== defaultVal

                        return (
                          <tr key={key} className="hover:bg-white/[0.01] transition-colors">
                            <td className="py-3 px-4">
                              <div className="font-medium text-white flex items-center gap-1.5">
                                {title}
                                {(isOverridden || isModified) && (
                                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-[#645DF6]/20 text-[#a5a1f8] border border-[#645DF6]/30">
                                    custom
                                  </span>
                                )}
                              </div>
                              <div className="text-[10px] text-gray-500 font-mono mt-0.5">{key}</div>
                              {rationale && (
                                <div className="text-[11px] text-gray-500 mt-1 max-w-sm truncate" title={rationale}>
                                  {rationale}
                                </div>
                              )}
                            </td>
                            <td className="py-3 px-4">
                              {canEdit ? (
                                <input
                                  type="number"
                                  step="any"
                                  min={minVal}
                                  max={maxVal}
                                  value={currentValStr}
                                  onChange={e => handleValueChange(key, e.target.value)}
                                  className={`w-28 px-2 py-1 rounded bg-black/40 border text-white font-mono text-xs focus:outline-none focus:border-[#00C2BB] ${
                                    isOutOfRange ? 'border-orange-500 bg-orange-950/20 text-orange-300' : 'border-white/10'
                                  }`}
                                />
                              ) : (
                                <span className="font-mono text-white">{currentValStr}</span>
                              )}
                            </td>
                            <td className="py-3 px-4 font-mono text-gray-400">
                              {defaultVal}
                            </td>
                            <td className="py-3 px-4 font-mono text-gray-500">
                              [{minVal}, {maxVal}]
                            </td>
                            <td className="py-3 px-4 text-gray-400">
                              {unit}
                            </td>
                            {canEdit && (
                              <td className="py-3 px-4 text-right">
                                {isModified && (
                                  <button
                                    type="button"
                                    onClick={() => handleResetToDefault(a)}
                                    className="text-[11px] text-gray-400 hover:text-white underline cursor-pointer"
                                  >
                                    Reset
                                  </button>
                                )}
                              </td>
                            )}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
