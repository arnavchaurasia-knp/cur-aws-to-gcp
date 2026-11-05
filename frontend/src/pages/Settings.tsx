import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { BackButton } from '../components/BackButton'
import {
  getUserProfile,
  saveUserProfile,
  getNotificationSettings,
  updateNotificationSettings,
  getUserTokens,
  createUserToken,
  revokeUserToken,
    type UserProfile,
  type NotificationSettings,
  type APIToken,
  } from '../api/auth'
import type { UserInfo } from '../api/auth'
import {
  getIntegrations,
  updateIntegrations,
  testNotify,
  type IntegrationsConfig,
} from '../api/admin'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

type TabKey = 'notifications' | 'tokens' | 'preferences'

const EXPIRY_OPTIONS: { label: string; value: number }[] = [
  { label: '30 days', value: 30 },
  { label: '90 days (default)', value: 90 },
  { label: '365 days', value: 365 },
  { label: 'Never', value: 0 },
]

const SCOPE_OPTIONS: { value: string; label: string; desc: string }[] = [
  {
    value: 'jobs:read',
    label: 'Jobs: read',
    desc: 'List your own jobs and read your own reports',
  },
  {
    value: 'jobs:write',
    label: 'Jobs: write',
    desc: 'Create and cancel your own jobs',
  },
  {
    value: 'reports:read',
    label: 'Reports: read',
    desc: 'Download your own report artifacts',
  },
]

