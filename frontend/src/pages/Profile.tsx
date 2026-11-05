import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { getUserProfile, saveUserProfile, getUserTokens, type UserProfile, type UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { BackButton } from '../components/BackButton'

const TIMEZONES = [
  { value: 'UTC', label: 'UTC — Coordinated Universal Time (+00:00)' },
  { value: 'America/New_York', label: 'New York / Eastern Time (-05:00 / -04:00)' },
  { value: 'America/Chicago', label: 'Chicago / Central Time (-06:00 / -05:00)' },
  { value: 'America/Denver', label: 'Denver / Mountain Time (-07:00 / -06:00)' },
  { value: 'America/Los_Angeles', label: 'Los Angeles / Pacific Time (-08:00 / -07:00)' },
  { value: 'America/Sao_Paulo', label: 'São Paulo / Brazil (-03:00)' },
  { value: 'Europe/London', label: 'London / GMT / BST (+00:00 / +01:00)' },
  { value: 'Europe/Berlin', label: 'Berlin / Frankfurt / Paris (+01:00 / +02:00)' },
  { value: 'Europe/Amsterdam', label: 'Amsterdam / Brussels (+01:00 / +02:00)' },
  { value: 'Asia/Dubai', label: 'Dubai / Gulf Standard Time (+04:00)' },
  { value: 'Asia/Kolkata', label: 'India Standard Time — Kolkata / Mumbai (+05:30)' },
  { value: 'Asia/Singapore', label: 'Singapore / SGT (+08:00)' },
  { value: 'Asia/Tokyo', label: 'Tokyo / JST (+09:00)' },
  { value: 'Australia/Sydney', label: 'Sydney / AEST (+10:00 / +11:00)' },
]

export function Profile({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('User Profile — Facets')
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [originalProfile, setOriginalProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTokenCount, setActiveTokenCount] = useState<number | null>(null)

  const initial = (user.name || user.email || '?').trim().charAt(0).toUpperCase()
  const isAdmin = Boolean(user.is_admin)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const [p, tokenRes] = await Promise.all([
        getUserProfile(),
        getUserTokens().catch(() => ({ tokens: [] })),
      ])
      const resolvedProfile: UserProfile = {
        email: p?.email || user.email,
        name: p?.name || user.name || '',
        phone: p?.phone || '',
        company: p?.company || '',
        department: p?.department || '',
        time_zone: p?.time_zone || 'UTC',
        updated_at: p?.updated_at || '',
      }
      setProfile(resolvedProfile)
      setOriginalProfile(resolvedProfile)
      const validTokens = (tokenRes.tokens || []).filter(t => !t.revoked_at)
      setActiveTokenCount(validTokens.length)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profile')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const isDirty = originalProfile && profile
    ? JSON.stringify(originalProfile) !== JSON.stringify(profile)
    : false

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!profile) return
    setSaving(true)
    setError(null)
    try {
      const updated = await saveUserProfile(profile)
      setProfile(updated)
      setOriginalProfile(updated)
      toast.success('Profile changes saved successfully')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to save profile'
      setError(msg)
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  if (loading || !profile) {
    return (
      <Layout user={user}>
        <div className="max-w-4xl mx-auto px-4 sm:px-6 py-12 text-center">
          <div className="flex items-center justify-center h-40">
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full bg-[#00C2BB] animate-pulse" />
              <div className="w-2.5 h-2.5 rounded-full bg-[#00C2BB] animate-pulse [animation-delay:150ms]" />
              <div className="w-2.5 h-2.5 rounded-full bg-[#00C2BB] animate-pulse [animation-delay:300ms]" />
            </div>
          </div>
          <p className="text-sm text-[var(--fcp-text-secondary)] font-medium">Loading profile details…</p>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <BackButton fallback="/" />

        {/* Top Header Card */}
        <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-6">
            <div className="flex items-center gap-5">
              <div className="relative shrink-0">
                {user.picture ? (
                  <img
                    src={user.picture}
                    alt=""
                    referrerPolicy="no-referrer"
                    className="h-20 w-20 rounded-2xl object-cover border-2 border-white/20 shadow-md"
                  />
                ) : (
                  <div className="h-20 w-20 rounded-2xl bg-gradient-to-tr from-[#645DF6] to-[#00C2BB] text-white text-2xl font-bold flex items-center justify-center shadow-md">
                    {initial}
                  </div>
                )}
                <span
                  title="Account Active"
                  className="absolute -bottom-1 -right-1 h-5 w-5 rounded-full bg-emerald-500 border-2 border-[var(--fcp-bg-surface)] flex items-center justify-center"
                >
                  <span className="h-2 w-2 rounded-full bg-white" />
                </span>
              </div>

              <div className="space-y-1">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <h1 className="text-xl font-bold text-[var(--fcp-text-primary)]">
                    {profile.name || user.name || 'User Profile'}
                  </h1>
                  {isAdmin ? (
                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#645DF6]/15 text-[#645DF6] dark:text-[#a5a0ff] border border-[#645DF6]/30">
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                      </svg>
                      Administrator
                    </span>
                  ) : (
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-gray-500/10 text-gray-600 dark:text-gray-300 border border-gray-500/20">
                      Standard Member
                    </span>
                  )}
                  {user.is_facets_employee && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#00C2BB]/15 text-[#00C2BB] border border-[#00C2BB]/30">
                      Facets Team
                    </span>
                  )}
                </div>
                <p className="text-xs text-[var(--fcp-text-secondary)] font-mono">{user.email}</p>
                <div className="flex items-center gap-3 pt-1 text-xs text-[var(--fcp-text-tertiary)]">
                  <span>SSO Authenticated</span>
                  <span>•</span>
                  <span>
                    {profile.updated_at
                      ? `Last updated: ${new Date(profile.updated_at).toLocaleDateString()}`
                      : 'Active Account'}
                  </span>
                </div>
              </div>
            </div>

            {/* Quick shortcuts */}
            <div className="flex sm:flex-col gap-2 shrink-0">
              <Link
                to="/profile/tokens"
                className="inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl text-xs font-medium border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] text-[var(--fcp-text-primary)] hover:border-[#00C2BB]/50 transition-colors"
              >
                <svg className="w-3.5 h-3.5 text-[#00C2BB]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                </svg>
                <span>API Tokens</span>
                {activeTokenCount !== null && (
                  <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-[#00C2BB]/20 text-[#00C2BB]">
                    {activeTokenCount}
                  </span>
                )}
              </Link>

              <Link
                to="/settings/notifications"
                className="inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl text-xs font-medium border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] text-[var(--fcp-text-primary)] hover:border-[#00C2BB]/50 transition-colors"
              >
                <svg className="w-3.5 h-3.5 text-[#645DF6]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                </svg>
                <span>Notifications</span>
              </Link>
            </div>
          </div>
        </div>

        {error && (
          <div className="p-4 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 text-rose-800 dark:text-rose-200 text-xs">
            <strong className="block font-semibold mb-0.5">Unable to save profile</strong>
            <p>{error}</p>
          </div>
        )}

        {/* Detailed Profile Form */}
        <form onSubmit={handleSave} className="space-y-6">
          {/* Section 1: Personal Details */}
          <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 space-y-4">
            <div className="flex items-center gap-2.5 pb-2 border-b border-[var(--fcp-border-subtle)]">
              <div className="w-8 h-8 rounded-lg bg-[#00C2BB]/10 text-[#00C2BB] flex items-center justify-center">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Personal Information</h2>
                <p className="text-xs text-[var(--fcp-text-secondary)]">Your identity and direct contact information.</p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
              <div>
                <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider mb-1.5">
                  Full Name
                </label>
                <input
                  type="text"
                  required
                  value={profile.name || ''}
                  onChange={e => setProfile({ ...profile, name: e.target.value })}
                  placeholder="e.g. Arnav Chaurasia"
                  className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20 transition-all"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider">
                    Work Email
                  </label>
                  <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    Verified
                  </span>
                </div>
                <input
                  type="email"
                  value={profile.email}
                  readOnly
                  disabled
                  className="w-full rounded-xl bg-[var(--fcp-input-bg)]/50 border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-secondary)] cursor-not-allowed opacity-80"
                />
                <p className="text-[11px] text-[var(--fcp-text-tertiary)] mt-1">Managed via your identity provider sign-in.</p>
              </div>

              <div className="sm:col-span-2">
                <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider mb-1.5">
                  Phone Number
                </label>
                <input
                  type="tel"
                  value={profile.phone || ''}
                  onChange={e => setProfile({ ...profile, phone: e.target.value })}
                  placeholder="+1 (555) 012-3456"
                  className="w-full max-w-md rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20 transition-all"
                />
                <p className="text-[11px] text-[var(--fcp-text-tertiary)] mt-1">Used for urgent automated system alerts and ticket verification.</p>
              </div>
            </div>
          </div>

          {/* Section 2: Organization & Professional Details */}
          <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 space-y-4">
            <div className="flex items-center gap-2.5 pb-2 border-b border-[var(--fcp-border-subtle)]">
              <div className="w-8 h-8 rounded-lg bg-[#645DF6]/10 text-[#645DF6] flex items-center justify-center">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Organization &amp; Team</h2>
                <p className="text-xs text-[var(--fcp-text-secondary)]">Your organizational affiliation and role in cloud cost operations.</p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
              <div>
                <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider mb-1.5">
                  Company / Organization
                </label>
                <input
                  type="text"
                  value={profile.company || ''}
                  onChange={e => setProfile({ ...profile, company: e.target.value })}
                  placeholder="e.g. Acme Cloud Corp"
                  className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20 transition-all"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider mb-1.5">
                  Department / Unit
                </label>
                <input
                  type="text"
                  value={profile.department || ''}
                  onChange={e => setProfile({ ...profile, department: e.target.value })}
                  placeholder="e.g. Cloud Infrastructure / FinOps"
                  className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20 transition-all"
                />
              </div>
            </div>
          </div>

          {/* Section 3: Localization & Preferences */}
          <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 space-y-4">
            <div className="flex items-center gap-2.5 pb-2 border-b border-[var(--fcp-border-subtle)]">
              <div className="w-8 h-8 rounded-lg bg-emerald-500/10 text-emerald-500 flex items-center justify-center">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Localization &amp; Regional Formatting</h2>
                <p className="text-xs text-[var(--fcp-text-secondary)]">Timestamp rendering and schedule alignment.</p>
              </div>
            </div>

            <div className="pt-1">
              <label className="block text-xs font-semibold text-[var(--fcp-text-secondary)] uppercase tracking-wider mb-1.5">
                Primary Time Zone
              </label>
              <select
                value={profile.time_zone || 'UTC'}
                onChange={e => setProfile({ ...profile, time_zone: e.target.value })}
                className="w-full max-w-xl rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-sm text-[var(--fcp-text-primary)] focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20 transition-all cursor-pointer"
              >
                {TIMEZONES.map(tz => (
                  <option key={tz.value} value={tz.value} className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]">
                    {tz.label}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-[var(--fcp-text-tertiary)] mt-1.5">
                Projection run times and system audit logs will be displayed aligned to this timezone.
              </p>
            </div>
          </div>

          {/* Form Actions */}
          <div className="flex items-center justify-between pt-2">
            <div className="text-xs text-[var(--fcp-text-secondary)]">
              {isDirty ? (
                <span className="text-amber-500 dark:text-amber-400 font-medium flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  Unsaved changes
                </span>
              ) : (
                <span className="text-emerald-500 dark:text-emerald-400 font-medium flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  All changes saved
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              {isDirty && (
                <button
                  type="button"
                  onClick={() => setProfile(originalProfile)}
                  disabled={saving}
                  className="px-4 py-2.5 rounded-xl text-xs font-medium border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors cursor-pointer"
                >
                  Reset
                </button>
              )}

              <button
                type="submit"
                disabled={saving || !isDirty}
                className="btn-shimmer px-6 py-2.5 rounded-xl text-sm font-semibold text-white cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 shadow-sm"
              >
                {saving && (
                  <div className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                )}
                <span>{saving ? 'Saving changes…' : 'Save Profile'}</span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </Layout>
  )
}
