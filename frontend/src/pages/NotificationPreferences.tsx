import { useEffect, useState } from 'react'
import { Layout } from '../components/Layout'
import { BackButton } from '../components/BackButton'
import {
  getNotificationSettings,
  updateNotificationSettings,
  type NotificationSettings,
  type UserInfo,
} from '../api/auth'
import {
  getIntegrations,
  updateIntegrations,
  testNotify,
  type IntegrationsConfig,
} from '../api/admin'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { ToggleSwitch } from '../components/ToggleSwitch'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

type TestState = { status: 'idle' | 'sending' | 'ok' | 'error'; msg?: string }

export function NotificationPreferences({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Notifications & Delivery — Facets')
  const isAdmin = Boolean(user.is_admin)

  // Direct user notification preferences
  const [settings, setSettings] = useState<NotificationSettings>({
    email_notifications: true,
    slack_notifications: false,
    slack_webhook_url: '',
    slack_channel: '',
  })
  const [loading, setLoading] = useState(true)
  const [savingUser, setSavingUser] = useState(false)

  // Admin organization delivery configuration
  const [intCfg, setIntCfg] = useState<IntegrationsConfig | null>(null)
  const [emailEnabled, setEmailEnabled] = useState(false)
  const [emailFrom, setEmailFrom] = useState('')
  const [emailKey, setEmailKey] = useState('')
  const [slackEnabled, setSlackEnabled] = useState(false)
  const [slackWebhook, setSlackWebhook] = useState('')
  const [intSaving, setIntSaving] = useState(false)
  const [emailTest, setEmailTest] = useState<TestState>({ status: 'idle' })
  const [slackTest, setSlackTest] = useState<TestState>({ status: 'idle' })
  const [deleteConfirm, setDeleteConfirm] = useState<'email' | 'slack' | null>(null)

  const refresh = async () => {
    setLoading(true)
    try {
      const s = await getNotificationSettings()
      setSettings(s)
    } catch {
      // quiet fallback
    }

    if (isAdmin) {
      try {
        const d = await getIntegrations()
        setIntCfg(d)
        setEmailEnabled(d.email.enabled)
        setEmailFrom(d.email.from)
        setSlackEnabled(d.slack.enabled)
      } catch {
        // quiet fallback
      }
    }
    setLoading(false)
  }

  useEffect(() => {
    refresh()
  }, [isAdmin])

  const handleSaveUserPrefs = async (e: React.FormEvent) => {
    e.preventDefault()
    setSavingUser(true)
    try {
      await updateNotificationSettings(settings)
      triggerNotificationRefresh()
      toast.success('Direct notification preferences saved')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save preferences')
    } finally {
      setSavingUser(false)
    }
  }

  const handleSaveIntegrations = async () => {
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
      toast.success('Organization delivery channels updated')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save delivery settings')
    } finally {
      setIntSaving(false)
    }
  }

  const handleDeleteConfiguredSecret = async (channel: 'email' | 'slack') => {
    setIntSaving(true)
    try {
      const payload = channel === 'email'
        ? { email: { api_key: '__DELETE__' } }
        : { slack: { webhook_url: '__DELETE__' } }
      const d = await updateIntegrations(payload)
      setIntCfg(d)
      if (channel === 'email') setEmailKey('')
      if (channel === 'slack') setSlackWebhook('')
      setDeleteConfirm(null)
      triggerNotificationRefresh()
      toast.success(`${channel === 'email' ? 'Resend API Key' : 'Slack Webhook URL'} removed`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete secret')
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
        const msg = channel === 'email'
          ? `Test email sent to ${user.email}`
          : 'Test message posted to Slack channel'
        set({ status: 'ok', msg })
        triggerNotificationRefresh()
        toast.success(msg)
      } else {
        const msg = r.error || 'Test notification failed'
        set({ status: 'error', msg })
        toast.error(msg)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Test failed'
      set({ status: 'error', msg })
      toast.error(msg)
    }
  }

  const secretLabel = (s: { configured: boolean; last4?: string } | undefined, envFallback: boolean) => {
    if (s?.configured) return `Configured ✓ (…${s.last4 ?? ''})`
    if (envFallback) return 'Configured via server environment variables'
    return 'Not configured'
  }

  if (loading) {
    return (
      <Layout user={user}>
        <div className="max-w-4xl mx-auto px-4 sm:px-6 py-12 text-center">
          <div className="flex items-center justify-center h-40">
            <div className="w-6 h-6 rounded-full border-2 border-[#00C2BB] border-t-transparent animate-spin" />
          </div>
          <p className="text-sm text-[var(--fcp-text-secondary)]">Loading notification preferences…</p>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-8">
        <BackButton fallback="/profile" />

        {/* Page Title */}
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">
            Notifications &amp; Delivery
          </h1>
          <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
            Configure automated status updates for your own projections, as well as team-wide delivery channels.
          </p>
        </div>

        {/* Section 1: Direct User Notifications */}
        <form onSubmit={handleSaveUserPrefs} className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 space-y-6 shadow-sm">
          <div className="border-b border-[var(--fcp-border-subtle)] pb-4">
            <h2 className="text-base font-bold text-[var(--fcp-text-primary)]">Direct Alerts</h2>
            <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
              Choose how you want to be notified when your cost projection jobs start, finish, or encounter errors.
            </p>
          </div>

          <div className="space-y-4">
            {/* Email Notifications Toggle */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-4">
              <ToggleSwitch
                checked={settings.email_notifications}
                onChange={(checked) => setSettings({ ...settings, email_notifications: checked })}
                label="Email Notifications"
                description="Receive automated completion summaries and direct download links to your registered email when jobs finish."
              />
            </div>

            {/* Slack Notifications Toggle */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-4 space-y-4">
              <ToggleSwitch
                checked={settings.slack_notifications}
                onChange={(checked) => setSettings({ ...settings, slack_notifications: checked })}
                label="Personal Slack Webhook Alerts"
                description="Post notifications to your personal incoming Slack webhook or private direct-message channel."
              />

              {settings.slack_notifications && (
                <div className="pt-3 border-t border-[var(--fcp-border-subtle)] space-y-3 anim-fade-in">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                      Personal Slack Webhook URL
                    </label>
                    <input
                      type="url"
                      value={settings.slack_webhook_url || ''}
                      onChange={(e) => setSettings({ ...settings, slack_webhook_url: e.target.value })}
                      placeholder="https://hooks.slack.com/services/T.../B.../..."
                      className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-xs text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                      Target Channel or Handle (optional)
                    </label>
                    <input
                      type="text"
                      value={settings.slack_channel || ''}
                      onChange={(e) => setSettings({ ...settings, slack_channel: e.target.value })}
                      placeholder="#finops-alerts or @username"
                      className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-xs text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#00C2BB] focus:ring-2 focus:ring-[#00C2BB]/20"
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="flex justify-end pt-2">
            <button
              type="submit"
              disabled={savingUser}
              className="btn-shimmer px-6 py-2.5 rounded-xl text-sm font-semibold text-white cursor-pointer disabled:opacity-40"
            >
              {savingUser ? 'Saving…' : 'Save Notification Preferences'}
            </button>
          </div>
        </form>

        {/* Section 2: Organization Delivery Channels (Admin Only) */}
        {isAdmin && intCfg && (
          <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 space-y-6 shadow-sm">
            <div className="border-b border-[var(--fcp-border-subtle)] pb-4">
              <div className="flex items-center gap-2">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-[#645DF6]/15 text-[#645DF6] dark:text-[#a5a0ff] border border-[#645DF6]/30">
                  Admin Configuration
                </span>
              </div>
              <h2 className="text-base font-bold text-[var(--fcp-text-primary)] mt-1.5">
                Organization Delivery Channels
              </h2>
              <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                System-wide Resend Email and Slack webhook delivery channels for all team projections and reports.
              </p>
            </div>

            {/* High-Contrast, Professional Encryption Key Banner */}
            {!intCfg.encryption_available && (
              <div className="rounded-2xl bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-700/60 p-4 sm:p-5 flex items-start gap-3.5 shadow-sm">
                <div className="w-9 h-9 rounded-xl bg-amber-100 dark:bg-amber-900/60 text-amber-700 dark:text-amber-300 flex items-center justify-center shrink-0 mt-0.5">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <div className="space-y-1">
                  <h3 className="text-sm font-bold text-amber-950 dark:text-amber-100">
                    SETTINGS_ENCRYPTION_KEY Not Configured
                  </h3>
                  <p className="text-xs text-amber-900/90 dark:text-amber-200/90 leading-relaxed">
                    <code className="font-mono bg-amber-100/90 dark:bg-amber-900/80 px-1.5 py-0.5 rounded text-amber-950 dark:text-amber-100 font-semibold border border-amber-300 dark:border-amber-700">
                      SETTINGS_ENCRYPTION_KEY
                    </code>{' '}
                    is not set on the server environment. API keys and webhook URLs cannot be saved to the database until it is configured.
                  </p>
                </div>
              </div>
            )}

            {/* Email Delivery (Resend) */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-5 space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Email Delivery (Resend)</h3>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                    Automated system emails for job completion and report download delivery.
                  </p>
                </div>
                <ToggleSwitch
                  checked={emailEnabled}
                  onChange={setEmailEnabled}
                  size="md"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">
                    From Address
                  </label>
                  <input
                    type="text"
                    value={emailFrom}
                    onChange={(e) => setEmailFrom(e.target.value)}
                    placeholder="Facets Cloud <no-reply@facets.cloud>"
                    className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-xs text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)]">
                      Resend API Key
                    </label>
                    <span className="text-[11px] font-mono text-[var(--fcp-text-tertiary)]">
                      {secretLabel(intCfg.email.api_key, intCfg.email.env_fallback)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="password"
                      autoComplete="off"
                      value={emailKey}
                      onChange={(e) => setEmailKey(e.target.value)}
                      placeholder="Enter new key or leave blank"
                      className="flex-1 rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-xs text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20"
                    />

                    {/* DELETE BUTTON FOR API KEY */}
                    {intCfg.email.api_key.configured && (
                      <button
                        type="button"
                        onClick={() => setDeleteConfirm('email')}
                        title="Delete configured Resend API key"
                        className="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 border border-rose-500/20 transition-colors cursor-pointer shrink-0"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                        <span>Delete Key</span>
                      </button>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => runTest('email')}
                  disabled={emailTest.status === 'sending'}
                  className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] px-4 py-2 text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] disabled:opacity-50 cursor-pointer transition-colors shadow-sm"
                >
                  {emailTest.status === 'sending' ? 'Sending test email…' : 'Send Test Email'}
                </button>
                {emailTest.status === 'ok' && (
                  <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{emailTest.msg}</span>
                )}
                {emailTest.status === 'error' && (
                  <span className="text-xs text-rose-600 dark:text-rose-400 font-medium">{emailTest.msg}</span>
                )}
              </div>
            </div>

            {/* Slack Delivery Channel */}
            <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-5 space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-sm font-semibold text-[var(--fcp-text-primary)]">Slack Delivery Channel</h3>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                    Team notifications and report summaries posted to an incoming Slack webhook.
                  </p>
                </div>
                <ToggleSwitch
                  checked={slackEnabled}
                  onChange={setSlackEnabled}
                  size="md"
                />
              </div>

              <div className="pt-2">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)]">
                    Incoming Webhook URL
                  </label>
                  <span className="text-[11px] font-mono text-[var(--fcp-text-tertiary)]">
                    {secretLabel(intCfg.slack.webhook_url, intCfg.slack.env_fallback)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    value={slackWebhook}
                    onChange={(e) => setSlackWebhook(e.target.value)}
                    placeholder="Enter new webhook URL or leave blank"
                    className="flex-1 rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-xs text-[var(--fcp-text-primary)] placeholder-gray-400 focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20"
                  />

                  {/* DELETE BUTTON FOR WEBHOOK URL */}
                  {intCfg.slack.webhook_url.configured && (
                    <button
                      type="button"
                      onClick={() => setDeleteConfirm('slack')}
                      title="Delete configured Slack Webhook URL"
                      className="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 border border-rose-500/20 transition-colors cursor-pointer shrink-0"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                      <span>Delete URL</span>
                    </button>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => runTest('slack')}
                  disabled={slackTest.status === 'sending'}
                  className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] px-4 py-2 text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] disabled:opacity-50 cursor-pointer transition-colors shadow-sm"
                >
                  {slackTest.status === 'sending' ? 'Posting test message…' : 'Send Test Slack Message'}
                </button>
                {slackTest.status === 'ok' && (
                  <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{slackTest.msg}</span>
                )}
                {slackTest.status === 'error' && (
                  <span className="text-xs text-rose-600 dark:text-rose-400 font-medium">{slackTest.msg}</span>
                )}
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={handleSaveIntegrations}
                disabled={intSaving}
                className="btn-shimmer px-6 py-2.5 rounded-xl text-sm font-semibold text-white cursor-pointer disabled:opacity-50"
              >
                {intSaving ? 'Saving…' : 'Save Organization Delivery Channels'}
              </button>
            </div>
          </div>
        )}

        {/* Delete API Key / Webhook Confirmation Dialog */}
        <ConfirmDialog
          open={Boolean(deleteConfirm)}
          onOpenChange={(o) => { if (!o) setDeleteConfirm(null) }}
          title={`Remove configured ${deleteConfirm === 'email' ? 'Resend API Key' : 'Slack Webhook URL'}?`}
          description={`This will erase the stored credential from the database and revert to environment defaults. Automated ${deleteConfirm} delivery will stop working until re-configured.`}
          confirmLabel="Delete Credential"
          destructive
          loading={intSaving}
          onConfirm={() => {
            if (deleteConfirm) {
              handleDeleteConfiguredSecret(deleteConfirm)
            }
          }}
        />
      </div>
    </Layout>
  )
}
