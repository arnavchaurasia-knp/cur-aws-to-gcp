// frontend/src/api/admin.ts
import type { Job } from './jobs'

export interface PagedJobs {
  jobs: Job[]
  total: number
  limit: number
  offset: number
}

export interface ListJobsParams {
  limit?: number
  offset?: number
  status?: string
  from?: string // yyyy-mm-dd
  to?: string   // yyyy-mm-dd
}

export async function listAllJobsPaged(params: ListJobsParams = {}): Promise<PagedJobs> {
  const qs = new URLSearchParams()
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.offset != null) qs.set('offset', String(params.offset))
  if (params.status) qs.set('status', params.status)
  if (params.from) qs.set('from', params.from)
  if (params.to) qs.set('to', params.to)
  const res = await fetch(`/api/admin/jobs?${qs.toString()}`)
  if (!res.ok) return { jobs: [], total: 0, limit: params.limit ?? 50, offset: params.offset ?? 0 }
  return res.json()
}

export async function cancelJob(id: string): Promise<void> {
  const res = await fetch(`/api/admin/jobs/${id}/cancel`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `cancel failed (${res.status})`)
  }
}

export interface CancelAllResponse {
  cancelled: string[]
  already_terminal: string[]
  reaped_pids: number[]
  errors: string[]
  stop_all: boolean
}

export async function cancelAllJobs(): Promise<CancelAllResponse> {
  const res = await fetch('/api/admin/jobs/cancel-all', { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `cancel-all failed (${res.status})`)
  }
  return res.json()
}

export async function resumeAllJobs(): Promise<{ status: string; stop_all: boolean }> {
  const res = await fetch('/api/admin/jobs/resume-all', { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `resume-all failed (${res.status})`)
  }
  return res.json()
}

export interface SystemState {
  halted: boolean
  stop_all: boolean
}

export async function getSystemState(): Promise<SystemState> {
  const res = await fetch('/api/admin/system/state')
  if (!res.ok) {
    return { halted: false, stop_all: false }
  }
  return res.json()
}

export async function deleteJob(id: string): Promise<void> {
  const res = await fetch(`/api/admin/jobs/${id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const text = await res.text()
    throw new Error(text || `delete failed (${res.status})`)
  }
}

export interface AdminUser {
  email: string
  role: string
  created_at: string
  updated_at: string
}

export async function listUsers(): Promise<AdminUser[]> {
  const res = await fetch('/api/admin/users')
  if (!res.ok) return []
  return res.json()
}

export async function setUserRole(email: string, role: string): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(email)}/role`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `role update failed (${res.status})`)
  }
}

export interface CatalogStatus {
  age_days: number
  stale: boolean
}

export async function getCatalogStatus(): Promise<CatalogStatus | null> {
  const res = await fetch('/api/admin/catalog/status')
  if (!res.ok) return null
  return res.json()
}

export async function triggerCatalogRefresh(): Promise<void> {
  const res = await fetch('/api/admin/catalog/refresh', { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `refresh failed (${res.status})`)
  }
}

export interface AuditEntry {
  id: number
  actor_email: string
  action: string
  target_id: string
  details: string
  created_at: string
}

export async function listAuditLog(params: { limit?: number; offset?: number } = {}): Promise<AuditEntry[]> {
  const qs = new URLSearchParams()
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.offset != null) qs.set('offset', String(params.offset))
  const res = await fetch(`/api/admin/audit?${qs.toString()}`)
  if (!res.ok) return []
  return res.json()
}

export interface DayCount {
  day: string
  count: number
}

export interface UsageStats {
  total_jobs: number
  done_jobs: number
  failed_jobs: number
  failure_rate: number
  avg_processing_seconds: number
  jobs_by_day: DayCount[] | null
}

export async function getAnalytics(): Promise<UsageStats | null> {
  const res = await fetch('/api/admin/analytics')
  if (!res.ok) return null
  return res.json()
}

export async function shareJob(id: string): Promise<{ token: string; url: string }> {
  const res = await fetch(`/api/jobs/${id}/share`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `share failed (${res.status})`)
  }
  return res.json()
}

export interface DeletedJob {
  id: string
  owner: string
  prospect: string
  status: string
  input_ext?: string
  aws_spend: number | null
  error: string
  session_id: string
  created_at: string
  deleted_at: string
  deleted_by: string
}

