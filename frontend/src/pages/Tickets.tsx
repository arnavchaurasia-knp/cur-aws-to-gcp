import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { createTicket, listTickets, updateTicket, deleteTicket, type Ticket } from '../api/admin'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { BackButton } from '../components/BackButton'
import { triggerNotificationRefresh } from '../lib/notificationsContext'
import { triggerTicketsRefresh } from '../lib/ticketsContext'

function statusColor(status: string): string {
  switch (status) {
    case 'open': return 'text-[#00C2BB]'
    case 'in_progress': return 'text-[#645DF6]'
    case 'resolved': return 'text-[#10B981]'
    case 'closed': return 'text-gray-400'
    default: return 'text-gray-400'
  }
}

function priorityColor(priority: string): string {
  switch (priority) {
    case 'high': return 'text-red-400'
    case 'medium': return 'text-orange-400'
    case 'low': return 'text-gray-400'
    default: return 'text-gray-400'
  }
}

export function Tickets({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Support tickets')
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [updating, setUpdating] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [newPriority, setNewPriority] = useState('medium')
  const [newCategory, setNewCategory] = useState('general')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editStatus, setEditStatus] = useState('open')
  const [editPriority, setEditPriority] = useState('medium')
  const [editCategory, setEditCategory] = useState('general')
  const [error, setError] = useState<string | null>(null)
  const [resolveTarget, setResolveTarget] = useState<Ticket | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const [resolving, setResolving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Ticket | null>(null)
  const [viewTicket, setViewTicket] = useState<Ticket | null>(null)
  const [deleting, setDeleting] = useState(false)

  const closeViewTicket = () => {
    setViewTicket(null)
    const next = new URLSearchParams(searchParams)
    if (next.has('ticket') || next.has('ticketId') || next.has('id')) {
      next.delete('ticket')
      next.delete('ticketId')
      next.delete('id')
      setSearchParams(next, { replace: true })
    }
  }

  const handleResolve = async () => {
    if (!resolveTarget) return
    setResolving(true)
    try {
      await updateTicket(resolveTarget.id, { status: 'resolved' })
      toast.success(`Ticket "${resolveTarget.title}" marked as resolved`)
      setResolveTarget(null)
      await refresh()
      triggerNotificationRefresh()
      triggerTicketsRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'failed to resolve ticket')
    } finally {
      setResolving(false)
    }
  }

  const handleReopen = async (ticket: Ticket) => {
    try {
      await updateTicket(ticket.id, { status: 'open' })
      toast.success(`Ticket "${ticket.title}" reopened`)
      await refresh()
      triggerNotificationRefresh()
      triggerTicketsRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'failed to reopen ticket')
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteTicket(deleteTarget.id)
      toast.success(`Deleted ticket "${deleteTarget.title}"`)
      setDeleteTarget(null)
      await refresh()
      triggerNotificationRefresh()
      triggerTicketsRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'failed to delete ticket')
    } finally {
      setDeleting(false)
    }
  }

  const isAdmin = Boolean(user?.is_admin)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listTickets()
      setTickets(res.tickets)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load tickets')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  // Auto-open ticket modal if ?ticket=<id> or ?ticketId=<id> is present in the URL
  useEffect(() => {
    const targetTicketId = searchParams.get('ticket') || searchParams.get('ticketId') || searchParams.get('id')
    if (targetTicketId && tickets.length > 0) {
      const match = tickets.find(t => t.id === targetTicketId || t.id.startsWith(targetTicketId))
      if (match) {
        setViewTicket(match)
      }
    }
  }, [searchParams, tickets])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTitle.trim() || !newDescription.trim()) return
    setCreating(true)
    setError(null)
    try {
      await createTicket({
        title: newTitle.trim(),
        description: newDescription.trim(),
        priority: newPriority,
        category: newCategory,
      })
      setNewTitle('')
      setNewDescription('')
      setNewPriority('medium')
      setNewCategory('general')
      await refresh()
      triggerNotificationRefresh()
      triggerTicketsRefresh()
      toast.success('Ticket created')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to create ticket')
    } finally {
      setCreating(false)
    }
  }

  const openEdit = (ticket: Ticket) => {
    setEditingId(ticket.id)
    setEditTitle(ticket.title)
    setEditDescription(ticket.description)
    setEditStatus(ticket.status)
    setEditPriority(ticket.priority)
    setEditCategory(ticket.category)
  }

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingId) return
    setUpdating(editingId)
    setError(null)
    try {
      await updateTicket(editingId, {
        title: editTitle.trim() || undefined,
        description: editDescription.trim() || undefined,
        status: editStatus,
        priority: editPriority,
        category: editCategory,
      })
      setEditingId(null)
      await refresh()
      triggerNotificationRefresh()
      triggerTicketsRefresh()
      toast.success('Ticket updated')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to update ticket')
    } finally {
      setUpdating(null)
    }
  }

  const filtered = tickets

  return (
    <Layout user={user}>
      <div className="max-w-5xl mx-auto px-6 py-8">
        <BackButton fallback={isAdmin ? "/admin" : "/"} />
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Support Tickets</h1>
            <p className="text-sm text-gray-400">
              Raise a ticket for anything — billing questions, report issues, feature requests, or general support.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        {/* Create Ticket Form */}
        <div className="bg-white/[0.02] border border-white/10 rounded-xl p-6 mb-8">
          <h2 className="text-sm font-semibold text-white mb-4">Raise a New Ticket</h2>
          <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="col-span-full sm:col-span-2 lg:col-span-2">
              <label className="block text-xs text-gray-400 mb-1">Title</label>
              <input
                type="text"
                value={newTitle}
                onChange={e => setNewTitle(e.target.value)}
                placeholder="What do you need help with?"
                className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] placeholder-[var(--fcp-text-tertiary)] focus:border-[#645DF6] transition-colors"
              />
            </div>
            <div className="col-span-full sm:col-span-2 lg:col-span-2">
              <label className="block text-xs text-gray-400 mb-1">Description</label>
              <textarea
                value={newDescription}
                onChange={e => setNewDescription(e.target.value)}
                placeholder="Describe your issue or request in detail..."
                rows={3}
                className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] placeholder-[var(--fcp-text-tertiary)] focus:border-[#645DF6] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Priority</label>
              <select
                value={newPriority}
                onChange={e => setNewPriority(e.target.value)}
                className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors cursor-pointer"
              >
                <option value="low" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Low</option>
                <option value="medium" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Medium</option>
                <option value="high" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">High</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Category</label>
              <select
                value={newCategory}
                onChange={e => setNewCategory(e.target.value)}
                className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors cursor-pointer"
              >
                <option value="general" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">General</option>
                <option value="billing" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Billing</option>
                <option value="technical" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Technical</option>
                <option value="feature" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Feature Request</option>
              </select>
            </div>
            <div className="col-span-full sm:col-span-2 lg:col-span-2">
              <button
                type="submit"
                disabled={creating}
                className="w-full btn-shimmer py-2 rounded-lg text-sm font-medium text-white cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {creating ? 'Creating…' : 'Raise Ticket'}
              </button>
            </div>
          </form>
        </div>

        {/* Tickets List */}
        <div className="bg-white/[0.02] border border-white/10 rounded-xl overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-gray-400 text-sm">Loading tickets…</div>
          ) : filtered.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-sm">No tickets yet.</div>
          ) : (
            <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.04] text-xs uppercase tracking-wider text-gray-400 border-b border-white/10">
                <tr>
                  <th className="text-left px-3 py-3 font-medium">Title</th>
                  <th className="text-left px-3 py-3 font-medium">Status</th>
                  <th className="text-left px-3 py-3 font-medium">Priority</th>
                  <th className="text-left px-3 py-3 font-medium">Category</th>
                  <th className="text-left px-3 py-3 font-medium">Created By</th>
                  <th className="text-left px-3 py-3 font-medium">Created</th>
                  <th className="text-right px-3 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filtered.map(t => (
                  <tr key={t.id} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-3 py-3 max-w-[20rem]">
                      <div className="text-[var(--fcp-text-primary)] font-medium truncate" title={t.title}>{t.title}</div>
                      {t.description && (
                        <div className="text-xs text-[var(--fcp-text-secondary)] mt-1 line-clamp-2 leading-relaxed" title={t.description}>
                          {t.description}
                        </div>
                      )}
                      <div className="text-[10px] font-mono text-[var(--fcp-text-tertiary)] mt-1 truncate">{t.id}</div>
                    </td>
                    <td className="px-3 py-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${statusColor(t.status)} border-current`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${priorityColor(t.priority)} border-current`}>
                        {t.priority}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs text-gray-400 capitalize">{t.category}</td>
                    <td className="px-3 py-3 text-xs text-gray-400 max-w-[12rem] truncate" title={t.created_by}>{t.created_by}</td>
                    <td className="px-3 py-3 text-xs text-gray-400 whitespace-nowrap">
                      {new Date(t.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-3 text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setViewTicket(t)}
                          className="text-xs px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white transition-colors cursor-pointer border border-white/10"
                          title="View full description and details"
                        >
                          View
                        </button>
                        <button
                          type="button"
                          onClick={() => openEdit(t)}
                          className="text-xs px-2 py-1 rounded border border-white/10 hover:border-white/30 text-gray-300 hover:text-white transition-colors cursor-pointer"
                        >
                          Edit
                        </button>
                        {t.status === 'resolved' ? (
                          (isAdmin || t.created_by === user?.email) && (
                            <button
                              type="button"
                              onClick={() => handleReopen(t)}
                              className="text-xs px-2.5 py-1 rounded bg-amber-500/15 hover:bg-amber-500/30 text-amber-300 transition-colors cursor-pointer border border-amber-500/30 font-medium"
                              title="Reopen ticket"
                            >
                              Reopen
                            </button>
                          )
                        ) : t.status !== 'closed' ? (
                          (isAdmin || t.created_by === user?.email) && (
                            <button
                              type="button"
                              onClick={() => setResolveTarget(t)}
                              className="text-xs px-2.5 py-1 rounded bg-[#10B981]/15 hover:bg-[#10B981]/30 text-[#10B981] transition-colors cursor-pointer border border-[#10B981]/30 font-medium"
                              title="Mark as resolved"
                            >
                              Resolve
                            </button>
                          )
                        ) : null}
                        {isAdmin && (
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(t)}
                            title="Delete ticket"
                            className="inline-flex items-center justify-center w-7 h-7 rounded border border-white/10 text-gray-400 hover:text-red-400 hover:border-red-500/40 transition-colors cursor-pointer"
                          >
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* Edit Ticket Dialog */}
        {editingId && (
          <div
            role="dialog"
            aria-modal="true"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4 anim-fade-in"
            onClick={e => {
              if ((e.target as HTMLElement).className.includes('fixed inset-0')) setEditingId(null)
            }}
          >
            <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-2xl shadow-2xl p-6 w-full max-w-md anim-scale-in text-[var(--fcp-text-primary)]">
              <div className="flex items-start justify-between gap-4 mb-4">
                <div>
                  <h2 className="text-lg font-semibold text-[var(--fcp-text-primary)]">Edit Ticket</h2>
                  <p className="text-xs text-[var(--fcp-text-secondary)]">{editingId}</p>
                </div>
                <button
                  onClick={() => setEditingId(null)}
                  aria-label="Close"
                  className="text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] text-xl leading-none transition-colors duration-150 cursor-pointer p-1">
                  ×
                </button>
              </div>
              <form onSubmit={handleUpdate} className="space-y-4">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Title</label>
                  <input
                    type="text"
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Description</label>
                  <textarea
                    value={editDescription}
                    onChange={e => setEditDescription(e.target.value)}
                    rows={4}
                    className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors"
                  />
                </div>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Status</label>
                    <select
                      value={editStatus}
                      onChange={e => setEditStatus(e.target.value)}
                      disabled={!isAdmin}
                      className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors disabled:opacity-50 cursor-pointer"
                    >
                      <option value="open" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Open</option>
                      <option value="in_progress" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">In Progress</option>
                      <option value="resolved" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Resolved</option>
                      <option value="closed" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Closed</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Priority</label>
                    <select
                      value={editPriority}
                      onChange={e => setEditPriority(e.target.value)}
                      className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors cursor-pointer"
                    >
                      <option value="low" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Low</option>
                      <option value="medium" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Medium</option>
                      <option value="high" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">High</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Category</label>
                    <select
                      value={editCategory}
                      onChange={e => setEditCategory(e.target.value)}
                      className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-2 text-sm text-[var(--fcp-text-primary)] focus:border-[#645DF6] transition-colors cursor-pointer"
                    >
                      <option value="general" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">General</option>
                      <option value="billing" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Billing</option>
                      <option value="technical" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Technical</option>
                      <option value="feature" className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">Feature Request</option>
                    </select>
                  </div>
                </div>
                <div className="flex items-center justify-end gap-3 pt-4 border-t border-[var(--fcp-border-subtle)]">
                  <button
                    type="button"
                    onClick={() => setEditingId(null)}
                    className="px-4 py-2 text-sm text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-hover)] rounded-lg border border-[var(--fcp-border-subtle)] transition-colors cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={updating === editingId}
                    className="px-5 py-2 rounded-lg text-sm font-medium text-white btn-shimmer cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {updating === editingId ? 'Saving…' : 'Save Changes'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        <ConfirmDialog
          open={!!resolveTarget}
          onOpenChange={(o) => { if (!o) setResolveTarget(null) }}
          title={`Mark "${resolveTarget?.title ?? ''}" as resolved?`}
          description="This marks the ticket as resolved."
          confirmLabel="Mark as resolved"
          loading={resolving}
          onConfirm={handleResolve}
        />

        <ConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
          title={`Delete "${deleteTarget?.title ?? ''}"?`}
          description="This permanently deletes this support ticket. This action cannot be undone."
          confirmLabel="Delete ticket"
          destructive
          loading={deleting}
          onConfirm={handleDelete}
        />

        {/* Ticket Full Details & Description Modal */}
        {viewTicket && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm anim-fade-in"
            onClick={e => { if (e.target === e.currentTarget) closeViewTicket() }}
            role="dialog"
            aria-modal="true"
          >
            <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-default)] rounded-2xl w-full max-w-2xl max-h-[88vh] flex flex-col shadow-2xl overflow-hidden text-[var(--fcp-text-primary)] anim-scale-in">
              {/* Header */}
              <div className="px-6 py-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between gap-4 bg-[var(--fcp-bg-raised)]">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(viewTicket.id)
                      toast.success('Ticket ID copied to clipboard')
                    }}
                    className="inline-flex items-center gap-1.5 font-mono text-xs px-2.5 py-1 rounded-md bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:border-[var(--fcp-border-default)] transition-colors cursor-pointer"
                    title="Click to copy Ticket ID"
                  >
                    <span>#{viewTicket.id.slice(0, 8)}</span>
                    <svg className="w-3 h-3 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                    </svg>
                  </button>
                  <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold border ${statusColor(viewTicket.status)} border-current tracking-wide`}>
                    {viewTicket.status.toUpperCase()}
                  </span>
                  <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold border ${priorityColor(viewTicket.priority)} border-current tracking-wide`}>
                    {viewTicket.priority.toUpperCase()} PRIORITY
                  </span>
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-medium bg-white/5 text-[var(--fcp-text-secondary)] capitalize border border-[var(--fcp-border-subtle)]">
                    {viewTicket.category}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      const url = `${window.location.origin}/tickets?ticket=${viewTicket.id}`
                      navigator.clipboard.writeText(url)
                      toast.success('Ticket link copied to clipboard')
                    }}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-subtle)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] transition-colors cursor-pointer"
                    title="Copy direct link to this ticket"
                  >
                    <span>🔗</span>
                    <span>Copy Link</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => closeViewTicket()}
                    className="text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] text-xl p-1 rounded transition-colors cursor-pointer"
                    title="Close modal (Esc)"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Content */}
              <div className="p-6 overflow-y-auto space-y-6 flex-1">
                <div>
                  <h2 className="text-xl font-bold text-[var(--fcp-text-primary)] leading-snug">
                    {viewTicket.title}
                  </h2>
                  <div className="flex items-center gap-3 text-xs text-[var(--fcp-text-secondary)] mt-3 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="w-6 h-6 rounded-full bg-[#645DF6]/20 border border-[#645DF6]/30 text-[#8981ff] flex items-center justify-center font-bold text-[10px] uppercase">
                        {(viewTicket.created_by || 'U')[0]}
                      </span>
                      <span>Created by <strong className="text-[var(--fcp-text-primary)]">{viewTicket.created_by}</strong></span>
                    </div>
                    <span>•</span>
                    <span className="font-mono text-[11px]">{new Date(viewTicket.created_at).toLocaleString()}</span>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] flex items-center gap-1.5">
                      <svg className="w-3.5 h-3.5 text-[var(--fcp-text-tertiary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" />
                      </svg>
                      Description &amp; Details
                    </label>
                  </div>
                  <div className="bg-[var(--fcp-bg-base)] border border-[var(--fcp-border-subtle)] rounded-xl p-5 text-sm leading-relaxed whitespace-pre-wrap font-sans text-[var(--fcp-text-primary)] min-h-[140px] shadow-inner select-text">
                    {viewTicket.description ? (
                      viewTicket.description
                    ) : (
                      <span className="text-[var(--fcp-text-tertiary)] italic">No detailed description was provided for this ticket.</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="px-6 py-4 border-t border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-raised)] flex items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => { const target = viewTicket; closeViewTicket(); openEdit(target) }}
                  className="text-xs px-3.5 py-2 rounded-lg border border-[var(--fcp-border-default)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-primary)] font-medium transition-colors cursor-pointer"
                >
                  Edit Ticket
                </button>
                <div className="flex items-center gap-2.5">
                  {viewTicket.status === 'resolved' ? (
                    (isAdmin || viewTicket.created_by === user?.email) && (
                      <button
                        type="button"
                        onClick={() => { handleReopen(viewTicket); closeViewTicket() }}
                        className="text-xs px-4 py-2 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 font-semibold transition-colors cursor-pointer border border-amber-500/30"
                      >
                        Reopen Ticket
                      </button>
                    )
                  ) : viewTicket.status !== 'closed' ? (
                    (isAdmin || viewTicket.created_by === user?.email) && (
                      <button
                        type="button"
                        onClick={() => { setResolveTarget(viewTicket); closeViewTicket() }}
                        className="text-xs px-4 py-2 rounded-lg bg-[#10B981]/15 hover:bg-[#10B981]/25 text-[#10B981] font-semibold transition-colors cursor-pointer border border-[#10B981]/30"
                      >
                        Resolve Ticket
                      </button>
                    )
                  ) : null}
                  <button
                    type="button"
                    onClick={() => closeViewTicket()}
                    className="text-xs px-4 py-2 rounded-lg bg-[var(--fcp-bg-subtle)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] border border-[var(--fcp-border-subtle)] transition-colors cursor-pointer"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}      </div>
    </Layout>
  )
}
