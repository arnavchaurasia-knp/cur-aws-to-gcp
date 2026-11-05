import { useEffect, useState, useMemo } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  getJobLineItems,
  overrideLineItem,
  deleteLineItemOverride,
  type LineItem,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'

export function ReportOverride({ user }: Readonly<{ user: UserInfo }>) {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const targetRow = searchParams.get('row')

  useTitle(`Review & Overrides — ${id?.slice(0, 8)}`)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<LineItem[]>([])
  const [totalAws, setTotalAws] = useState(0)
  const [totalGcp, setTotalGcp] = useState(0)

  // Filters
  const [search, setSearch] = useState('')
  const [strategyFilter, setStrategyFilter] = useState<string>('all')

  // Edit state
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editGcpService, setEditGcpService] = useState('')
  const [editRate, setEditRate] = useState<string>('')
  const [editNotes, setEditNotes] = useState('')
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  const loadData = async () => {
    if (!id) return
    setLoading(true)
    setError(null)
    try {
      const data = await getJobLineItems(id)
      setItems(data.items || [])
      setTotalAws(data.total_aws || 0)
      setTotalGcp(data.total_gcp || 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load line items')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [id])

  // Scroll to targeted row if query param provided
  useEffect(() => {
    if (targetRow && items.length > 0) {
      const el = document.getElementById(`row-${encodeURIComponent(targetRow)}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('bg-cyan-500/10')
        setTimeout(() => el.classList.remove('bg-cyan-500/10'), 3000)
      }
    }
  }, [targetRow, items])

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      if (strategyFilter === 'suppressed' && !item.is_suppressed) return false
      if (strategyFilter === 'mapped' && item.strategy !== 'map') return false
      if (strategyFilter === 'pass' && item.strategy !== 'pass') return false
      if (strategyFilter === 'ignore' && item.strategy !== 'ignore') return false

      if (search.trim()) {
        const q = search.toLowerCase()
        const text = `${item.product} ${item.usage_type} ${item.operation || ''} ${item.gcp_service || ''} ${item.notes || ''}`.toLowerCase()
        if (!text.includes(q)) return false
      }
      return true
    })
  }, [items, strategyFilter, search])

  const startEdit = (item: LineItem) => {
    setEditingKey(item.aws_li_key)
    setEditGcpService(item.gcp_service || '')
    setEditRate(item.manual_rate != null ? String(item.manual_rate) : '')
    setEditNotes(item.notes || '')
  }

  const handleSaveOverride = async (key: string, isSuppressed?: boolean) => {
    if (!id) return
    setSavingKey(key)
    try {
      const rateNum = editRate ? parseFloat(editRate) : undefined
      await overrideLineItem(id, key, {
        gcp_service: editGcpService || undefined,
        manual_rate: rateNum,
        is_suppressed: isSuppressed,
        notes: editNotes || undefined,
      })
      setToastMsg('Override applied. Deterministic recalculation triggered.')
      setTimeout(() => setToastMsg(null), 4000)
      setEditingKey(null)
      loadData()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Override failed')
    } finally {
      setSavingKey(null)
    }
  }

  const handleToggleSuppress = async (item: LineItem) => {
    if (!id) return
    const newSuppressed = !item.is_suppressed
    setSavingKey(item.aws_li_key)
    try {
      await overrideLineItem(id, item.aws_li_key, {
        is_suppressed: newSuppressed,
        notes: newSuppressed ? 'Manually suppressed by operator' : '',
      })
      setToastMsg(newSuppressed ? 'Line item suppressed ($0 GCP cost).' : 'Line item unsuppressed.')
      setTimeout(() => setToastMsg(null), 3000)
      loadData()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setSavingKey(null)
    }
  }

  const handleReset = async (key: string) => {
    if (!id) return
    if (!confirm('Reset this line item to default automated mapping?')) return
    setSavingKey(key)
    try {
      await deleteLineItemOverride(id, key)
      setToastMsg('Override deleted. Restored automated mapping.')
      setTimeout(() => setToastMsg(null), 3000)
      loadData()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setSavingKey(null)
    }
  }

  return (
    <Layout user={user}>
      <div className="max-w-7xl mx-auto px-6 py-8">
        <BackButton fallback={`/jobs/${id}`} />

        {/* Top Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mt-2 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
                Report Overrides Console
              </h1>
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-secondary)] border border-[var(--fcp-border-subtle)]">
                Job: {id?.slice(0, 8)}
              </span>
            </div>
            <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1">
              Audit line items, force target GCP SKUs, suppress non-applicable charges, or adjust unit rates with instant recalculation.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Link
              to={`/jobs/${id}`}
              className="px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer"
            >
              ← Back to Report
            </Link>
            <button
              onClick={loadData}
              disabled={loading}
              className="px-3.5 py-1.5 rounded-lg bg-[var(--fcp-accent)] text-white text-xs font-medium hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
            >
              ↻ Refresh Data
            </button>
          </div>
        </div>

        {toastMsg && (
          <div className="p-3 mb-6 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center justify-between anim-fade-in-up">
            <span>✓ {toastMsg}</span>
            <button onClick={() => setToastMsg(null)} className="hover:text-emerald-200 cursor-pointer">×</button>
          </div>
        )}

        {/* Cost Summary Banner */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
            <div className="text-[10px] uppercase font-semibold text-[color:var(--fcp-text-tertiary)]">Total AWS Spend</div>
            <div className="text-xl font-bold text-[color:var(--fcp-text-primary)] mt-1 tabular-nums">
              ${totalAws.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
            <div className="text-[10px] uppercase font-semibold text-[color:var(--fcp-text-tertiary)]">Projected GCP Cost</div>
            <div className="text-xl font-bold text-cyan-400 mt-1 tabular-nums">
              ${totalGcp.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
            <div className="text-[10px] uppercase font-semibold text-[color:var(--fcp-text-tertiary)]">Line Items</div>
            <div className="text-xl font-bold text-[color:var(--fcp-text-primary)] mt-1 tabular-nums">
              {items.length} total
            </div>
          </div>
        </div>

        {/* Filter Bar */}
        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)] mb-6 flex flex-col md:flex-row items-center gap-4">
          <div className="relative flex-1 w-full">
            <input
              type="text"
              placeholder="Filter by product, usage type, or GCP service..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full px-3.5 py-2 text-xs rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:border-[var(--fcp-accent)]"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                className="absolute right-3 top-2.5 text-xs text-[color:var(--fcp-text-tertiary)] hover:text-[color:var(--fcp-text-primary)] cursor-pointer"
              >
                ✕
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 w-full md:w-auto">
            <span className="text-xs text-[color:var(--fcp-text-tertiary)]">Strategy:</span>
            <select
              value={strategyFilter}
              onChange={(e) => setStrategyFilter(e.target.value)}
              className="px-3 py-2 text-xs rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:border-[var(--fcp-accent)] cursor-pointer"
            >
              <option value="all">All Items ({items.length})</option>
              <option value="mapped">Mapped</option>
              <option value="pass">Passthrough</option>
              <option value="ignore">Ignored</option>
              <option value="suppressed">Suppressed</option>
            </select>
          </div>
        </div>

        {/* Table View */}
        {loading ? (
          <div className="py-20 text-center text-sm text-[color:var(--fcp-text-tertiary)]">
            Loading line items from DuckDB...
          </div>
        ) : error ? (
          <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-400 text-sm">
            {error}
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="py-16 text-center text-sm text-[color:var(--fcp-text-tertiary)] border border-[var(--fcp-border-subtle)] rounded-xl bg-[var(--fcp-bg-surface)]">
            No line items match the selected filter.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-tertiary)] font-medium">
                <tr>
                  <th className="py-3 px-4">AWS Line Item</th>
                  <th className="py-3 px-4 text-right">AWS Cost</th>
                  <th className="py-3 px-4">GCP Target Service</th>
                  <th className="py-3 px-4 text-right">GCP Cost</th>
                  <th className="py-3 px-4">Override Status</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                {filteredItems.map((item) => {
                  const isEditing = editingKey === item.aws_li_key
                  const hasOverride = item.is_suppressed || (item.manual_rate != null) || Boolean(item.notes)

                  return (
                    <tr
                      key={item.aws_li_key}
                      id={`row-${encodeURIComponent(item.aws_li_key)}`}
                      className={`transition-colors hover:bg-[var(--fcp-hover-overlay)] ${
                        item.is_suppressed ? 'opacity-50 line-through' : ''
                      }`}
                    >
                      {/* AWS Line Item Column */}
                      <td className="py-3 px-4 max-w-xs">
                        <div className="font-semibold text-[color:var(--fcp-text-primary)] truncate">
                          {item.product}
                        </div>
                        <div className="text-[11px] text-[color:var(--fcp-text-secondary)] font-mono truncate">
                          {item.usage_type}
                        </div>
                        {item.operation && (
                          <div className="text-[10px] text-[color:var(--fcp-text-tertiary)] truncate">
                            Op: {item.operation}
                          </div>
                        )}
                      </td>

                      {/* AWS Cost Column */}
                      <td className="py-3 px-4 text-right font-mono font-medium text-[color:var(--fcp-text-primary)]">
                        ${item.aws_amortized_cost.toFixed(2)}
                      </td>

                      {/* GCP Target Service Column */}
                      <td className="py-3 px-4">
                        {isEditing ? (
                          <div className="space-y-1.5">
                            <input
                              type="text"
                              value={editGcpService}
                              onChange={(e) => setEditGcpService(e.target.value)}
                              placeholder="e.g. Cloud Storage"
                              className="w-full px-2 py-1 text-xs rounded border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)]"
                            />
                            <input
                              type="text"
                              value={editRate}
                              onChange={(e) => setEditRate(e.target.value)}
                              placeholder="Override unit rate ($)"
                              className="w-full px-2 py-1 text-xs rounded border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)]"
                            />
                            <input
                              type="text"
                              value={editNotes}
                              onChange={(e) => setEditNotes(e.target.value)}
                              placeholder="Reason / Notes"
                              className="w-full px-2 py-1 text-xs rounded border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)]"
                            />
                          </div>
                        ) : (
                          <div>
                            <div className="font-medium text-[color:var(--fcp-text-primary)]">
                              {item.gcp_service || 'Unassigned'}
                            </div>
                            <div className="text-[10px] text-[color:var(--fcp-text-secondary)]">
                              Strategy: <span className="font-mono">{item.strategy}</span>
                            </div>
                            {item.notes && (
                              <div className="text-[10px] text-amber-400 mt-0.5 italic">
                                Note: {item.notes}
                              </div>
                            )}
                          </div>
                        )}
                      </td>

                      {/* GCP Cost Column */}
                      <td className="py-3 px-4 text-right font-mono font-medium text-cyan-400">
                        {item.gcp_projected_cost != null ? `$${item.gcp_projected_cost.toFixed(2)}` : '—'}
                      </td>

                      {/* Override Status Column */}
                      <td className="py-3 px-4">
                        {item.is_suppressed ? (
                          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">
                            Suppressed ($0)
                          </span>
                        ) : hasOverride ? (
                          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
                            Manual Override
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-tertiary)]">
                            Auto Mapped
                          </span>
                        )}
                      </td>

                      {/* Actions Column */}
                      <td className="py-3 px-4 text-right whitespace-nowrap">
                        {isEditing ? (
                          <div className="flex items-center justify-end gap-2">
                            <button
                              onClick={() => handleSaveOverride(item.aws_li_key)}
                              disabled={savingKey === item.aws_li_key}
                              className="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs cursor-pointer disabled:opacity-50"
                            >
                              Save
                            </button>
                            <button
                              onClick={() => setEditingKey(null)}
                              className="px-2 py-1 rounded border border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] text-xs cursor-pointer"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            <button
                              onClick={() => startEdit(item)}
                              className="px-2 py-1 rounded border border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)] hover:bg-[var(--fcp-hover-overlay)] text-xs cursor-pointer"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => handleToggleSuppress(item)}
                              disabled={savingKey === item.aws_li_key}
                              className={`px-2 py-1 rounded text-xs cursor-pointer ${
                                item.is_suppressed
                                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20'
                                  : 'bg-rose-500/10 text-rose-400 border border-rose-500/30 hover:bg-rose-500/20'
                              }`}
                            >
                              {item.is_suppressed ? 'Unsuppress' : 'Suppress'}
                            </button>
                            {hasOverride && (
                              <button
                                onClick={() => handleReset(item.aws_li_key)}
                                disabled={savingKey === item.aws_li_key}
                                className="px-2 py-1 rounded border border-amber-500/30 text-amber-400 hover:bg-amber-500/10 text-xs cursor-pointer"
                                title="Reset to auto mapping"
                              >
                                Reset
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Layout>
  )
}

