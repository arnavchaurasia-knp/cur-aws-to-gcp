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
    let text = ''
    if (data.entries) {
      text = data.entries.map(e => e.raw || e.message).join('\n')
    } else if (data.lines) {
      text = data.lines.join('\n')
    } else if (data.records) {
      text = JSON.stringify(data.records, null, 2)
    }
    navigator.clipboard.writeText(text)
    setCopied(true)
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
                className={`px-3 py-1 rounded-md font-medium transition-colors ${
                  stream === s
                    ? 'bg-[#645DF6] text-white shadow-sm'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={copyLogs}
            className="px-3 py-1.5 rounded-lg border border-white/10 text-xs font-medium text-gray-300 hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
          >
            {copied ? '✓ Copied' : 'Copy'}
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
      <div className="bg-black/60 border border-white/10 rounded-lg p-4 font-mono text-xs max-h-[500px] overflow-y-auto space-y-1">
        {loading && !data && (
          <p className="text-gray-500">Loading logs...</p>
        )}

        {data && !data.available && (
          <p className="text-gray-500 italic">No log entries available for stream "{stream}".</p>
        )}

        {/* Structured pipeline entries */}
        {data?.entries && data.entries.length > 0 && (
          data.entries.map((e, idx) => {
            let color = 'text-gray-300'
            if (e.level === 'error' || e.level === 'critical') color = 'text-red-400 font-semibold'
            else if (e.level === 'warning' || e.level === 'warn') color = 'text-yellow-400'
            else if (e.level === 'info') color = 'text-cyan-300'
            else if (e.level === 'debug') color = 'text-gray-500'

            return (
              <div key={idx} className="flex items-start gap-2 leading-relaxed hover:bg-white/[0.02] py-0.5 px-1 rounded">
                {e.timestamp && <span className="text-gray-500 select-none text-[11px] shrink-0">{e.timestamp}</span>}
                <span className={`uppercase text-[10px] font-bold px-1 rounded bg-white/5 shrink-0 ${color}`}>
                  {e.level}
                </span>
                {e.logger && <span className="text-[#645DF6] shrink-0 font-medium">{e.logger}</span>}
                <span className={`break-all ${color}`}>{e.message || e.raw}</span>
              </div>
            )
          })
        )}

        {/* Engine / raw lines */}
        {data?.lines && data.lines.length > 0 && (
          data.lines.map((line, idx) => (
            <div key={idx} className="text-gray-300 leading-relaxed whitespace-pre-wrap break-all hover:bg-white/[0.02]">
              {line}
            </div>
          ))
        )}

        {/* Diagnostics structured JSONL */}
        {data?.records && data.records.length > 0 && (
          <div className="space-y-2">
            {data.records.map((rec, idx) => (
              <pre key={idx} className="text-emerald-300 bg-white/5 p-2 rounded text-[11px] overflow-x-auto">
                {JSON.stringify(rec, null, 2)}
              </pre>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
