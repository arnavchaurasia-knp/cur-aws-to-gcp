import type { Job } from '../api/jobs'
import { deleteJob } from '../api/jobs'
import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { toast } from 'sonner'
import { ConfirmDialog } from './ConfirmDialog'

const statusColor: Record<string, string> = {
  pending:   'text-gray-400',
  running:   'text-[#00C2BB]',
  done:      'text-[#00C2BB]',
  failed:    'text-orange-400',
  cancelled: 'text-gray-400',
}

function StatusBadge({ status }: Readonly<{ status: string }>) {
  if (status === 'running') {
    return (
      <span className="flex items-center gap-1.5 text-[#00C2BB]">
        <span className="relative flex w-2 h-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00C2BB] opacity-60" />
          <span className="relative inline-flex rounded-full w-2 h-2 bg-[#00C2BB]" />
        </span>{' '}
        Running
      </span>
    )
  }
  const icon: Record<string, string> = { done: '✓', failed: '✕', pending: '–', cancelled: '⊘' }
  const label = status.charAt(0).toUpperCase() + status.slice(1)
  return (
    <span className={`flex items-center gap-1 ${statusColor[status] ?? 'text-gray-400'}`}>
      <span className="text-xs font-bold">{icon[status] ?? ''}</span>
      {label}
    </span>
  )
}

export function JobList({ jobs, onJobDeleted }: Readonly<{ jobs: Job[]; onJobDeleted?: (id: string) => void }>) {
  const nav = useNavigate()
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<{ id: string; prospect: string } | null>(null)

  const confirmDelete = async () => {
    if (!pendingDelete) return
    const { id, prospect } = pendingDelete
    setDeletingId(id)
    try {
      await deleteJob(id)
      toast.success(`Report for "${prospect}" deleted`)
      setPendingDelete(null)
      if (onJobDeleted) {
        onJobDeleted(id)
      } else {
        window.location.reload()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed')
    } finally {
      setDeletingId(null)
    }
  }

  const handleDelete = (e: React.MouseEvent, id: string, prospect: string) => {
    e.stopPropagation()
    setPendingDelete({ id, prospect })
  }

  if (!jobs.length) return <p className="text-sm text-gray-500">No reports yet</p>
  return (
    <>
    <ul className="divide-y divide-white/10 text-sm">
      {jobs.map((j, i) => {
        const isTerminal = j.status === 'done' || j.status === 'failed' || j.status === 'cancelled'
        return (
          <li
            key={j.id}
            role="button"
            tabIndex={0}
            className="group flex justify-between items-center py-2 cursor-pointer
              hover:bg-white/[0.04] px-1 rounded transition-colors duration-150
              anim-fade-in-up"
            style={{ animationDelay: `${i * 55}ms` }}
            onClick={() => nav(`/jobs/${j.id}`)}
            onKeyDown={e => { if (e.key === 'Enter') nav(`/jobs/${j.id}`) }}
          >
            <span className="text-gray-200 truncate max-w-[130px] font-medium" title={j.prospect}>
              {j.prospect}
            </span>
            <div className="flex items-center gap-2">
              <StatusBadge status={j.status} />
              {isTerminal && (
                <button
                  type="button"
                  title="Delete report"
                  onClick={e => handleDelete(e, j.id, j.prospect)}
                  disabled={deletingId === j.id}
                  className="opacity-0 group-hover:opacity-100 p-1 text-gray-500 hover:text-red-400 rounded transition-opacity duration-150 disabled:opacity-40"
                  aria-label="Delete report"
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                    <path d="M2.5 4h11m-9.5 0v9a1 1 0 001 1h6a1 1 0 001-1v-9M5.5 4V2.5a1 1 0 011-1h3a1 1 0 011 1V4" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
    <ConfirmDialog
      open={pendingDelete !== null}
      onOpenChange={(open) => {
        if (!open) setPendingDelete(null)
      }}
      title="Delete report"
      description={`Delete report for "${pendingDelete?.prospect}"? This action cannot be undone.`}
      confirmLabel="Delete"
      destructive
      loading={deletingId !== null}
      onConfirm={confirmDelete}
    />
    </>
  )
}
