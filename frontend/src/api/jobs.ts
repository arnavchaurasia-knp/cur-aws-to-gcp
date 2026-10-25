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

export async function createJob(
  file: File,
  prospect: string,
  onProgress?: (percent: number) => void
): Promise<{ id: string }> {
  const CHUNK_SIZE = 15 * 1024 * 1024 // 15 MB chunks to easily fit inside all proxy/Cloudflare limits

  // If file is smaller than or equal to 25 MB, upload directly in one piece
  if (file.size <= 25 * 1024 * 1024) {
    const form = new FormData()
    form.append('file', file)
    form.append('prospect_name', prospect)

    let res: Response
    try {
      res = await fetch('/api/jobs', { method: 'POST', body: form })
    } catch {
      throw new Error('Network error: Failed to reach the server. Please check your internet connection.')
    }

    if (!res.ok) {
      const errText = await res.text().catch(() => '')
      try {
        const parsed = JSON.parse(errText)
        throw new Error(parsed?.error || `Upload failed (HTTP ${res.status})`)
      } catch (e: any) {
        throw new Error(errText || e?.message || `Upload failed (HTTP ${res.status})`)
      }
    }
    onProgress?.(100)
    return res.json()
  }

  // Large file (> 25 MB): Slice into 15 MB chunks, upload sequentially, and combine on server
  const uploadId = crypto.randomUUID()
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE)

  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE
    const end = Math.min(start + CHUNK_SIZE, file.size)
    const chunkBlob = file.slice(start, end)

    const chunkForm = new FormData()
    chunkForm.append('upload_id', uploadId)
    chunkForm.append('chunk_index', String(i))
    chunkForm.append('total_chunks', String(totalChunks))
    chunkForm.append('chunk', chunkBlob, file.name)

    let chunkRes: Response
    try {
      chunkRes = await fetch('/api/jobs/chunk', { method: 'POST', body: chunkForm })
    } catch {
      throw new Error(`Connection lost while uploading part ${i + 1} of ${totalChunks}. Please try again.`)
    }

    if (!chunkRes.ok) {
      const msg = await chunkRes.text().catch(() => '')
      throw new Error(`Upload of part ${i + 1} failed: ${msg || chunkRes.statusText}`)
    }

    const percent = Math.round(((i + 1) / totalChunks) * 95)
    onProgress?.(percent)
  }

  // All chunks uploaded: tell backend to reassemble and start the pipeline
  let completeRes: Response
  try {
    completeRes = await fetch('/api/jobs/complete-chunked', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        upload_id: uploadId,
        prospect_name: prospect,
        file_name: file.name,
        total_chunks: totalChunks,
      }),
    })
  } catch {
    throw new Error('Connection lost while assembling bill on server. Please try again.')
  }

  if (!completeRes.ok) {
    const msg = await completeRes.text().catch(() => '')
    try {
      const parsed = JSON.parse(msg)
      throw new Error(parsed?.error || `Assembly failed (HTTP ${completeRes.status})`)
    } catch (e: any) {
      throw new Error(msg || e?.message || `Assembly failed (HTTP ${completeRes.status})`)
    }
  }

  onProgress?.(100)
  return completeRes.json()
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

