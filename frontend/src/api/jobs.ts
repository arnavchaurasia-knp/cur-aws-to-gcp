// frontend/src/api/jobs.ts
export interface Job {
  id: string; owner: string; prospect: string; status: string
  aws_spend: number | null; gcp_od?: number | null; error: string; created_at: string
}

export interface RunResult {
  run_id: string
  ts_utc: string
  run_type: 'initial' | 'refinement'
  instruction: string | null
  aws_total: number
  gcp_od: number
  gcp_1yr_cud: number
  gcp_3yr_cud: number
  report_html: string
  report_md: string
  summary_md: string | null
  mapped_rows: number
  passthroughs: number
  confidence: string | null
}

export async function createJob(file: File, prospect: string): Promise<{ id: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('prospect_name', prospect)

  let res: Response
  try {
    res = await fetch('/api/jobs', { method: 'POST', body: form })
  } catch (netErr: any) {
    if (file.size > 100 * 1024 * 1024) {
      const sizeMB = (file.size / (1024 * 1024)).toFixed(1)
      throw new Error(
        `Upload connection dropped (${sizeMB} MB). Cloudflare & reverse proxies disconnect files larger than 100 MB. Please compress this bill (.csv.gz or .zip) before uploading.`
      )
    }
    throw new Error('Network error: Failed to reach the server. Please check your internet connection or try again.')
  }

  if (!res.ok) {
    let msg = `Upload failed (HTTP ${res.status})`
    try {
      const text = await res.text()
      try {
        const data = JSON.parse(text)
        if (data?.error) msg = data.error
        else if (text) msg = text
      } catch {
        if (text) msg = text
      }
    } catch {
      // Ignore stream reading errors and keep default HTTP status message
    }
    throw new Error(msg)
  }
  return res.json()
}

export async function listJobs(): Promise<Job[]> {
  const res = await fetch('/api/jobs')
  if (!res.ok) return []
  return res.json()
}

export async function getJob(id: string): Promise<Job> {
  const res = await fetch(`/api/jobs/${id}`)
  if (!res.ok) throw new Error('not found')
  return res.json()
}

export function downloadURL(id: string, runId?: string, format?: string): string {
  const params = new URLSearchParams()
  if (runId) params.set('run_id', runId)
  if (format) params.set('format', format)
  const qs = params.toString()
  return qs ? `/api/jobs/${id}/download?${qs}` : `/api/jobs/${id}/download`
}

export async function getRuns(id: string): Promise<RunResult[]> {
  const res = await fetch(`/api/jobs/${id}/runs`)
  if (!res.ok) return []
  return res.json()
}

export async function getSummary(id: string, runId?: string): Promise<string | null> {
  const url = runId
    ? `/api/jobs/${id}/summary?run_id=${encodeURIComponent(runId)}`
    : `/api/jobs/${id}/summary`
  const res = await fetch(url)
  if (res.status === 404) return null
  if (!res.ok) return null
  return res.text()
}

export interface Progress {
  events: number
  phase: string
  phase_number: number
  last_activity: string
  transcript_ok: boolean
}

export async function getProgress(id: string): Promise<Progress | null> {
  const res = await fetch(`/api/jobs/${id}/progress`)
  if (!res.ok) return null
  return res.json()
}

export async function retryJob(id: string): Promise<{ status: string }> {
  const res = await fetch(`/api/jobs/${id}/retry`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `retry failed (${res.status})`)
  }
  return res.json()
}

export async function refineJob(id: string, instruction: string): Promise<{ status: string }> {
  const res = await fetch(`/api/jobs/${id}/refine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `refine failed (${res.status})`)
  }
  return res.json()
}

export async function cancelJob(id: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `cancel failed (${res.status})`)
  }
  return res.json()
}

export async function deleteJob(id: string): Promise<void> {
  const res = await fetch(`/api/jobs/${id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    let msg = `delete failed (${res.status})`
    try {
      const text = await res.text()
      if (text) msg = text
    } catch {
      // ignore
    }
    throw new Error(msg)
  }
}

export async function regenerateReport(id: string): Promise<{ id: string; run_id: string; status: string; version: string }> {
  const res = await fetch(`/api/admin/jobs/${encodeURIComponent(id)}/regenerate-report`, {
    method: 'POST',
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `regenerate failed (${res.status})`)
  }
  return res.json()
}

// ─── Run comparison ───

export interface RunComparison {
  run_a: RunResult
  run_b: RunResult
  aws_delta: number
  gcp_od_delta: number
  gcp_1yr_cud_delta: number
  gcp_3yr_cud_delta: number
  assumptions_version_a?: string
  assumptions_version_b?: string
  report_settings_version_a?: string
  report_settings_version_b?: string
  catalog_vintage_a?: string
  catalog_vintage_b?: string
}

export async function compareRuns(id: string, from: string, to: string): Promise<RunComparison | null> {
  const res = await fetch(`/api/jobs/${encodeURIComponent(id)}/compare?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`)
  if (!res.ok) return null
  return res.json()
}

