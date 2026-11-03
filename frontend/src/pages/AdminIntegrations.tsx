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
      toast.success('Saved')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'save failed'
      setError(msg)
      toast.error(msg)
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
      <div className="max-w-3xl mx-auto px-4 py-6">
        <BackButton fallback="/admin" />
        <h1 className="text-2xl font-semibold text-[color:var(--fcp-text-primary)] mb-1">Pricing Catalog &amp; Notifications</h1>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] mb-6">
          Freshness of the GCP SKU pricing catalog used for projections, and notification delivery channel configurations.
        </p>

        {loading && <p className="text-[color:var(--fcp-text-tertiary)] py-4">Loading…</p>}
        {error && <div className="mb-4 rounded-xl bg-red-900/40 border border-red-700 text-red-200 px-4 py-3 text-sm">{error}</div>}

        {/* Pricing Catalog Freshness */}
        {catalogStatus && (
          <section className="mb-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-medium text-[color:var(--fcp-text-primary)]">GCP Pricing Catalog</h2>
                <p className="text-xs text-[color:var(--fcp-text-secondary)]">Freshness of the GCP SKU catalog used for cost projections.</p>
              </div>
              <span className={`text-xs px-2.5 py-1 rounded-full font-semibold ${catalogStatus.stale ? 'bg-orange-950/30 text-orange-400 border border-orange-500/30' : 'bg-[#00C2BB]/10 text-[#00C2BB] border border-[#00C2BB]/30'}`}>
                {catalogStatus.stale ? 'Stale' : 'Fresh'}
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-base)]/50 p-4 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
                <div className="text-xs uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">Last refreshed</div>
                <div className="text-lg font-semibold tabular-nums text-[color:var(--fcp-text-primary)]">
                  {catalogStatus.age_days >= 9999 ? 'unknown' : `${catalogStatus.age_days} day${catalogStatus.age_days === 1 ? '' : 's'} ago`}
                </div>
              </div>
              <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-base)]/50 p-4 shadow-[var(--fcp-card-shadow)] anim-fade-in-up" style={{ animationDelay: '60ms' }}>
                <div className="text-xs uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">Status</div>
                <div className={`text-lg font-semibold ${catalogStatus.stale ? 'text-orange-400' : 'text-[#00C2BB]'}`}>
                  {catalogStatus.stale ? 'Stale' : 'Fresh'}
                </div>
              </div>
            </div>

            {catalogActionError && <p className="text-xs text-orange-400 mt-3">{catalogActionError}</p>}

            <div className="mt-4">
              <button
                type="button"
                onClick={handleCatalogRefresh}
                disabled={refreshingCatalog}
                className="btn-shimmer px-4 py-2 rounded-lg font-medium text-white text-xs disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {refreshingCatalog ? 'Starting refresh…' : 'Refresh catalog now'}
              </button>
            </div>
          </section>
        )}

        {cfg && !cfg.encryption_available && (
          <div className="mb-6 rounded-xl bg-amber-900/40 border border-amber-700 text-amber-200 px-4 py-3 text-sm">
            <strong>SETTINGS_ENCRYPTION_KEY is not set on the server.</strong> You can toggle channels and edit the
            From address, but API keys and webhook URLs cannot be saved until it is configured (base64 of 32 random bytes).
          </div>
        )}

        {cfg && (
          <>
            {/* Email card */}
            <section className="mb-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-medium text-[color:var(--fcp-text-primary)]">Email (Resend)</h2>
                <label className="flex items-center gap-2 text-sm text-[color:var(--fcp-text-secondary)] cursor-pointer">
                  <input type="checkbox" checked={emailEnabled} onChange={e => setEmailEnabled(e.target.checked)} />
                  Enabled
                </label>
              </div>
              <label className="block text-sm text-[color:var(--fcp-text-secondary)] mb-1">From address</label>
              <input
                className="w-full mb-3 rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-[color:var(--fcp-text-primary)] text-sm focus:outline-none focus:border-[#645DF6]"
                value={emailFrom}
                onChange={e => setEmailFrom(e.target.value)}
                placeholder="Facets Cloud <no-reply@facets.cloud>"
              />
              <label className="block text-sm text-[color:var(--fcp-text-secondary)] mb-1">Resend API key</label>
              <input
                type="password"
                autoComplete="off"
                className="w-full rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-[color:var(--fcp-text-primary)] text-sm focus:outline-none focus:border-[#645DF6]"
                value={emailKey}
                onChange={e => setEmailKey(e.target.value)}
                placeholder="Leave blank to keep current"
              />
              <p className="mt-1 text-xs text-[color:var(--fcp-text-tertiary)]">{secretLabel(cfg.email.api_key, cfg.email.env_fallback)}</p>
              <div className="mt-4 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => runTest('email')}
                  disabled={emailTest.status === 'sending'}
                  className="rounded-lg border border-[var(--fcp-border-subtle)] px-3 py-1.5 text-xs text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-bg-hover)] disabled:opacity-50 cursor-pointer"
                >
                  {emailTest.status === 'sending' ? 'Sending…' : 'Send test email'}
                </button>
                {emailTest.status === 'ok' && <span className="text-xs text-green-400">{emailTest.msg}</span>}
                {emailTest.status === 'error' && <span className="text-xs text-red-400">{emailTest.msg}</span>}
              </div>
            </section>

            {/* Slack card */}
            <section className="mb-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-medium text-[color:var(--fcp-text-primary)]">Slack</h2>
                <label className="flex items-center gap-2 text-sm text-[color:var(--fcp-text-secondary)] cursor-pointer">
                  <input type="checkbox" checked={slackEnabled} onChange={e => setSlackEnabled(e.target.checked)} />
                  Enabled
                </label>
              </div>
              <label className="block text-sm text-[color:var(--fcp-text-secondary)] mb-1">Incoming webhook URL</label>
              <input
                type="password"
                autoComplete="off"
                className="w-full rounded-lg bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] px-3 py-2 text-[color:var(--fcp-text-primary)] text-sm focus:outline-none focus:border-[#645DF6]"
                value={slackWebhook}
                onChange={e => setSlackWebhook(e.target.value)}
                placeholder="Leave blank to keep current"
              />
              <p className="mt-1 text-xs text-[color:var(--fcp-text-tertiary)]">{secretLabel(cfg.slack.webhook_url, cfg.slack.env_fallback)}</p>
              <p className="mt-1 text-xs text-[color:var(--fcp-text-tertiary)]">
                The webhook determines the target channel — create one per channel in Slack and paste its URL here.
              </p>
              <div className="mt-4 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => runTest('slack')}
                  disabled={slackTest.status === 'sending'}
                  className="rounded-lg border border-[var(--fcp-border-subtle)] px-3 py-1.5 text-xs text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-bg-hover)] disabled:opacity-50 cursor-pointer"
                >
                  {slackTest.status === 'sending' ? 'Sending…' : 'Send test message'}
                </button>
                {slackTest.status === 'ok' && <span className="text-xs text-green-400">{slackTest.msg}</span>}
                {slackTest.status === 'error' && <span className="text-xs text-red-400">{slackTest.msg}</span>}
              </div>
            </section>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="rounded-lg bg-[#645DF6] hover:bg-[#645DF6]/85 px-4 py-2 text-sm font-medium text-white transition-colors disabled:opacity-50 cursor-pointer"
              >
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </>
        )}
      </div>
    </Layout>
  )
}
