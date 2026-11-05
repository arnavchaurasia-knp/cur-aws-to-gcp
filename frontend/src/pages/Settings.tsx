import { useEffect } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { BackButton } from '../components/BackButton'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

export function Settings({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Settings Overview — Facets')
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const isAdmin = Boolean(user?.is_admin)

  // Redirect legacy tab query params to the dedicated separated pages
  useEffect(() => {
    const tab = searchParams.get('tab')
    if (tab === 'preferences' || tab === 'profile') {
      navigate('/profile', { replace: true })
    } else if (tab === 'tokens') {
      navigate('/profile/tokens', { replace: true })
    } else if (tab === 'notifications' || tab === 'integrations') {
      navigate('/settings/notifications', { replace: true })
    }
  }, [searchParams, navigate])

  const settingsCards = [
    {
      title: 'User Profile & Identity',
      desc: 'Update your display name, company affiliation, department, and primary time zone.',
      to: '/profile',
      icon: (
        <svg className="w-6 h-6 text-[#00C2BB]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
      ),
      badge: 'Account',
      badgeColor: 'bg-[#00C2BB]/15 text-[#00C2BB] border-[#00C2BB]/30',
    },
    {
      title: 'Notifications & Delivery',
      desc: 'Control direct job completion emails, personal Slack alerts, and organization delivery webhooks.',
      to: '/settings/notifications',
      icon: (
        <svg className="w-6 h-6 text-[#645DF6]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
      ),
      badge: 'Alerts',
      badgeColor: 'bg-[#645DF6]/15 text-[#645DF6] dark:text-[#a5a0ff] border-[#645DF6]/30',
    },
    {
      title: 'API Tokens & Developer Access',
      desc: 'Manage personal access tokens for REST API integration, automated CI jobs, and MCP assistants.',
      to: '/profile/tokens',
      icon: (
        <svg className="w-6 h-6 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
        </svg>
      ),
      badge: 'Developer',
      badgeColor: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
    },
  ]

  const adminCards = [
    {
      title: 'Report Settings & Customization',
      desc: 'Configure PDF company branding, disclaimers, executive summary formats, and default rate options.',
      to: '/admin/report-settings',
      icon: (
        <svg className="w-6 h-6 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      ),
      badge: 'Reports',
      badgeColor: 'bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30',
    },
    {
      title: 'Pricing Assumptions & Math',
      desc: 'Audit global VM sizing factors, storage multipliers, network egress formulas, and license offsets.',
      to: '/admin/assumptions',
      icon: (
        <svg className="w-6 h-6 text-sky-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      ),
      badge: 'Pricing Engine',
      badgeColor: 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/30',
    },
    {
      title: 'Audit Trail & Compliance',
      desc: 'Inspect tamper-evident logs of all projection overrides, user management changes, and admin activities.',
      to: '/admin/audit',
      icon: (
        <svg className="w-6 h-6 text-violet-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
        </svg>
      ),
      badge: 'Compliance',
      badgeColor: 'bg-violet-500/15 text-violet-600 dark:text-violet-400 border-violet-500/30',
    },
  ]

  return (
    <Layout user={user}>
      <div className="w-full max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-8">
        <BackButton fallback="/" />

        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">
            Account &amp; System Settings
          </h1>
          <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
            Access dedicated management sections for profile details, automated alerts, and API developer tools.
          </p>
        </div>

        {/* User Account Settings Cards */}
        <div className="space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--fcp-text-tertiary)]">
            Account Management
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {settingsCards.map((card) => (
              <Link
                key={card.to}
                to={card.to}
                className="group rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm hover:border-[#00C2BB]/50 hover:shadow-md transition-all duration-200 flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <div className="w-12 h-12 rounded-xl bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] flex items-center justify-center group-hover:scale-105 transition-transform">
                      {card.icon}
                    </div>
                    <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${card.badgeColor}`}>
                      {card.badge}
                    </span>
                  </div>
                  <h3 className="text-base font-bold text-[var(--fcp-text-primary)] group-hover:text-[#00C2BB] transition-colors">
                    {card.title}
                  </h3>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-1.5 leading-relaxed">
                    {card.desc}
                  </p>
                </div>

                <div className="flex items-center gap-1.5 pt-5 text-xs font-semibold text-[#00C2BB] group-hover:translate-x-1 transition-transform">
                  <span>Manage</span>
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
              </Link>
            ))}
          </div>
        </div>

        {/* Admin Settings Cards (if admin) */}
        {isAdmin && (
          <div className="space-y-4 pt-4 border-t border-[var(--fcp-border-subtle)]">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--fcp-text-tertiary)]">
              Platform &amp; Pricing Administration
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              {adminCards.map((card) => (
                <Link
                  key={card.to}
                  to={card.to}
                  className="group rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-6 shadow-sm hover:border-[#645DF6]/50 hover:shadow-md transition-all duration-200 flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <div className="w-12 h-12 rounded-xl bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] flex items-center justify-center group-hover:scale-105 transition-transform">
                        {card.icon}
                      </div>
                      <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${card.badgeColor}`}>
                        {card.badge}
                      </span>
                    </div>
                    <h3 className="text-base font-bold text-[var(--fcp-text-primary)] group-hover:text-[#645DF6] transition-colors">
                      {card.title}
                    </h3>
                    <p className="text-xs text-[var(--fcp-text-secondary)] mt-1.5 leading-relaxed">
                      {card.desc}
                    </p>
                  </div>

                  <div className="flex items-center gap-1.5 pt-5 text-xs font-semibold text-[#645DF6] group-hover:translate-x-1 transition-transform">
                    <span>Configure</span>
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
