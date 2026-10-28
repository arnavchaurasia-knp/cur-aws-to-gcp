import { getJSON } from './http'

export interface UserInfo {
  email: string
  name: string
  picture?: string
  role?: string
  is_admin?: boolean
  is_facets_employee?: boolean
}

export interface UserProfile {
  email: string
  name?: string
  phone?: string
  company?: string
  department?: string
  time_zone?: string
  updated_at: string
}

export async function getMe(): Promise<UserInfo | null> {
  const res = await fetch('/api/auth/me')
  if (res.status === 401) return null
  return res.json()
}

export async function getUserProfile(): Promise<UserProfile> {
  return getJSON<UserProfile>('/api/user/profile')
}

export async function saveUserProfile(profile: UserProfile): Promise<UserProfile> {
  const res = await fetch('/api/user/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `save profile failed (${res.status})`)
  }
  return res.json()
}

export interface NotificationSettings {
  email_notifications: boolean
  slack_notifications: boolean
  slack_webhook_url?: string
  slack_channel?: string
}

export async function getNotificationSettings(): Promise<NotificationSettings> {
  return getJSON<NotificationSettings>('/api/user/notifications')
}

export async function updateNotificationSettings(settings: Partial<NotificationSettings>): Promise<NotificationSettings> {
  const res = await fetch('/api/user/notifications', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `update notifications failed (${res.status})`)
  }
  return res.json()
}

export interface UserTokenResponse {
  token: string
  email: string
}

export async function getUserToken(): Promise<UserTokenResponse> {
  return getJSON<UserTokenResponse>('/api/user/token')
}

export async function regenerateUserToken(): Promise<UserTokenResponse> {
  const res = await fetch('/api/user/token/regenerate', {
    method: 'POST',
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `regenerate token failed (${res.status})`)
  }
  return res.json()
}

export function loginURL() { return '/api/auth/login' }
export function logoutURL() { return '/api/auth/logout' }