export function Settings({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Settings')
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = (searchParams.get('tab') as TabKey) || 'notifications'
  const [tab, setTab] = useState<TabKey>(initialTab)

  // Sync tab with URL
  const handleTabChange = (t: TabKey) => {
    setTab(t)
    setSearchParams({ tab: t }, { replace: true })
  }

  return (
    <Layout user={user}>
      <div className="w-full max-w-5xl mx-auto px-4 sm:px-6 py-8 min-w-0 space-y-6">
        <BackButton fallback="/" />

        <div>
          <h1 className="text-2xl font-bold tracking-tight">Account Settings</h1>
          <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
            Manage your notification preferences, personal API tokens, and profile.
          </p>
        </div>

        {/* Tab Switcher */}
        <div className="flex items-center gap-2 border-b border-white/10 pb-0.5">
          <button
            type="button"
            onClick={() => handleTabChange('notifications')}
            className={`pb-3 px-4 text-sm font-semibold transition-all relative cursor-pointer ${
              tab === 'notifications'
                ? 'text-[#00C2BB]'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            <span className="flex items-center gap-2">
              <span>🔔</span>
              <span>Notifications</span>
            </span>
            {tab === 'notifications' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>

          <button
            type="button"
            onClick={() => handleTabChange('tokens')}
            className={`pb-3 px-4 text-sm font-semibold transition-all relative cursor-pointer ${
              tab === 'tokens'
                ? 'text-[#00C2BB]'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            <span className="flex items-center gap-2">
              <span>🔑</span>
              <span>API Tokens &amp; MCP</span>
            </span>
            {tab === 'tokens' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>

          <button
            type="button"
            onClick={() => handleTabChange('preferences')}
            className={`pb-3 px-4 text-sm font-semibold transition-all relative cursor-pointer ${
              tab === 'preferences'
                ? 'text-[#00C2BB]'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            <span className="flex items-center gap-2">
              <span>👤</span>
              <span>Profile &amp; Preferences</span>
            </span>
            {tab === 'preferences' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>
        </div>

        {/* Tab Content */}
        <div className="anim-fade-in">
          {tab === 'notifications' && <NotificationsTab user={user} />}
          {tab === 'tokens' && <TokensTab />}
          {tab === 'preferences' && <PreferencesTab user={user} />}
        </div>
      </div>
    </Layout>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   1. Notifications Tab (Email + Slack Per-User Settings)
   ───────────────────────────────────────────────────────────────────────────── */

function NotificationsTab({ user }: { user: UserInfo }) {
  const [settings, setSettings] = useState<NotificationSettings>({
    email_notifications: true,
    slack_notifications: false,
    slack_webhook_url: '',
    slack_channel: '',
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // Admin integration state
  const [intCfg, setIntCfg] = useState<IntegrationsConfig | null>(null)
  const [emailEnabled, setEmailEnabled] = useState(false)
  const [emailFrom, setEmailFrom] = useState('')
  const [emailKey, setEmailKey] = useState('')
  const [slackEnabled, setSlackEnabled] = useState(false)
  const [slackWebhook, setSlackWebhook] = useState('')
  const [intSaving, setIntSaving] = useState(false)
  const [emailTest, setEmailTest] = useState<{ status: 'idle' | 'sending' | 'ok' | 'error'; msg?: string }>({ status: 'idle' })
  const [slackTest, setSlackTest] = useState<{ status: 'idle' | 'sending' | 'ok' | 'error'; msg?: string }>({ status: 'idle' })

  useEffect(() => {
    getNotificationSettings()
      .then(s => setSettings(s))
      .catch(() => {})
      .finally(() => setLoading(false))

    if (user.is_admin) {
      getIntegrations()
        .then(d => {
          setIntCfg(d)
          setEmailEnabled(d.email.enabled)
          setEmailFrom(d.email.from)
          setSlackEnabled(d.slack.enabled)
        })
        .catch(() => {})
    }
  }, [user.is_admin])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await updateNotificationSettings(settings)
      toast.success('Notification preferences saved')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save preferences')
    } finally {
      setSaving(false)
    }
  }

  const saveIntegrations = async () => {
    setIntSaving(true)
    try {
      const d = await updateIntegrations({
        email: { enabled: emailEnabled, from: emailFrom, ...(emailKey ? { api_key: emailKey } : {}) },
        slack: { enabled: slackEnabled, ...(slackWebhook ? { webhook_url: slackWebhook } : {}) },
      })
      setIntCfg(d)
      setEmailKey('')
      setSlackWebhook('')
      triggerNotificationRefresh()
      toast.success('Organization delivery settings saved')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save delivery settings')
    } finally {
      setIntSaving(false)
    }
  }

  const runTest = async (channel: 'email' | 'slack') => {
    const set = channel === 'email' ? setEmailTest : setSlackTest
    set({ status: 'sending' })
    try {
      const r = await testNotify(channel)
      if (r.status === 'sent') {
        const msg = `Test ${channel} sent to ${user.email}`
        set({ status: 'ok', msg })
        triggerNotificationRefresh()
        toast.success(msg)
      } else {
        const msg = r.error || 'failed'
        set({ status: 'error', msg })
        toast.error(msg)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'failed'
      set({ status: 'error', msg })
      toast.error(msg)
    }
  }

  const secretLabel = (s: { configured: boolean; last4?: string }, envFallback: boolean) => {
    if (s.configured) return `Configured ✓ (…${s.last4 ?? ''})`
    if (envFallback) return 'Set via environment (enter a value here to override)'
    return 'Not set'
  }

  if (loading) {
    return <div className="p-8 text-center text-sm text-gray-500">Loading notifications preferences…</div>
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <form onSubmit={handleSave} className="bg-white/[0.02] border border-white/10 rounded-xl p-6 space-y-6">
        <div>
          <h2 className="text-base font-semibold text-white">Your Direct Notifications</h2>
          <p className="text-xs text-gray-400 mt-1">
            Control how the estimation engine updates you when your projections finish or fail.
          </p>
        </div>

        {/* Email Notifications Toggle */}
        <div className="flex items-start justify-between gap-4 p-4 rounded-lg bg-white/[0.02] border border-white/5">
          <div>
            <label className="text-sm font-medium text-white cursor-pointer block" htmlFor="email-notif">
              Email Notifications
            </label>
            <p className="text-xs text-gray-400 mt-0.5">
              Receive automated completion and report download links via email when your jobs finish.
            </p>
          </div>
          <input
            id="email-notif"
            type="checkbox"
            checked={settings.email_notifications}
            onChange={e => setSettings({ ...settings, email_notifications: e.target.checked })}
            className="w-4 h-4 rounded text-[#645DF6] focus:ring-[#645DF6] mt-1 cursor-pointer"
          />
        </div>

        {/* Per-User Slack Notifications */}
        <div className="space-y-4 p-4 rounded-lg bg-white/[0.02] border border-white/5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <label className="text-sm font-medium text-white cursor-pointer block" htmlFor="slack-notif">
                Personal Slack Notifications
              </label>
              <p className="text-xs text-gray-400 mt-0.5">
                Send notifications to your private Slack webhook or channel when your job completes.
              </p>
            </div>
            <input
              id="slack-notif"
              type="checkbox"
              checked={settings.slack_notifications}
              onChange={e => setSettings({ ...settings, slack_notifications: e.target.checked })}
              className="w-4 h-4 rounded text-[#645DF6] focus:ring-[#645DF6] mt-1 cursor-pointer"
            />
          </div>

          {settings.slack_notifications && (
            <div className="space-y-4 pt-2 border-t border-white/5 anim-fade-in">
              <div>
                <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">
                  Incoming Webhook URL
                </label>
                <input
                  type="url"
                  value={settings.slack_webhook_url || ''}
                  onChange={e => setSettings({ ...settings, slack_webhook_url: e.target.value })}
                  placeholder="https://hooks.slack.com/services/T.../B.../..."
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-[#645DF6] outline-none"
                />
              </div>
              <div>
                <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">
                  Target Channel (optional)
                </label>
                <input
                  type="text"
                  value={settings.slack_channel || ''}
                  onChange={e => setSettings({ ...settings, slack_channel: e.target.value })}
                  placeholder="#cost-alerts or @username"
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-[#645DF6] outline-none"
                />
              </div>
            </div>
          )}
        </div>

        <button
          type="submit"
          disabled={saving}
          className="btn-shimmer px-6 py-2.5 rounded-lg text-sm font-semibold text-white cursor-pointer disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Save Notification Preferences'}
        </button>
      </form>

      {/* Admin Organization-Level Slack & Email Delivery Integration */}
      {user.is_admin && intCfg && (
        <div className="bg-white/[0.02] border border-white/10 rounded-xl p-6 space-y-6">
          <div>
            <h2 className="text-base font-semibold text-white">Organization Delivery Channels (Admin)</h2>
            <p className="text-xs text-gray-400 mt-1">
              Configure system-wide Resend Email and Slack webhook delivery channels for team projections.
            </p>
          </div>

          {!intCfg.encryption_available && (
            <div className="rounded-xl bg-amber-900/40 border border-amber-700 text-amber-200 px-4 py-3 text-xs leading-relaxed">
              <strong>SETTINGS_ENCRYPTION_KEY is not set on the server.</strong> API keys and webhook URLs cannot be saved to the database until it is configured.
            </div>
          )}

          {/* Org Email (Resend) */}
          <div className="p-4 rounded-lg bg-white/[0.02] border border-white/5 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-white">Email Delivery (Resend)</h3>
                <p className="text-xs text-gray-400">Automated system emails for job completion and report delivery.</p>
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input type="checkbox" checked={emailEnabled} onChange={e => setEmailEnabled(e.target.checked)} className="rounded" />
                Enabled
              </label>
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">From address</label>
              <input
                className="w-full rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-white text-xs outline-none focus:border-[#645DF6]"
                value={emailFrom}
                onChange={e => setEmailFrom(e.target.value)}
                placeholder="Facets Cloud <no-reply@facets.cloud>"
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Resend API key</label>
              <input
                type="password"
                autoComplete="off"
                className="w-full rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-white text-xs outline-none focus:border-[#645DF6]"
                value={emailKey}
                onChange={e => setEmailKey(e.target.value)}
                placeholder="Leave blank to keep current"
              />
              <p className="mt-1 text-[11px] text-gray-500">{secretLabel(intCfg.email.api_key, intCfg.email.env_fallback)}</p>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <button
                type="button"
                onClick={() => runTest('email')}
                disabled={emailTest.status === 'sending'}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 hover:bg-white/5 disabled:opacity-50 cursor-pointer"
              >
                {emailTest.status === 'sending' ? 'Sending…' : 'Send test email'}
              </button>
              {emailTest.status === 'ok' && <span className="text-xs text-green-400">{emailTest.msg}</span>}
              {emailTest.status === 'error' && <span className="text-xs text-red-400">{emailTest.msg}</span>}
            </div>
          </div>

          {/* Org Slack */}
          <div className="p-4 rounded-lg bg-white/[0.02] border border-white/5 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-white">Slack Delivery Channel</h3>
                <p className="text-xs text-gray-400">Team notifications posted to an incoming Slack webhook.</p>
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input type="checkbox" checked={slackEnabled} onChange={e => setSlackEnabled(e.target.checked)} className="rounded" />
                Enabled
              </label>
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Incoming webhook URL</label>
              <input
                type="password"
                autoComplete="off"
                className="w-full rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-white text-xs outline-none focus:border-[#645DF6]"
                value={slackWebhook}
                onChange={e => setSlackWebhook(e.target.value)}
                placeholder="Leave blank to keep current"
              />
              <p className="mt-1 text-[11px] text-gray-500">{secretLabel(intCfg.slack.webhook_url, intCfg.slack.env_fallback)}</p>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <button
                type="button"
                onClick={() => runTest('slack')}
                disabled={slackTest.status === 'sending'}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 hover:bg-white/5 disabled:opacity-50 cursor-pointer"
              >
                {slackTest.status === 'sending' ? 'Sending…' : 'Send test Slack message'}
              </button>
              {slackTest.status === 'ok' && <span className="text-xs text-green-400">{slackTest.msg}</span>}
              {slackTest.status === 'error' && <span className="text-xs text-red-400">{slackTest.msg}</span>}
            </div>
          </div>

          <button
            type="button"
            onClick={saveIntegrations}
            disabled={intSaving}
            className="rounded-lg bg-[#645DF6] hover:bg-[#645DF6]/85 px-6 py-2.5 text-sm font-medium text-white transition-colors disabled:opacity-50 cursor-pointer"
          >
            {intSaving ? 'Saving…' : 'Save Organization Delivery Channels'}
          </button>
        </div>
      )}
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   2. Tokens Tab (Personal Access Tokens & MCP Configuration)
   ───────────────────────────────────────────────────────────────────────────── */

function TokensTab() {
  const [tokens, setTokens] = useState<APIToken[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [expiryDays, setExpiryDays] = useState(90)
  const [scopes, setScopes] = useState<string[]>(['jobs:read', 'jobs:write'])
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [confirmRevoke, setConfirmRevoke] = useState<APIToken | null>(null)
  const [copied, setCopied] = useState(false)

  const loadTokens = () => {
    getUserTokens()
      .then(res => setTokens(res.tokens || []))
      .catch(() => setTokens([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadTokens()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    try {
      const res = await createUserToken({
        name: name.trim(),
        ttl_days: expiryDays,
        scopes,
      })
      setCreatedSecret(res.token)
      setName('')
      loadTokens()
      triggerNotificationRefresh()
      toast.success('API token created')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create token')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async () => {
    if (!confirmRevoke) return
    try {
      await revokeUserToken(confirmRevoke.key_id)
      setTokens(prev => prev.map(t => t.key_id === confirmRevoke.key_id ? { ...t, revoked_at: new Date().toISOString() } : t))
      toast.success('Token revoked')
      setConfirmRevoke(null)
    } catch (err) {
      toast.error('Failed to revoke token')
    }
  }

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Secret Display Banner */}
      {createdSecret && (
        <div className="p-4 rounded-xl bg-emerald-950/40 border border-emerald-500/40 space-y-3 anim-scale-in">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wider text-emerald-400 font-bold">New Token Generated</span>
            <button
              onClick={() => setCreatedSecret(null)}
              className="text-gray-400 hover:text-white text-xs cursor-pointer"
            >
              ✕ Dismiss
            </button>
          </div>
          <p className="text-xs text-gray-300">
            Copy this secret now. It will <strong className="text-white">never</strong> be displayed again.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-black/50 border border-emerald-500/30 rounded-lg p-2.5 font-mono text-xs text-emerald-300 break-all select-all">
              {createdSecret}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(createdSecret)
                setCopied(true)
                toast.success('Copied to clipboard')
                setTimeout(() => setCopied(false), 2000)
              }}
              className="btn-shimmer px-4 py-2.5 rounded-lg text-xs font-semibold text-white shrink-0 cursor-pointer"
            >
              {copied ? 'Copied!' : 'Copy Secret'}
            </button>
          </div>
        </div>
      )}

      {/* Create Token Box */}
      <form onSubmit={handleCreate} className="bg-white/[0.02] border border-white/10 rounded-xl p-5 space-y-4">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Generate New API Token</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Token Description / Name</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Claude Desktop / CI Pipeline"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-[#645DF6] outline-none"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Expiration</label>
            <select
              value={expiryDays}
              onChange={e => setExpiryDays(Number(e.target.value))}
              className="w-full bg-[#12131d] border border-white/10 rounded-lg px-3 py-2 text-xs text-white focus:border-[#645DF6] outline-none"
            >
              {EXPIRY_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs text-gray-400 mb-1.5">Scopes</label>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {SCOPE_OPTIONS.map(s => {
              const active = scopes.includes(s.value)
              return (
                <label
                  key={s.value}
                  className={`flex items-start gap-2 p-2.5 rounded-lg border text-xs cursor-pointer transition-colors ${
                    active ? 'bg-[#645DF6]/10 border-[#645DF6]/40 text-white' : 'bg-white/[0.02] border-white/10 text-gray-400'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => {
                      setScopes(prev => active ? prev.filter(x => x !== s.value) : [...prev, s.value])
                    }}
                    className="mt-0.5 rounded text-[#645DF6]"
                  />
                  <div>
                    <span className="font-semibold block">{s.label}</span>
                    <span className="text-[10px] text-gray-400">{s.desc}</span>
                  </div>
                </label>
              )
            })}
          </div>
        </div>

        <button
          type="submit"
          disabled={creating || !name.trim()}
          className="btn-shimmer px-5 py-2 rounded-lg text-xs font-semibold text-white cursor-pointer disabled:opacity-40"
        >
          {creating ? 'Creating…' : '+ Generate Token'}
        </button>
      </form>

      {/* Existing Tokens Table */}
      <div className="bg-white/[0.02] border border-white/10 rounded-xl overflow-hidden">
        <div className="p-4 border-b border-white/10 flex items-center justify-between">
          <h3 className="text-xs uppercase tracking-wider text-gray-400 font-semibold">Active Tokens ({tokens.length})</h3>
        </div>
        {loading ? (
          <p className="p-6 text-center text-xs text-gray-500">Loading tokens…</p>
        ) : tokens.length === 0 ? (
          <p className="p-6 text-center text-xs text-gray-500 italic">No API tokens generated yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-white/[0.02] border-b border-white/10 text-gray-400">
                <tr>
                  <th className="p-3">Name</th>
                  <th className="p-3">Token Prefix</th>
                  <th className="p-3">Scopes</th>
                  <th className="p-3">Created</th>
                  <th className="p-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {tokens.map(t => (
                  <tr key={t.key_id} className="hover:bg-white/[0.02]">
                    <td className="p-3 font-medium text-white">{t.name}</td>
                    <td className="p-3 font-mono text-gray-400">{t.display}</td>
                    <td className="p-3 text-gray-400 font-mono text-[11px]">{t.scopes.join(', ')}</td>
                    <td className="p-3 text-gray-400">{new Date(t.created_at).toLocaleDateString()}</td>
                    <td className="p-3 text-right">
                      {t.revoked_at ? (
                        <span className="text-red-400 text-[11px] font-mono">Revoked</span>
                      ) : (
                        <button
                          onClick={() => setConfirmRevoke(t)}
                          className="text-red-400 hover:text-red-300 text-[11px] font-medium cursor-pointer"
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={Boolean(confirmRevoke)}
        onOpenChange={o => { if (!o) setConfirmRevoke(null) }}
        title="Revoke API Token"
        description={`Revoke token "${confirmRevoke?.name}"? Any MCP servers or scripts using it will immediately stop working.`}
        confirmLabel="Revoke Token"
        destructive
        onConfirm={handleRevoke}
      />
    </div>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   3. Preferences Tab (Profile details, Engine, Formats)
   ───────────────────────────────────────────────────────────────────────────── */

function PreferencesTab({ user: _user }: { user: UserInfo }) {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getUserProfile()
      .then(setProfile)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!profile) return
    setSaving(true)
    try {
      await saveUserProfile(profile)
      toast.success('Profile preferences saved')
    } catch (err) {
      toast.error('Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  if (loading || !profile) {
    return <div className="p-8 text-center text-sm text-gray-500">Loading profile…</div>
  }

  return (
    <form onSubmit={handleSave} className="bg-white/[0.02] border border-white/10 rounded-xl p-6 space-y-6 max-w-2xl">
      <div>
        <h2 className="text-base font-semibold text-white">Profile &amp; Display Preferences</h2>
        <p className="text-xs text-gray-400 mt-1">
          Customize your display name and default regional settings.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Email</label>
          <input
            type="email"
            value={profile.email}
            readOnly
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-gray-400 outline-none cursor-not-allowed"
          />
        </div>
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Full Name</label>
          <input
            type="text"
            value={profile.name || ''}
            onChange={e => setProfile({ ...profile, name: e.target.value } as UserProfile)}
            placeholder="Your name"
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-[#645DF6] outline-none"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Company</label>
          <input
            type="text"
            value={profile.company || ''}
            onChange={e => setProfile({ ...profile, company: e.target.value } as UserProfile)}
            placeholder="Organization"
            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 focus:border-[#645DF6] outline-none"
          />
        </div>
        <div>
          <label className="block text-xs uppercase tracking-wider text-gray-400 mb-1">Time Zone</label>
          <select
            value={profile.time_zone || 'UTC'}
            onChange={e => setProfile({ ...profile, time_zone: e.target.value } as UserProfile)}
            className="w-full bg-[#12131d] border border-white/10 rounded-lg px-3 py-2 text-xs text-white focus:border-[#645DF6] outline-none"
          >
            <option value="UTC">UTC</option>
            <option value="America/New_York">Eastern Time (US &amp; Canada)</option>
            <option value="America/Chicago">Central Time (US &amp; Canada)</option>
            <option value="America/Denver">Mountain Time (US &amp; Canada)</option>
            <option value="America/Los_Angeles">Pacific Time (US &amp; Canada)</option>
            <option value="Europe/London">London</option>
            <option value="Europe/Berlin">Berlin</option>
            <option value="Asia/Kolkata">India Standard Time (Kolkata)</option>
            <option value="Asia/Singapore">Singapore</option>
            <option value="Asia/Tokyo">Tokyo</option>
          </select>
        </div>
      </div>

      <button
        type="submit"
        disabled={saving}
        className="btn-shimmer px-6 py-2.5 rounded-lg text-sm font-semibold text-white cursor-pointer disabled:opacity-40"
      >
        {saving ? 'Saving…' : 'Save Profile Changes'}
      </button>
    </form>
  )
}
