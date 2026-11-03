import { toast } from 'sonner'
import { useState, useEffect } from 'react'

export interface LogEntry {
  timestamp?: string
  level: string
  logger?: string
  message: string
  raw: string
}

export interface LogResponse {
  job_id: string
  stream: string
  available: boolean
  source: string
  truncated?: boolean
  total: number
  offset: number
  limit: number
  entries?: LogEntry[]
  lines?: string[]
  records?: any[]
  digest?: {
    job_id: string
    run_id: string
    error_count: number
    warn_count: number
    first_error?: string
    last_lines?: string
    recorded_at?: string
  }
}

export function JobLogsViewer({ jobId, isRunning }: { jobId: string; isRunning: boolean }) {
  const [stream, setStream] = useState<'pipeline' | 'engine' | 'diagnostics'>('pipeline')
  const [level, setLevel] = useState<string>('info')
  const [script, setScript] = useState<string>('')
  const [search, setSearch] = useState<string>('')
  const [data, setData] = useState<LogResponse | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [copied, setCopied] = useState<boolean>(false)

  const fetchLogs = async () => {
    try {
      const params = new URLSearchParams()
      params.set('stream', stream)
      if (stream === 'pipeline') {
        if (level) params.set('level', level)
        if (script) params.set('script', script)
        if (search) params.set('q', search)
      }
      params.set('limit', '500')
      const res = await fetch(`/api/jobs/${jobId}/logs?${params.toString()}`)
      if (res.ok) {
        const json: LogResponse = await res.json()
        setData(json)
      }
    } catch (e) {
      console.error('Failed to fetch job logs', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    fetchLogs()
  }, [jobId, stream, level, script, search])

  useEffect(() => {
    if (!isRunning) return
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [isRunning, jobId, stream, level, script, search])

  const copyLogs = () => {
    if (!data) return
    let content = ''
    if (data.entries) {
      content = data.entries.map(e => e.raw || e.message).join('\n')
    } else if (data.lines) {
      content = data.lines.join('\n')
    } else if (data.records) {
      content = JSON.stringify(data.records, null, 2)
    }
    navigator.clipboard.writeText(content)
    setCopied(true)
    toast.success('Logs copied to clipboard')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5 space-y-4">
      {/* Header controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">Stream:</span>
          <div className="inline-flex rounded-lg bg-white/5 p-1 border border-white/10 text-xs">
            {(['pipeline', 'engine', 'diagnostics'] as const).map(s => (
              <button
                key={s}
                onClick={() => setStream(s)}
                className={`px-3.5 py-1.5 rounded-md text-xs font-semibold transition-all duration-150 cursor-pointer ${
                  stream === s
                    ? 'bg-[#645DF6] !text-white shadow-md'
                    : 'text-slate-400 hover:!text-white hover:bg-white/10'
                }`}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={copyLogs}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all duration-150 cursor-pointer ${
              copied
                ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300'
                : 'border-white/10 text-gray-300 hover:text-white hover:bg-white/5'
            }`}
            title="Copy logs to clipboard"
          >
            {copied ? (
              <>
                <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span>Copied!</span>
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                </svg>
                <span>Copy</span>
              </>
            )}
          </button>
          <a
            href={`/api/jobs/${jobId}/logs?stream=${stream}&limit=2000`}
            target="_blank"
            rel="noreferrer"
            className="px-3 py-1.5 rounded-lg border border-white/10 text-xs font-medium text-gray-300 hover:text-white hover:bg-white/5 transition-colors"
          >
            Download full
          </a>
        </div>
      </div>

      {/* Filter bar for pipeline stream */}
      {stream === 'pipeline' && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-gray-400 mb-1">Min Level</label>
            <select
              value={level}
              onChange={e => setLevel(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white outline-none focus:border-[#645DF6]"
            >
              <option value="debug">DEBUG &amp; above</option>
              <option value="info">INFO &amp; above</option>
              <option value="warning">WARNING &amp; above</option>
              <option value="error">ERROR &amp; above</option>
            </select>
          </div>

          <div>
            <label className="block text-[11px] uppercase tracking-wider text-gray-400 mb-1">Script filter</label>
            <input
              type="text"
              placeholder="e.g. ingest.py"
              value={script}
              onChange={e => setScript(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 outline-none focus:border-[#645DF6]"
            />
          </div>

          <div>
            <label className="block text-[11px] uppercase tracking-wider text-gray-400 mb-1">Search text</label>
            <input
              type="text"
              placeholder="Filter by keyword..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 outline-none focus:border-[#645DF6]"
            />
          </div>
        </div>
      )}

      {/* Digest Notice Banner */}
      {data?.source === 'digest' && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex items-center justify-between">
          <span>⚠️ Full live logs expired; showing saved post-mortem digest.</span>
          {data.digest && (
            <span className="font-mono text-[11px]">
              Errors: {data.digest.error_count} | Warnings: {data.digest.warn_count}
            </span>
          )}
        </div>
      )}

      {/* Log Output Console */}
      <div
        data-terminal
        style={{ backgroundColor: "#07090e", color: "#f8fafc" }}
        className="dark-console border border-slate-800/80 rounded-xl p-4 font-mono text-xs max-h-[540px] overflow-y-auto space-y-0.5 shadow-2xl select-text"
      >
        {loading && !data && (
          <p className="text-gray-500">Loading logs...</p>
        )}

        {data && !data.available && (
          <p className="text-gray-500 italic">No log entries available for stream "{stream}".</p>
        )}

        {/* Structured pipeline entries */}
        {data?.entries && data.entries.length > 0 && (
          data.entries.map((e, idx) => {
            let badgeClass = 'text-cyan-300 bg-cyan-950/50 border border-cyan-800/40'
            if (e.level === 'error' || e.level === 'critical') badgeClass = 'text-rose-400 bg-rose-950/60 border border-rose-800/40 font-bold'
            else if (e.level === 'warning' || e.level === 'warn') badgeClass = 'text-amber-400 bg-amber-950/60 border border-amber-800/40 font-semibold'
            else if (e.level === 'debug') badgeClass = 'text-slate-400 bg-slate-900 border border-slate-700'

            return (
              <div key={idx} className="flex items-start gap-2.5 leading-relaxed hover:bg-white/[0.08] hover:border-l-2 hover:border-l-[#00C2BB] pl-1.5 py-1 px-2 rounded font-mono text-xs transition-all duration-100 group">
                {e.timestamp && <span className="text-slate-400 select-none text-[11px] shrink-0 font-mono w-[155px] min-w-[155px]">{e.timestamp}</span>}
                <span className={`uppercase text-[10px] px-1.5 py-0.5 rounded shrink-0 ${badgeClass}`}>
                  {e.level}
                </span>
                {e.logger && <span className="text-indigo-400 shrink-0 font-medium">{e.logger}</span>}
                <span className="break-all text-slate-200 leading-relaxed flex-1">{e.message || e.raw}</span>
              </div>
            )
          })
        )}

        {/* Engine / raw lines with intelligent syntax coloring */}
        {stream === 'engine' && data?.lines && data.lines.length > 0 && (
          <div className="space-y-0.5">
            {data.lines.map((line, idx) => {
              const isError = /error|exception|traceback|fatal|failed/i.test(line)
              const isWarn = /warn|warning/i.test(line)
              const isInfo = /info|notice|step|phase/i.test(line)
              const isSuccess = /done|success|completed|ok\b/i.test(line)

              let lineClass = 'text-slate-300'
              if (isError) lineClass = 'text-rose-400 font-medium bg-rose-950/30 rounded px-1.5'
              else if (isWarn) lineClass = 'text-amber-300 font-medium'
              else if (isSuccess) lineClass = 'text-emerald-400'
              else if (isInfo) lineClass = 'text-cyan-300'

              return (
                <div key={idx} className="flex items-start gap-2.5 py-1 px-2 rounded hover:bg-white/[0.08] hover:border-l-2 hover:border-l-[#645DF6] pl-1.5 transition-all duration-100 font-mono text-xs leading-relaxed group">
                  <span className="text-slate-500 group-hover:text-slate-300 select-none text-[11px] w-9 text-right shrink-0 font-mono">{idx + 1}</span>
                  <span className={`break-all flex-1 ${lineClass}`}>{line}</span>
                </div>
              )
            })}
          </div>
        )}

        {/* Diagnostics structured JSONL cards */}
        {stream === 'diagnostics' && data?.records && data.records.length > 0 && (
          <div className="space-y-2.5">
            {data.records.map((rec, idx) => (
              <div key={idx} className="border border-slate-800 bg-[#0d1017] rounded-xl p-3.5 space-y-1.5">
                <div className="flex items-center justify-between text-xs border-b border-slate-800/80 pb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-slate-500 text-[11px]">#{idx + 1}</span>
                    <span className="px-2 py-0.5 rounded bg-[#645DF6]/20 text-[#a5a0ff] font-semibold text-[11px] border border-[#645DF6]/30 uppercase">
                      {rec.phase || rec.event || rec.type || 'Event'}
                    </span>
                  </div>
                  {rec.timestamp && (
                    <span className="text-slate-400 font-mono text-[11px]">{rec.timestamp}</span>
                  )}
                </div>
                <pre className="text-xs font-mono text-emerald-400 overflow-x-auto whitespace-pre-wrap leading-relaxed pt-1">
                  {JSON.stringify(rec, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
