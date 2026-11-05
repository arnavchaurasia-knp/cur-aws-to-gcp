import { getJSON, sendJSON } from './http'

export const EXPECTED_HEALTH_SCHEMA = 3 // must equal HealthSchemaVersion in system_health.go

export interface IssueAction {
  kind: 'test_engine' | 'refresh_catalog' | 'edit_env' | 'open_store' | 'open_tab' | 'link'
  target?: string
  label: string
}

export interface HealthIssue {
  id: string
  severity: 'critical' | 'warning' | 'info'
  area: 'engine' | 'catalog' | 'jobs' | 'data' | 'settings' | 'runtime'
  title: string
  detail: string
  fix: string
  action?: IssueAction
}

export interface EngineTestRecord {
  checked_at: string
  state: 'ok' | 'warn' | 'fail' | 'unknown'
  latency_ms: number
  detail: string
  actor?: string
  model: string
}

export interface EngineProbe {
  state: 'ok' | 'warn' | 'fail' | 'unknown'
  detail: string
  binary_found?: boolean
  binary_path?: string
  version?: string
  auth_present?: boolean
  latency_ms?: number
  checked_at?: string
  model?: string
}

export interface EngineHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  model: string
  model_source: 'report_settings' | 'env' | 'default' | string
  cli_found: boolean
  cli_path: string
  cli_version: string
  key_present: boolean
  key_source: 'app' | 'server' | 'none' | string
  verified_by?: string
  last_test?: EngineTestRecord
  history: EngineTestRecord[]
  last_successful_job_at?: string
  live_test_disabled: boolean
}

export interface CatalogRefreshRun {
  state: 'idle' | 'running' | 'success' | 'failed' | 'skipped' | string
  started_at?: string
  finished_at?: string
  duration_sec?: number
  reason?: string
  log_tail?: string
  actor?: string
}

export interface CatalogHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  sku_count: number
  service_count: number
  fetched_at: string
  age_days: number
  stale_after_days: number
  stale: boolean
  artifact_ok: boolean
  source_total: number
  source_problems: string[]
  image_baked: boolean
  api_key_present: boolean
  last_run: CatalogRefreshRun
  history: CatalogRefreshRun[]
}

export interface JobBrief {
  id: string
  prospect: string
  owner: string
  status: string
  error?: string
  updated_at: string
}

export interface JobsHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  running: number
  queued: number
  completed_total: number
  failed_total: number
  recent_window: number
  recent_failed: number
  recent_failure_pct: number
  avg_duration_sec: number
  concurrency_limit: number
  halted: boolean
  last_success_at?: string
  last_failure_at?: string
  recent_failures: JobBrief[]
}

export interface StoreHealth {
  key: string
  title?: string
  description?: string
  file: string
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  detail: string
  size_bytes: number
  age_days: number
  count: number
  editable: boolean
  is_db_backed?: boolean
  manage_url?: string
}

export interface DataHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  stores: StoreHealth[]
}

export interface IgnoredVar {
  key: string
  use_instead: string
  description: string
}

export interface SettingsHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  encryption_source: 'env' | 'file' | 'generated' | 'none' | string
  override_count: number
  missing_required: string[]
  ignored_present: IgnoredVar[]
  notifications_on: boolean
}

export interface DepHealth {
  key: string
  title: string
  description: string
  status: 'ok' | 'warn' | 'fail' | string
  detail: string
  present: boolean
  fix?: string
}

export interface RuntimeHealth {
  status: 'ok' | 'warn' | 'fail' | 'unknown'
  summary: string
  checks: DepHealth[]
}

export interface ServerInfo {
  schema_version: number
  build_sha: string
  build_time: string
  started_at: string
  uptime_sec: number
  go_version: string
  os: string
  pid: number
  data_dir: string
  skill_dir: string
  image_baked: boolean
}

