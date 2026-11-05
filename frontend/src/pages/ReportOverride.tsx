import { useEffect, useState, useMemo } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  getJobLineItems,
  overrideLineItem,
  deleteLineItemOverride,
  type LineItem,
} from '../api/admin'
import {
  getRefinementThread,
  proposeRefinement,
  confirmRefinement,
  answerClarification,
  revokeRefinement,
  applyRefinement,
  type RefinementThreadResponse,
  type Clarification,
  type Directive,
} from '../api/refinement'
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

  // Edit state (manual per-row)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editGcpService, setEditGcpService] = useState('')
  const [editRate, setEditRate] = useState<string>('')
  const [editNotes, setEditNotes] = useState('')
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  // Row selection for targeted refinement
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set())

  // Refinement thread state
  const [threadLoading, setThreadLoading] = useState(false)
  const [thread, setThread] = useState<RefinementThreadResponse | null>(null)
  const [promptInput, setPromptInput] = useState('')
  const [submittingPrompt, setSubmittingPrompt] = useState(false)
  const [clarificationAnswers, setClarificationAnswers] = useState<Record<string, string>>({})
  const [actionInProgress, setActionInProgress] = useState<string | null>(null)
  const [isRecalculating, setIsRecalculating] = useState(false)
  const [showThreadPanel, setShowThreadPanel] = useState(true)

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

  const loadThread = async () => {
    if (!id) return
    setThreadLoading(true)
    try {
      const th = await getRefinementThread(id)
      setThread(th)
    } catch {
      // refinement thread may not exist yet, that's normal
    } finally {
      setThreadLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    loadThread()
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
      loadThread()
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
      loadThread()
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
      loadThread()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setSavingKey(null)
    }
  }

  // Refinement actions
  const handlePropose = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!id || !promptInput.trim() || submittingPrompt) return
    setSubmittingPrompt(true)
    try {
      const selectedArr = Array.from(selectedKeys)
      const th = await proposeRefinement(id, promptInput.trim(), selectedArr)
      setThread(th)
      setPromptInput('')
      setToastMsg('Refinement proposal analyzed.')
      setTimeout(() => setToastMsg(null), 3000)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Proposal failed')
    } finally {
      setSubmittingPrompt(false)
    }
  }

  const handleConfirmDirective = async (directiveId: string) => {
    if (!id) return
    setActionInProgress(directiveId)
    try {
      const th = await confirmRefinement(id, directiveId)
      setThread(th)
      setToastMsg('Directive confirmed! Click "Apply Refinements & Recalculate" to rerun projection.')
      setTimeout(() => setToastMsg(null), 4000)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Confirmation failed')
    } finally {
      setActionInProgress(null)
    }
  }

  const handleAnswerClarification = async (clarificationId: string, answer: string) => {
    if (!id || !answer) return
    setActionInProgress(clarificationId)
    try {
      const th = await answerClarification(id, clarificationId, answer)
      setThread(th)
      setToastMsg('Clarification answered.')
      setTimeout(() => setToastMsg(null), 3000)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Clarification answer failed')
    } finally {
      setActionInProgress(null)
    }
  }

  const handleRevokeDirective = async (directiveId: string) => {
    if (!id) return
    if (!confirm('Revoke this directive? It will be marked superseded and reverted on recalculation.')) return
    setActionInProgress(directiveId)
    try {
      await revokeRefinement(id, directiveId)
      await loadThread()
      setToastMsg('Directive revoked. Click "Apply Refinements & Recalculate" to rerun projection.')
      setTimeout(() => setToastMsg(null), 4000)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Revocation failed')
    } finally {
      setActionInProgress(null)
    }
  }

  const handleApplyRefinements = async () => {
    if (!id || isRecalculating) return
    setIsRecalculating(true)
    try {
      await applyRefinement(id)
      setToastMsg('Refinements applied! Recalculation started in the background.')
      setTimeout(() => {
        loadData()
        loadThread()
        setIsRecalculating(false)
      }, 3000)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Apply failed')
      setIsRecalculating(false)
    }
  }

  const toggleSelectAll = () => {
    if (selectedKeys.size === filteredItems.length) {
      setSelectedKeys(new Set())
    } else {
      setSelectedKeys(new Set(filteredItems.map((i) => i.aws_li_key)))
    }
  }

  const toggleSelectRow = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const confirmedDirectives = useMemo(() => {
    return (thread?.directives || []).filter((d) => d.status === 'confirmed')
  }, [thread?.directives])

  const proposedDirectives = thread?.proposed_directives || []
  const openClarifications = thread?.open_clarifications || []

  return (
    <Layout user={user}>
      <div className="max-w-7xl mx-auto px-6 py-8">
        <BackButton fallback={`/jobs/${id}`} />

        {/* Top Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mt-2 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
                Report Overrides & Refinements
              </h1>
              <span className="font-mono text-xs px-2.5 py-0.5 rounded-full bg-[var(--fcp-hover-overlay)] text-[color:var(--fcp-text-secondary)] border border-[var(--fcp-border-subtle)]">
                Job: {id?.slice(0, 8)}
              </span>
            </div>
            <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1">
              Audit line items, refine mappings via conversational AGY prompts, force target GCP SKUs, or adjust rates with auditable reversibility.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowThreadPanel(!showThreadPanel)}
              className={`px-3.5 py-1.5 rounded-lg border text-xs font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
                showThreadPanel
                  ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-400'
                  : 'border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-[color:var(--fcp-text-primary)]'
              }`}
            >
              <span>💬 {showThreadPanel ? 'Hide Refinement Assistant' : 'Show Refinement Assistant'}</span>
              {openClarifications.length > 0 && (
                <span
                  title={`${openClarifications.length} unresolved clarification(s)`}
                  className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30"
                >
                  {openClarifications.length}
                </span>
              )}
            </button>
            <Link
              to={`/jobs/${id}`}
              className="px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer"
            >
              ← Back to Report
            </Link>
            <button
              onClick={() => {
                loadData()
                loadThread()
              }}
              disabled={loading || threadLoading}
              className="px-3.5 py-1.5 rounded-lg bg-[var(--fcp-accent)] text-white text-xs font-medium hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
            >
              ↻ Refresh Data
            </button>
          </div>
        </div>

        {toastMsg && (
          <div className="p-3 mb-6 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center justify-between anim-fade-in-up">
            <span>✓ {toastMsg}</span>
            <button onClick={() => setToastMsg(null)} className="hover:text-emerald-200 cursor-pointer">✕</button>
          </div>
        )}

        {/* Cost Summary Banner */}
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 mb-6">
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
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
            <div className="text-[10px] uppercase font-semibold text-[color:var(--fcp-text-tertiary)]">Active Directives</div>
            <div className="text-xl font-bold text-emerald-400 mt-1 tabular-nums">
              {confirmedDirectives.length} active
            </div>
          </div>
        </div>

        {/* Conversational Refinement Panel */}
        {showThreadPanel && (
          <div className="mb-8 rounded-xl border border-cyan-500/30 bg-gradient-to-b from-cyan-950/20 to-[var(--fcp-bg-surface)] p-5 shadow-lg">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-[var(--fcp-border-subtle)] gap-2">
              <div className="flex items-center gap-2">
                <span className="text-lg">🤖</span>
                <h2 className="font-semibold text-sm text-[color:var(--fcp-text-primary)]">
                  AGY Conversational Refinement
                </h2>
                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-mono">
                  Directive Layer Phase 2
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleApplyRefinements}
                  disabled={isRecalculating}
                  className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs transition-colors cursor-pointer disabled:opacity-50 shadow-sm"
                >
                  {isRecalculating ? 'Recalculating...' : '🚀 Apply Refinements & Recalculate'}
                </button>
              </div>
            </div>

            {/* Conversation History / Messages */}
            <div className="mt-4 space-y-3 max-h-72 overflow-y-auto pr-2">
              {(!thread || thread.messages.length === 0) ? (
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-secondary)]">
                  <p className="font-medium text-[color:var(--fcp-text-primary)]">Instruct the projection in natural language:</p>
                  <ul className="list-disc list-inside mt-1.5 space-y-1 text-[11px] text-[color:var(--fcp-text-tertiary)]">
                    <li>&ldquo;Map all RDS MySQL instances to Cloud SQL for MySQL&rdquo;</li>
                    <li>&ldquo;Ignore DataTransfer charges as non-workload&rdquo;</li>
                    <li>&ldquo;Set S3 standard storage rate to $0.02 per GB&rdquo;</li>
                    <li>&ldquo;Suppress the selected line items&rdquo; (select rows below)</li>
                  </ul>
                </div>
              ) : (
                thread.messages.map((m) => (
                  <div
                    key={m.id}
                    className={`p-3 rounded-lg text-xs ${
                      m.role === 'user'
                        ? 'bg-[var(--fcp-accent)]/15 border border-[var(--fcp-accent)]/30 text-[color:var(--fcp-text-primary)] ml-8'
                        : 'bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-secondary)] mr-8'
                    }`}
                  >
                    <div className="flex items-center justify-between text-[10px] text-[color:var(--fcp-text-tertiary)] mb-1">
                      <span className="font-semibold uppercase tracking-wider">
                        {m.role === 'user' ? 'You' : 'AGY Assistant'}
                      </span>
                      <span>{new Date(m.created_at).toLocaleTimeString()}</span>
                    </div>
                    <div className="whitespace-pre-wrap">{m.content}</div>
                  </div>
                ))
              )}
            </div>

            {/* Proposed Directives Waiting for Confirmation */}
            {proposedDirectives.length > 0 && (
              <div className="mt-4 pt-3 border-t border-[var(--fcp-border-subtle)] space-y-2">
                <div className="text-xs font-semibold text-cyan-400 flex items-center gap-1.5">
                  <span>⚡ Proposed Directives ({proposedDirectives.length})</span>
                  <span className="text-[10px] font-normal text-[color:var(--fcp-text-tertiary)]">
                    — review scope and confirm to stage into the ledger
                  </span>
                </div>
                {proposedDirectives.map((pd: Directive) => (
                  <div
                    key={pd.id}
                    className="p-3 rounded-lg border border-cyan-500/40 bg-cyan-950/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300">
                          {pd.action}
                        </span>
                        <span className="font-semibold text-xs text-[color:var(--fcp-text-primary)]">
                          {pd.intent || pd.source_utterance}
                        </span>
                      </div>
                      {pd.scope_estimate && (
                        <div className="text-[11px] text-[color:var(--fcp-text-secondary)] font-mono">
                          Impacts: <strong className="text-cyan-300">{pd.scope_estimate.affected_rows} rows</strong> ({`$${(pd.scope_estimate.monthly_aws_cost || 0).toFixed(2)}/mo AWS spend`})
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleConfirmDirective(pd.id)}
                        disabled={actionInProgress === pd.id}
                        className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs cursor-pointer disabled:opacity-50"
                      >
                        {actionInProgress === pd.id ? 'Confirming...' : '✓ Confirm Directive'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Open Clarifications */}
            {openClarifications.length > 0 && (
              <div className="mt-4 pt-3 border-t border-[var(--fcp-border-subtle)] space-y-2">
                <div className="text-xs font-semibold text-amber-400 flex items-center gap-1.5">
                  <span>❓ Clarifications Needed ({openClarifications.length})</span>
                </div>
                {openClarifications.map((cl: Clarification) => (
                  <div
                    key={cl.id}
                    className="p-3 rounded-lg border border-amber-500/40 bg-amber-950/20 space-y-2"
                  >
                    <div className="text-xs text-amber-200 font-medium">{cl.question}</div>
                    {cl.options && cl.options.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {cl.options.map((opt: string) => (
                          <button
                            key={opt}
                            onClick={() => handleAnswerClarification(cl.id, opt)}
                            disabled={actionInProgress === cl.id}
                            className="px-2.5 py-1 text-xs rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 cursor-pointer disabled:opacity-50"
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          placeholder="Type answer..."
                          value={clarificationAnswers[cl.id] || ''}
                          onChange={(e) =>
                            setClarificationAnswers({ ...clarificationAnswers, [cl.id]: e.target.value })
                          }
                          className="flex-1 px-2.5 py-1 text-xs rounded border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)]"
                        />
                        <button
                          onClick={() =>
                            handleAnswerClarification(cl.id, clarificationAnswers[cl.id] || '')
                          }
                          disabled={!clarificationAnswers[cl.id]?.trim() || actionInProgress === cl.id}
                          className="px-3 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium cursor-pointer disabled:opacity-50"
                        >
                          Submit
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Input Prompt Form */}
            <form onSubmit={handlePropose} className="mt-4 pt-3 border-t border-[var(--fcp-border-subtle)] flex gap-2">
              <input
                type="text"
                placeholder={
                  selectedKeys.size > 0
                    ? `Refine mapping for ${selectedKeys.size} selected row(s)...`
                    : 'Ask AGY to map, ignore, or rerate services...'
                }
                value={promptInput}
                onChange={(e) => setPromptInput(e.target.value)}
                disabled={submittingPrompt}
                className="flex-1 px-3.5 py-2 text-xs rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-canvas)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:border-cyan-500"
              />
              <button
                type="submit"
                disabled={!promptInput.trim() || submittingPrompt}
                className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs transition-colors cursor-pointer disabled:opacity-50"
              >
                {submittingPrompt ? 'Analyzing...' : 'Propose'}
              </button>
            </form>
          </div>
        )}

        {/* Active Applied Directives Accordion/Section */}
        {confirmedDirectives.length > 0 && (
          <div className="mb-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-4 shadow-[var(--fcp-card-shadow)]">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs font-semibold text-[color:var(--fcp-text-primary)] flex items-center gap-2">
                <span>📋 Active Directives Ledger ({confirmedDirectives.length})</span>
                <span className="text-[10px] text-[color:var(--fcp-text-tertiary)]">
                  Stored in directives.json with full provenance
                </span>
              </div>
              <button
                onClick={handleApplyRefinements}
                disabled={isRecalculating}
                className="text-xs text-emerald-400 hover:underline cursor-pointer"
              >
                Rerun Projection →
              </button>
            </div>
            <div className="divide-y divide-[var(--fcp-border-subtle)] max-h-48 overflow-y-auto">
              {confirmedDirectives.map((d: Directive) => (
                <div key={d.id} className="py-2.5 flex items-center justify-between text-xs gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        {d.action}
                      </span>
                      <span className="font-medium text-[color:var(--fcp-text-primary)] truncate">
                        {d.intent || d.source_utterance || d.id}
                      </span>
                    </div>
                    {d.source_utterance && (
                      <div className="text-[10px] text-[color:var(--fcp-text-tertiary)] italic truncate mt-0.5">
                        &ldquo;{d.source_utterance}&rdquo;
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => handleRevokeDirective(d.id)}
                    disabled={actionInProgress === d.id}
                    className="shrink-0 px-2.5 py-1 text-[11px] rounded border border-rose-500/30 text-rose-400 hover:bg-rose-500/10 cursor-pointer disabled:opacity-50"
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Filter Bar & Row Selection Info */}
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

          <div className="flex items-center gap-3 w-full md:w-auto">
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

            {selectedKeys.size > 0 && (
              <span className="text-xs px-2 py-1 rounded bg-cyan-500/20 text-cyan-300 font-mono">
                {selectedKeys.size} selected
              </span>
            )}
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
                  <th className="py-3 px-3 w-8 text-center">
                    <input
                      type="checkbox"
                      checked={selectedKeys.size === filteredItems.length && filteredItems.length > 0}
                      onChange={toggleSelectAll}
                      className="cursor-pointer"
                    />
                  </th>
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
                  const isSelected = selectedKeys.has(item.aws_li_key)

                  return (
                    <tr
                      key={item.aws_li_key}
                      id={`row-${encodeURIComponent(item.aws_li_key)}`}
                      className={`transition-colors hover:bg-[var(--fcp-hover-overlay)] ${
                        item.is_suppressed ? 'opacity-50 line-through' : ''
                      } ${isSelected ? 'bg-cyan-500/5' : ''}`}
                    >
                      {/* Checkbox Column */}
                      <td className="py-3 px-3 text-center">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelectRow(item.aws_li_key)}
                          className="cursor-pointer"
                        />
                      </td>

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
