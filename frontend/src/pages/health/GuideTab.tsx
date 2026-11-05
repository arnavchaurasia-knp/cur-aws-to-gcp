import { StatusBadge } from './StatusBadge'

export function GuideTab() {
  return (
    <div className="space-y-6 max-w-4xl">
      {/* Intro */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          System Health Console Guide
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          This console provides a single source of truth for the health, integrity, and configuration of the cost
          projection engine. Every status indicator is derived directly from actionable issues.
        </p>
      </div>

      {/* Status Definitions */}
      <div className="space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">What the Status Words Mean</h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div className="p-3.5 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
            <div className="flex items-center gap-2">
              <StatusBadge status="ok" />
              <strong className="text-[color:var(--fcp-text-primary)]">Healthy</strong>
            </div>
            <p className="text-[color:var(--fcp-text-secondary)]">
              All monitored items are working normally. No actions are required.
            </p>
          </div>

          <div className="p-3.5 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
            <div className="flex items-center gap-2">
              <StatusBadge status="warn" />
              <strong className="text-[color:var(--fcp-text-primary)]">Needs attention</strong>
            </div>
            <p className="text-[color:var(--fcp-text-secondary)]">
              Non-critical issues or warnings exist (e.g. stale price catalog, slow AI response, or missing optional
              notifications). Projections may still run.
            </p>
          </div>

          <div className="p-3.5 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
            <div className="flex items-center gap-2">
              <StatusBadge status="fail" />
              <strong className="text-[color:var(--fcp-text-primary)]">Not working</strong>
            </div>
            <p className="text-[color:var(--fcp-text-secondary)]">
              A critical problem is preventing projections from running (e.g. missing AI key, missing catalog, or
              corrupted data file). Needs immediate attention.
            </p>
          </div>

          <div className="p-3.5 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
            <div className="flex items-center gap-2">
              <StatusBadge status="unknown" />
              <strong className="text-[color:var(--fcp-text-primary)]">Not checked yet</strong>
            </div>
            <p className="text-[color:var(--fcp-text-secondary)]">
              The component hasn't been verified recently. Run a test to confirm live connectivity.
            </p>
          </div>
        </div>
      </div>

      {/* When something is red checklist */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">
          When Something is Red: Quick Checklist
        </h4>
        <ol className="list-decimal pl-5 space-y-2 text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
          <li>
            <strong className="text-[color:var(--fcp-text-primary)]">Check the "Needs attention" list:</strong> The
            top of this page lists every active issue with plain-English instructions and a direct action button.
          </li>
          <li>
            <strong className="text-[color:var(--fcp-text-primary)]">AI Engine issues:</strong> Go to the{' '}
            <em>AI Engine</em> tab and click "Run Live Test". Verify that <code className="font-mono">ANTIGRAVITY_API_KEY</code>{' '}
            is configured and <code className="font-mono">agy</code> is installed.
          </li>
          <li>
            <strong className="text-[color:var(--fcp-text-primary)]">Catalog issues:</strong> Go to the{' '}
            <em>Pricing Catalog</em> tab. If prices are stale or missing, ensure{' '}
            <code className="font-mono">GOOGLE_CLOUD_API_KEY</code> is set, then click "Refresh Catalog Now".
          </li>
          <li>
            <strong className="text-[color:var(--fcp-text-primary)]">Data file issues:</strong> Go to the{' '}
            <em>Data Files</em> tab, open the flagged file, and check for invalid JSON syntax. You can restore an
            earlier backup directly from the Version History pane.
          </li>
          <li>
            <strong className="text-[color:var(--fcp-text-primary)]">Page looks outdated or mismatched:</strong> If a
            version mismatch banner appears or responses are missing, restart the backend server:{' '}
            <code className="font-mono bg-black/10 dark:bg-white/10 px-1 py-0.5 rounded">go run ./cmd/server</code>.
          </li>
        </ol>
      </div>

      {/* Where settings live */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Where Settings Live</h4>
        <div className="space-y-2 text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
          <p>
            • <strong className="text-[color:var(--fcp-text-primary)]">Settings & Secrets tab:</strong> Operational
            overrides such as <code className="font-mono">ANTIGRAVITY_API_KEY</code>,{' '}
            <code className="font-mono">GOOGLE_CLOUD_API_KEY</code>, and <code className="font-mono">LOG_LEVEL</code>.
            Encrypted in SQLite and live without restarts.
          </p>
          <p>
            • <strong className="text-[color:var(--fcp-text-primary)]">Integrations page:</strong> Outbound email
            (Resend API) and Slack webhook notifications.
          </p>
          <p>
            • <strong className="text-[color:var(--fcp-text-primary)]">Server .env file:</strong> Deployment-critical
            variables like <code className="font-mono">PORT</code>, <code className="font-mono">SESSION_SECRET</code>,
            and database paths.
          </p>
        </div>
      </div>

      {/* Full Documentation Reference */}
      <div className="p-4 rounded-xl bg-indigo-50 dark:bg-indigo-950/30 border border-indigo-200 dark:border-indigo-800/50 text-indigo-900 dark:text-indigo-200 text-xs flex items-center justify-between gap-3">
        <div>
          <strong className="block font-semibold">Technical Architecture Documentation</strong>
          <span>For engineers extending health checks, see the complete guide at </span>
          <code className="font-mono font-bold">docs/SYSTEM_HEALTH.md</code>
        </div>
      </div>
    </div>
  )
}
