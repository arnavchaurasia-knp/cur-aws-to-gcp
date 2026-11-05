import { useEffect, useState } from 'react'
import { Layout } from '../components/Layout'
import {
  getIntegrations,
  updateIntegrations,
  testNotify,
  getCatalogStatus,
  triggerCatalogRefresh,
  type IntegrationsConfig,
  type CatalogStatus,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'
import { triggerNotificationRefresh } from '../lib/notificationsContext'
import { toast } from 'sonner'
import { ToggleSwitch } from '../components/ToggleSwitch'
import { ConfirmDialog } from '../components/ConfirmDialog'

type TestState = { status: 'idle' | 'sending' | 'ok' | 'error'; msg?: string }

export function AdminIntegrations({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Pricing Catalog & Notifications')
  const [cfg, setCfg] = useState<IntegrationsConfig | null>(null)
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus | null>(null)
  const [refreshingCatalog, setRefreshingCatalog] = useState(false)
  const [catalogActionError, setCatalogActionError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Draft fields. Secrets are write-only: blank means "keep current".
  const [emailEnabled, setEmailEnabled] = useState(true)
  const [emailFrom, setEmailFrom] = useState('')
  const [emailKey, setEmailKey] = useState('')
  const [slackEnabled, setSlackEnabled] = useState(true)
  const [slackWebhook, setSlackWebhook] = useState('')

  const [emailTest, setEmailTest] = useState<TestState>({ status: 'idle' })
  const [slackTest, setSlackTest] = useState<TestState>({ status: 'idle' })
  const [deleteConfirm, setDeleteConfirm] = useState<'email' | 'slack' | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const [d, cat] = await Promise.all([
        getIntegrations(),
        getCatalogStatus().catch(() => null),
      ])
      setCfg(d)
      if (cat) setCatalogStatus(cat)
      setEmailEnabled(d.email.enabled)
      setEmailFrom(d.email.from)
      setSlackEnabled(d.slack.enabled)
      setEmailKey('')
      setSlackWebhook('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load settings')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const handleCatalogRefresh = async () => {
    setRefreshingCatalog(true)
    setCatalogActionError(null)
    try {
      await triggerCatalogRefresh()
      toast.success('Catalog refresh started')
      setTimeout(async () => {
        try {
          const cat = await getCatalogStatus()
          setCatalogStatus(cat)
        } catch { /* ignore */ }
      }, 3000)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'refresh failed'
      setCatalogActionError(msg)
      toast.error(msg)
    } finally {
      setRefreshingCatalog(false)
    }
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const d = await updateIntegrations({
        email: { enabled: emailEnabled, from: emailFrom, ...(emailKey ? { api_key: emailKey } : {}) },
        slack: { enabled: slackEnabled, ...(slackWebhook ? { webhook_url: slackWebhook } : {}) },
      })
      setCfg(d)
      setEmailKey('')
      setSlackWebhook('')
      triggerNotificationRefresh()
      toast.success('Settings saved successfully')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'save failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteSecret = async (channel: 'email' | 'slack') => {
    setSaving(true)
    try {
      const payload = channel === 'email'
        ? { email: { api_key: '__DELETE__' } }
        : { slack: { webhook_url: '__DELETE__' } }
      const d = await updateIntegrations(payload)
      setCfg(d)
      if (channel === 'email') setEmailKey('')
      if (channel === 'slack') setSlackWebhook('')
      setDeleteConfirm(null)
      triggerNotificationRefresh()
      toast.success(`${channel === 'email' ? 'Resend API Key' : 'Slack Webhook URL'} deleted`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete credential')
    } finally {
      setSaving(false)
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

  return (
    <Layout user={user}>
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        <BackButton fallback="/admin" />
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">
            Pricing Catalog &amp; Delivery Channels
          </h1>
          <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
            GCP SKU pricing catalog status used for projections, and organization notification delivery channels.
          </p>
        </div>

        {loading && <p className="text-[var(--fcp-text-tertiary)] py-4">Loading…</p>}
        {error && (
          <div className="rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-700 text-rose-800 dark:text-rose-200 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        {/* Pricing Catalog Freshness */}
        {catalogStatus && (
          <section className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-bold text-[var(--fcp-text-primary)]">GCP Pricing Catalog</h2>
                <p className="text-xs text-[var(--fcp-text-secondary)]">Freshness of indexed GCP SKUs queried during estimation.</p>
              </div>
              <span className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
                catalogStatus.stale
                  ? 'bg-amber-500/10 text-amber-500 border border-amber-500/30'
                  : 'bg-[#00C2BB]/10 text-[#00C2BB] border border-[#00C2BB]/30'
              }`}>
                {catalogStatus.stale ? 'Stale' : 'Fresh'}
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-4">
                <div className="text-xs uppercase tracking-wider text-[var(--fcp-text-tertiary)] mb-1">Last Refreshed</div>
                <div className="text-lg font-bold tabular-nums text-[var(--fcp-text-primary)]">
                  {catalogStatus.age_days >= 9999 ? 'Unknown' : `${catalogStatus.age_days} day${catalogStatus.age_days === 1 ? '' : 's'} ago`}
                </div>
              </div>
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] p-4">
                <div className="text-xs uppercase tracking-wider text-[var(--fcp-text-tertiary)] mb-1">Catalog State</div>
                <div className={`text-lg font-bold ${catalogStatus.stale ? 'text-amber-500' : 'text-[#00C2BB]'}`}>
                  {catalogStatus.stale ? 'Needs Refresh' : 'Up to date'}
                </div>
              </div>
            </div>

            {catalogActionError && <p className="text-xs text-rose-500 mt-3">{catalogActionError}</p>}

            <div className="mt-4">
              <button
                type="button"
                onClick={handleCatalogRefresh}
                disabled={refreshingCatalog}
                className="btn-shimmer px-4 py-2 rounded-xl font-medium text-white text-xs disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {refreshingCatalog ? 'Starting refresh…' : 'Refresh Catalog Now'}
              </button>
            </div>
          </section>
        )}

        {/* High-Contrast, Professional Encryption Warning */}
        {cfg && !cfg.encryption_available && (
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

        {cfg && (
          <>
            {/* Email (Resend) Card */}
            <section className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h2 className="text-base font-bold text-[var(--fcp-text-primary)]">Email Delivery (Resend)</h2>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">Automated job completion emails and report links.</p>
                </div>
                <ToggleSwitch
                  checked={emailEnabled}
                  onChange={setEmailEnabled}
                />
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)] mb-1.5">From Address</label>
                <input
                  className="w-full rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-[var(--fcp-text-primary)] text-xs focus:outline-none focus:border-[#645DF6]"
                  value={emailFrom}
                  onChange={e => setEmailFrom(e.target.value)}
                  placeholder="Facets Cloud <no-reply@facets.cloud>"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)]">Resend API Key</label>
                  <span className="text-[11px] font-mono text-[var(--fcp-text-tertiary)]">
                    {secretLabel(cfg.email.api_key, cfg.email.env_fallback)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    className="flex-1 rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-[var(--fcp-text-primary)] text-xs focus:outline-none focus:border-[#645DF6]"
                    value={emailKey}
                    onChange={e => setEmailKey(e.target.value)}
                    placeholder="Enter new key or leave blank"
                  />
                  {cfg.email.api_key.configured && (
                    <button
                      type="button"
                      onClick={() => setDeleteConfirm('email')}
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

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => runTest('email')}
                  disabled={emailTest.status === 'sending'}
                  className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] px-4 py-2 text-xs font-medium text-[var(--fcp-text-primary)] hover:border-gray-400 disabled:opacity-50 cursor-pointer"
                >
                  {emailTest.status === 'sending' ? 'Sending…' : 'Send Test Email'}
                </button>
                {emailTest.status === 'ok' && <span className="text-xs text-emerald-500 font-medium">{emailTest.msg}</span>}
                {emailTest.status === 'error' && <span className="text-xs text-rose-500 font-medium">{emailTest.msg}</span>}
              </div>
            </section>

            {/* Slack Card */}
            <section className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h2 className="text-base font-bold text-[var(--fcp-text-primary)]">Slack Delivery Channel</h2>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">Post team notifications to an incoming webhook.</p>
                </div>
                <ToggleSwitch
                  checked={slackEnabled}
                  onChange={setSlackEnabled}
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--fcp-text-secondary)]">Incoming Webhook URL</label>
                  <span className="text-[11px] font-mono text-[var(--fcp-text-tertiary)]">
                    {secretLabel(cfg.slack.webhook_url, cfg.slack.env_fallback)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    className="flex-1 rounded-xl bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3.5 py-2.5 text-[var(--fcp-text-primary)] text-xs focus:outline-none focus:border-[#645DF6]"
                    value={slackWebhook}
                    onChange={e => setSlackWebhook(e.target.value)}
                    placeholder="Enter new webhook URL or leave blank"
                  />
                  {cfg.slack.webhook_url.configured && (
                    <button
                      type="button"
                      onClick={() => setDeleteConfirm('slack')}
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
                  className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface-hover)] px-4 py-2 text-xs font-medium text-[var(--fcp-text-primary)] hover:border-gray-400 disabled:opacity-50 cursor-pointer"
                >
                  {slackTest.status === 'sending' ? 'Sending…' : 'Send Test Slack Message'}
                </button>
                {slackTest.status === 'ok' && <span className="text-xs text-emerald-500 font-medium">{slackTest.msg}</span>}
                {slackTest.status === 'error' && <span className="text-xs text-rose-500 font-medium">{slackTest.msg}</span>}
              </div>
            </section>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="btn-shimmer px-6 py-2.5 rounded-xl text-sm font-semibold text-white cursor-pointer disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save Settings'}
              </button>
            </div>
          </>
        )}

        {/* Delete Confirmation Dialog */}
        <ConfirmDialog
          open={Boolean(deleteConfirm)}
          onOpenChange={(o) => { if (!o) setDeleteConfirm(null) }}
          title={`Delete ${deleteConfirm === 'email' ? 'Resend API Key' : 'Slack Webhook URL'}?`}
          description="This credential will be removed from the database and revert to environment defaults."
          confirmLabel="Delete Credential"
          destructive
          loading={saving}
          onConfirm={() => {
            if (deleteConfirm) {
              handleDeleteSecret(deleteConfirm)
            }
          }}
        />
      </div>
    </Layout>
  )
}