export interface HealthResponse {
  schema_version: number
  checked_at: string
  status: 'ok' | 'warn' | 'fail'
  summary: string
  issues: HealthIssue[]
  engine: EngineHealth
  catalog: CatalogHealth
  jobs: JobsHealth
  data: DataHealth
  settings: SettingsHealth
  runtime: RuntimeHealth
  server: ServerInfo
}

export interface EnvValueView {
  set: boolean
  preview?: string
  from_key?: string
}

export interface EnvOverrideView {
  set: boolean
  preview?: string
  updated_by?: string
  updated_at?: string
  load_error?: string
}

export interface EnvEffectiveView {
  set: boolean
  preview?: string
  source: 'app' | 'server' | 'integrations' | 'none' | string
}

export interface EnvVarView {
  key: string
  title: string
  category: string
  description: string
  note?: string
  secret: boolean
  mode: 'managed' | 'integration' | 'deployment'
  required: boolean
  options?: string[]
  example?: string
  manage_url?: string
  testable: boolean
  server: EnvValueView
  override: EnvOverrideView
  effective: EnvEffectiveView
}

export interface EnvListResponse {
  encryption: {
    available: boolean
    source: string // env | file | generated | none
    key_path?: string
    problem?: string
  }
  categories: string[]
  vars: EnvVarView[]
  ignored_present: IgnoredVar[]
}

export interface EnvTestResult {
  ok: boolean
  status: 'ok' | 'warn' | 'fail'
  detail: string
  latency_ms?: number
}

export interface BackupInfo {
  name: string
  created_at: string
  size_bytes: number
  kind: 'before-edit' | 'pre-revert' | 'pre-restore' | string
}

export interface DataStoreDetail {
  key: string
  title?: string
  description?: string
  file: string
  editable: boolean
  is_db_backed: boolean
  manage_url?: string
  size_bytes: number
  age_days: number
  status: string
  detail: string
  count: number
  content?: string
  last_backup_at?: string
  backup_count: number
}

// API functions
export const getHealth = () => getJSON<HealthResponse>('/api/admin/health')
export const testEngine = () => sendJSON<EngineProbe>('POST', '/api/admin/engine/test')
export const getEnv = () => getJSON<EnvListResponse>('/api/admin/env')
export const setEnv = (key: string, value: string) =>
  sendJSON<EnvVarView>('PUT', `/api/admin/env/${encodeURIComponent(key)}`, { value })
export const clearEnv = (key: string) =>
  sendJSON<EnvVarView>('DELETE', `/api/admin/env/${encodeURIComponent(key)}`)
export const testEnv = (key: string) =>
  sendJSON<EnvTestResult>('POST', `/api/admin/env/${encodeURIComponent(key)}/test`)

export const listBackups = (key: string) =>
  getJSON<{ backups: BackupInfo[] }>(`/api/admin/datastores/${encodeURIComponent(key)}/backups`)
export const restoreBackup = (key: string, name: string) =>
  sendJSON<{ message: string; store: StoreHealth }>(
    'POST',
    `/api/admin/datastores/${encodeURIComponent(key)}/restore`,
    { name }
  )

export const getDataStore = (key: string) =>
  getJSON<DataStoreDetail>(`/api/admin/datastores/${encodeURIComponent(key)}`)
export const updateDataStore = (key: string, content: string) =>
  sendJSON<{ message: string; store: StoreHealth }>(
    'PUT',
    `/api/admin/datastores/${encodeURIComponent(key)}`,
    { content }
  )
export const revertDataStore = (key: string) =>
  sendJSON<{ message: string; store: StoreHealth }>(
    'POST',
    `/api/admin/datastores/${encodeURIComponent(key)}/revert`
  )

export const triggerCatalogRefresh = () => sendJSON<void>('POST', '/api/admin/catalog/refresh')
export const getCatalogRefreshStatus = () =>
  getJSON<CatalogRefreshRun>('/api/admin/catalog/refresh-status')
