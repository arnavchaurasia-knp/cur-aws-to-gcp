import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Nav } from '../components/Nav'
import { JobList } from '../components/JobList'
import { RunHistory } from '../components/RunHistory'
import { Summary } from '../components/Summary'
import { ContactCard } from '../components/ContactCard'
import { getJob, getProgress, listJobs, downloadURL, refineJob, retryJob, getRuns, getSummary } from '../api/jobs'
import type { Job, Progress, RunResult } from '../api/jobs'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

function formatDollars(n: number): string {
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function pctVsAws(gcp: number, aws: number): { label: string; positive: boolean } | null {
  if (!aws || aws <= 0) return null
  const diff = (gcp - aws) / aws
  const sign = diff >= 0 ? '+' : ''
  return { label: `${sign}${Math.round(diff * 100)}%`, positive: diff < 0 }
}

function TotalsCard({ run, fallback }: { run: RunResult | null; fallback: number | null }) {
  if (!run) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-4">
          Cost projection
        </h3>
        <div className="flex justify-between items-center">
          <span className="text-gray-400">AWS Monthly Spend (pre-tax)</span>
          <span className="text-white font-semibold text-lg">
            {fallback != null ? `$${fallback.toLocaleString()}` : '—'}
          </span>
        </div>
      </div>
    )
  }
  const rows: Array<{ label: string; value: number; compare: boolean }> = [
    { label: 'AWS Monthly Spend (pre-tax)', value: run.aws_total, compare: false },
    { label: 'GCP On-Demand', value: run.gcp_od, compare: true },
    { label: 'GCP 1-Year CUD', value: run.gcp_1yr_cud, compare: true },
    { label: 'GCP 3-Year CUD', value: run.gcp_3yr_cud, compare: true },
  ]
  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-4">
        Cost projection
      </h3>
      <div className="divide-y divide-white/5">
        {rows.map(r => {
          const pct = r.compare ? pctVsAws(r.value, run.aws_total) : null
          
          let valColorClass = 'text-white'
          if (r.compare && pct) {
            valColorClass = pct.positive ? 'text-emerald-400' : 'text-orange-400'
          }

          return (
            <div key={r.label}
              className="flex flex-col sm:flex-row sm:items-center sm:justify-between py-2.5 gap-1">
              <span className="text-gray-400 text-sm">{r.label}</span>
              <div className="flex items-baseline gap-3">
                <span className={`font-semibold ${valColorClass}`}>
                  {formatDollars(r.value)}
                </span>
                {pct && (
                  <span className={`text-xs font-medium ${
                    pct.positive ? 'text-emerald-400' : 'text-orange-400'
                  }`}>
                    {pct.label}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const PHASE_LABELS: Record<number, string> = {
  1: 'Loading and classifying your AWS bill',
  2: 'Mapping AWS line items to GCP services',
  3: 'Reviewing mappings for sanity',
  4: 'Applying GCP pricing (on-demand + CUDs)',
  5: 'Investigating anomalies',
  6: 'Generating the HTML report',
}

export function JobStatus({ user }: { user: UserInfo }) {
  const { id } = useParams<{ id: string }>()
  const [job, setJob] = useState<Job | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [progress, setProgress] = useState<Progress | null>(null)
  const [runs, setRuns] = useState<RunResult[]>([])
  const [summary, setSummary] = useState<string | null>(null)
  const [refineOpen, setRefineOpen] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [refining, setRefining] = useState(false)
  const [refineError, setRefineError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)

  const submitRetry = async () => {
    if (!id) return
    setRetrying(true)
    setRetryError(null)
    try {
      await retryJob(id)
      setJob(prev => prev ? { ...prev, status: 'running', error: '', aws_spend: null } : prev)
      setProgress(null)
      setRuns([])
      setSummary(null)
    } catch (e: unknown) {
      setRetryError(e instanceof Error ? e.message : String(e))
    } finally {
      setRetrying(false)
    }
  }

  const submitRefine = async () => {
    if (!id || instruction.trim().length < 3) return
    setRefining(true)
    setRefineError(null)
    try {
      await refineJob(id, instruction.trim())
      // Optimistic flip — the backend has already set status=running before
      // returning 202, but updating local state in the same batch avoids the
      // flicker of the done card briefly reappearing.
      setJob(prev => prev ? { ...prev, status: 'running', aws_spend: null, error: '' } : prev)
      setProgress(null)
      setRuns([])
      setSummary(null)
      setRefineOpen(false)
      setInstruction('')
    } catch (e: unknown) {
      setRefineError(e instanceof Error ? e.message : String(e))
    } finally {
      setRefining(false)
    }
  }
  useTitle(job ? `${job.prospect} · ${job.status.charAt(0).toUpperCase() + job.status.slice(1)}` : 'Loading')

  useEffect(() => { listJobs().then(setJobs) }, [])

  useEffect(() => {
    if (!id) return
    let cancelled = false
    let prevStatus: string | null = null
    const poll = async () => {
      if (cancelled) return
      const j = await getJob(id).catch(() => null)
      if (!j || cancelled) return
      setJob(j)
      setJobs(prev => prev.map(old => old.id === j.id ? { ...old, status: j.status } : old))
      if (j.status === 'running' || j.status === 'pending') {
        getProgress(id).then(p => { if (!cancelled && p) setProgress(p) })
        setTimeout(poll, 5000)
      } else if (j.status === 'done' && prevStatus !== 'done') {
        // First time we see done (or first poll where it's done) — fetch
        // runs + summary. Latest run summary by default.
        getRuns(id).then(r => { if (!cancelled) setRuns(r) })
        getSummary(id).then(s => { if (!cancelled) setSummary(s) })
      }
      prevStatus = j.status
    }
    poll()
    return () => { cancelled = true }
  }, [id, refining, retrying])

  if (!job) return <div className="min-h-screen bg-[#0a0a0f] text-white"><Nav user={user} /></div>

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <Nav user={user} />
      <div className="max-w-5xl mx-auto px-6 py-10 grid gap-8 lg:grid-cols-[1fr_320px]">
        <main className="flex flex-col gap-6">
          <h1 className="text-2xl font-semibold">
            Report for <span className="text-[#645DF6]">{job.prospect}</span>
          </h1>

        {(job.status === 'pending' || job.status === 'running') && (
          <div className="bg-[#645DF6]/10 border border-[#645DF6]/30 rounded-lg p-4 text-sm">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2 h-2 rounded-full bg-[#00C2BB] animate-pulse" />
              <strong className="text-[#00C2BB]">AI agent running</strong>
            </div>
            <p className="text-gray-300">An AI agent is analyzing your bill and mapping each AWS line item to its GCP equivalent. This typically takes 10–30 minutes.</p>
            <p className="text-gray-400 text-xs mt-1">We'll email you when it's ready — you can close this tab.</p>
            {progress && progress.transcript_ok && progress.phase_number > 0 && (
              <div className="mt-3 pt-3 border-t border-[#645DF6]/20">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs uppercase tracking-wider text-gray-400">Currently</span>
                  <span className="text-xs text-gray-500">Step {progress.phase_number} of 6</span>
                </div>
                <p className="text-sm text-gray-200">{PHASE_LABELS[progress.phase_number]}</p>
                <div className="mt-3 flex gap-1">
                  {[1, 2, 3, 4, 5, 6].map(n => (
                    <div key={n}
                      className={`h-1 flex-1 rounded-full ${
                        n < progress.phase_number ? 'bg-[#00C2BB]'
                        : n === progress.phase_number ? 'bg-[#00C2BB] animate-pulse'
                        : 'bg-white/10'
                      }`} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {job.status === 'done' && (
          <>
            <TotalsCard run={runs[0] ?? null} fallback={job.aws_spend} />

            <p className="text-xs text-gray-500 leading-relaxed -mt-2">
              AI-generated estimate — verify before sharing. If a mapping looks off,
              hit "Refine this report" below and tell the agent what to change.
            </p>

            {summary && <Summary markdown={summary} />}

            <a href={downloadURL(job.id)}
              className="w-full py-3 rounded-lg font-medium text-white text-center block
                bg-gradient-to-r from-[#645DF6] to-[#00C2BB]">
              ↓ Download latest report
            </a>

            <RunHistory jobId={job.id} runs={runs} />

            {!refineOpen ? (
              <button
                onClick={() => setRefineOpen(true)}
                className="text-sm text-[#645DF6] hover:text-[#7d77f8] hover:underline self-start">
                Refine this report →
              </button>
            ) : (
              <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5 space-y-3">
                <div>
                  <h3 className="text-sm font-semibold text-[#00C2BB] uppercase tracking-wider mb-1">
                    Refine the projection
                  </h3>
                  <p className="text-xs text-gray-500">
                    Tell the AI what to change. It will resume the same session, update mappings, recompute,
                    and rewrite the report. Original is preserved if refinement fails.
                  </p>
                </div>
                <textarea
                  value={instruction}
                  onChange={e => setInstruction(e.target.value)}
                  placeholder="e.g. Map gp3 EBS volumes to pd-standard instead of pd-ssd."
                  rows={3}
                  className="w-full bg-white/5 border border-white/20 rounded-lg px-3 py-2 text-sm outline-none focus:border-[#645DF6] resize-y"
                />
                {refineError && <p className="text-xs text-orange-400">{refineError}</p>}
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={() => { setRefineOpen(false); setInstruction(''); setRefineError(null) }}
                    className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition">
                    Cancel
                  </button>
                  <button
                    onClick={submitRefine}
                    disabled={refining || instruction.trim().length < 3}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white
                      bg-gradient-to-r from-[#645DF6] to-[#00C2BB]
                      disabled:opacity-40 disabled:cursor-not-allowed transition">
                    {refining ? 'Submitting…' : 'Submit refinement'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}

        {job.status === 'failed' && (
          <div className="bg-orange-400/10 border border-orange-400/30 rounded-lg p-4 text-sm space-y-3">
            <div>
              <strong className="text-orange-400">Report generation failed</strong>
              {job.error && (
                <p className="text-gray-400 text-xs mt-1 font-mono break-words">{job.error}</p>
              )}
            </div>
            {retryError && <p className="text-xs text-orange-400">{retryError}</p>}
            <button
              onClick={submitRetry}
              disabled={retrying}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white
                bg-gradient-to-r from-[#645DF6] to-[#00C2BB]
                disabled:opacity-40 disabled:cursor-not-allowed transition">
              {retrying ? 'Retrying…' : '↻ Retry'}
            </button>
          </div>
        )}

          <Link to="/" className="text-xs text-[#645DF6] hover:underline">← New estimation</Link>
        </main>
        <aside className="lg:border-l lg:border-white/10 lg:pl-8 flex flex-col gap-6">
          <div>
            <p className="text-xs uppercase tracking-wider text-gray-400 mb-3">Your Reports</p>
            <JobList jobs={jobs} />
          </div>
          <ContactCard />
        </aside>
      </div>
    </div>
  )
}