export async function listDeletedJobs(): Promise<DeletedJob[]> {
  const res = await fetch('/api/admin/deleted-jobs')
  if (!res.ok) return []
  return res.json()
}

export interface Assumption {
  key: string
  value: number
  default_value: number
  min_value: number
  max_value: number
  unit: string
  title: string
  rationale: string
  scope: string
  updated_by: string
  updated_at: string
}

export interface AssumptionHistory {
  id: number
  key: string
  old_value: number
  new_value: number
  reason: string
  changed_by: string
  changed_at: string
}

export interface AssumptionsResponse {
  assumptions: Assumption[]
  version: string
}

export async function getAssumptions(): Promise<AssumptionsResponse | null> {
  const res = await fetch('/api/admin/assumptions')
  if (!res.ok) return null
  return res.json()
}

export interface PutAssumptionBody {
  value: number
  reason: string
}

export async function putAssumption(key: string, body: PutAssumptionBody): Promise<{ key: string; value: number; version: string } | null> {
  const res = await fetch(`/api/admin/assumptions/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) return null
  return res.json()
}

export async function getAssumptionHistory(): Promise<AssumptionHistory[]> {
  const res = await fetch('/api/admin/assumptions/history')
  if (!res.ok) return []
  return res.json()
}

// ─── Report settings ───

export interface ReportSetting {
  key: string
  value: string
  value_type: string
  default_value: string
  min_value: number | null
  max_value: number | null
  title: string
  description: string
  group_name: string
  sort_order: number
  updated_by: string
  updated_at: string
}

export interface ReportSettingChange {
  id: number
  key: string
  old_value: string
  new_value: string
  changed_by: string
  changed_at: string
}

export interface ReportSettingsResponse {
  settings: ReportSetting[]
  version: string
}

export async function getReportSettings(): Promise<ReportSettingsResponse | null> {
  const res = await fetch('/api/admin/report-settings')
  if (!res.ok) return null
  return res.json()
}

export interface PutReportSettingBody {
  value: string
  reason: string
}

export async function putReportSetting(key: string, body: PutReportSettingBody): Promise<{ key: string; value: string; version: string } | null> {
  const res = await fetch(`/api/admin/report-settings/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) return null
  return res.json()
}

export async function getReportSettingHistory(): Promise<ReportSettingChange[]> {
  const res = await fetch('/api/admin/report-settings/history')
  if (!res.ok) return []
  return res.json()
}

export async function resetReportSettings(): Promise<{ version: string } | null> {
  const res = await fetch('/api/admin/report-settings/reset', { method: 'POST' })
  if (!res.ok) return null
  return res.json()
}

export interface RegenerateReportResponse {
  id: string
  run_id: string
  status: string
  version: string
}

export async function regenerateReport(id: string): Promise<RegenerateReportResponse | null> {
  const res = await fetch(`/api/admin/jobs/${encodeURIComponent(id)}/regenerate-report`, {
    method: 'POST',
  })
  if (!res.ok) return null
  return res.json()
}

// ─── Job health diagnostics ───

export interface JobDiagnosticByCode {
  level: string
  count: number
  rows: number
  usd: number
  sources: string[]
}

export interface JobDiagnosticsSummary {
  workload_usd: number
  error_usd: number
  error_pct_of_workload: number
  error_count: number
  warn_count: number
  checks_not_evaluated: number
  by_code: Record<string, JobDiagnosticByCode>
}

export interface JobDiagnosticsResponse {
  available: boolean
  summary?: JobDiagnosticsSummary
  version?: string
}

export interface JobDiagnosticRecord {
  ts: string
  level: string
  code: string
  source: string
  phase: string | null
  message: string
  rows: number
  usd: number
  detail: Record<string, any>
}

export interface JobDiagnosticRecordsResponse {
  available: boolean
  records: JobDiagnosticRecord[]
}

export async function getJobDiagnostics(id: string): Promise<JobDiagnosticsResponse | null> {
  const res = await fetch(`/api/admin/jobs/${encodeURIComponent(id)}/diagnostics`)
  if (!res.ok) return null
  return res.json()
}

export async function getJobDiagnosticRecords(id: string): Promise<JobDiagnosticRecordsResponse | null> {
  const res = await fetch(`/api/admin/jobs/${encodeURIComponent(id)}/diagnostics/records`)
  if (!res.ok) return null
  return res.json()
}

