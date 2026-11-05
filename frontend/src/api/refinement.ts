import { getJSON } from './http'

export interface ScopeEstimate {
  affected_rows: number
  monthly_aws_cost: number
  services?: string[]
  sample_rows?: Array<{
    aws_li_key: string
    product: string
    usage_type: string
    aws_cost: number
  }>
}

export interface Directive {
  id: string
  intent?: string
  action: string
  selector?: Record<string, any>
  parameters?: Record<string, any>
  status: 'proposed' | 'confirmed' | 'superseded'
  source_utterance?: string
  created_at?: string
  scope_estimate?: ScopeEstimate
}

export interface Clarification {
  id: string
  question: string
  options?: string[]
  directive_id?: string
  status: 'open' | 'answered'
  answer?: string
}

export interface RefinementMessage {
  id: string
  role: 'user' | 'agy'
  content: string
  created_at: string
}

export interface RefinementThreadResponse {
  job_id: string
  messages: RefinementMessage[]
  open_clarifications: Clarification[]
  proposed_directives: Directive[]
  directives?: Directive[]
  created_at?: string
  updated_at?: string
}

export async function getRefinementThread(jobId: string): Promise<RefinementThreadResponse> {
  return getJSON<RefinementThreadResponse>(`/api/jobs/${jobId}/refinement/thread`)
}

export async function proposeRefinement(
  jobId: string,
  utterance: string,
  selectedRowKeys?: string[]
): Promise<RefinementThreadResponse> {
  const res = await fetch(`/api/jobs/${jobId}/refinement/propose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ utterance, selected_row_keys: selectedRowKeys || [] }),
  })
  if (!res.ok) throw new Error((await res.text()) || 'Failed to propose refinement')
  return res.json()
}

export async function confirmRefinement(
  jobId: string,
  directiveId: string
): Promise<RefinementThreadResponse> {
  const res = await fetch(`/api/jobs/${jobId}/refinement/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directive_id: directiveId }),
  })
  if (!res.ok) throw new Error((await res.text()) || 'Failed to confirm refinement')
  return res.json()
}

export async function answerClarification(
  jobId: string,
  clarificationId: string,
  answer: string
): Promise<RefinementThreadResponse> {
  const res = await fetch(`/api/jobs/${jobId}/refinement/clarify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clarification_id: clarificationId, answer }),
  })
  if (!res.ok) throw new Error((await res.text()) || 'Failed to answer clarification')
  return res.json()
}

export async function revokeRefinement(
  jobId: string,
  directiveId: string
): Promise<Directive> {
  const res = await fetch(`/api/jobs/${jobId}/refinement/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directive_id: directiveId }),
  })
  if (!res.ok) throw new Error((await res.text()) || 'Failed to revoke refinement')
  return res.json()
}

export async function applyRefinement(
  jobId: string
): Promise<{ status: string; job_id: string; recalculating: boolean }> {
  const res = await fetch(`/api/jobs/${jobId}/refinement/apply`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error((await res.text()) || 'Failed to apply refinements')
  return res.json()
}
