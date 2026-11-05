import { useState } from 'react'
import { Layout } from '../components/Layout'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'

export function Docs({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Documentation & API — Facets')
  const [activeTab, setActiveTab] = useState<'architecture' | 'mcp' | 'api' | 'faq'>('architecture')

  return (
    <Layout user={user}>
      <div className="max-w-5xl mx-auto px-6 py-8">
        <BackButton fallback="/dashboard" />

        <div className="mt-2 mb-8">
          <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">
            Documentation & Developer Guides
          </h1>
          <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-1">
            Complete technical guide for the AWS → GCP Cost Projection Engine, MCP client setup, and REST API.
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-[var(--fcp-border-subtle)] gap-6 mb-8 text-xs font-medium">
          <button
            onClick={() => setActiveTab('architecture')}
            className={`pb-3 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'architecture'
                ? 'border-[var(--fcp-accent)] text-[color:var(--fcp-accent)] font-semibold'
                : 'border-transparent text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)]'
            }`}
          >
            Architecture & Pipeline
          </button>
          <button
            onClick={() => setActiveTab('mcp')}
            className={`pb-3 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'mcp'
                ? 'border-[var(--fcp-accent)] text-[color:var(--fcp-accent)] font-semibold'
                : 'border-transparent text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)]'
            }`}
          >
            Model Context Protocol (MCP)
          </button>
          <button
            onClick={() => setActiveTab('api')}
            className={`pb-3 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'api'
                ? 'border-[var(--fcp-accent)] text-[color:var(--fcp-accent)] font-semibold'
                : 'border-transparent text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)]'
            }`}
          >
            REST API Reference
          </button>
          <button
            onClick={() => setActiveTab('faq')}
            className={`pb-3 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'faq'
                ? 'border-[var(--fcp-accent)] text-[color:var(--fcp-accent)] font-semibold'
                : 'border-transparent text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)]'
            }`}
          >
            FAQ & Billing Rules
          </button>
        </div>

        {/* Tab Content */}
        {activeTab === 'architecture' && (
          <div className="space-y-6 text-sm text-[color:var(--fcp-text-primary)] leading-relaxed">
            <section className="p-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
              <h2 className="text-base font-bold mb-3 text-[color:var(--fcp-text-primary)]">
                6-Phase Hybrid Projection Pipeline
              </h2>
              <p className="text-xs text-[color:var(--fcp-text-secondary)] mb-4">
                The engine uses a deterministic-first architecture that couples regex normalizers with DuckDB analytics and LLM reasoning only for ambiguous multi-cloud conversions.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">1. Ingestion & Materiality</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Normalizes CSV, Parquet, or zipped CUR bill formats into a canonical schema. Filters out $0 noise and micro-cent line items while preserving reconciliation integrity.
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">2. Mechanics Classification</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Categorizes charges into mechanic groups: compute breakdown (vCPU/RAM), block storage, flat hourly, data transfer, or managed DB.
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">3. AI Mapping & Canonical Normalization</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Resolves AWS services and family identifiers to canonical GCP counterparts (e.g., EC2 c6g.2xlarge $\rightarrow$ C4A Arm, EBS gp3 $\rightarrow$ pd-balanced).
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">4. GCP Rate Application</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Joins mapped SKUs against our offline indexed Google Cloud catalog (90,000+ SKUs) and synthesizes 1-year and 3-year Committed Use Discounts (CUD).
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">5. Materiality Validation & Echo Clearing</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Runs strict validator gates to prevent service name echoing, catches unmapped runaway costs, and flags manual review items.
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="font-semibold text-cyan-400 mb-1">6. Report Generation</div>
                  <p className="text-[color:var(--fcp-text-secondary)]">
                    Produces interactive HTML reports, Excel workbooks with formula models, and per-line item audit DuckDB databases.
                  </p>
                </div>
              </div>
            </section>
          </div>
        )}

        {activeTab === 'mcp' && (
          <div className="space-y-6 text-sm text-[color:var(--fcp-text-primary)] leading-relaxed">
            <section className="p-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
              <h2 className="text-base font-bold mb-2">Model Context Protocol (MCP) Setup</h2>
              <p className="text-xs text-[color:var(--fcp-text-secondary)] mb-4">
                Connect external AI agents (like Claude Desktop, Cursor, or Praxis) directly to the Facets platform using the streamable HTTP MCP transport.
              </p>

              <h3 className="text-xs font-semibold text-[color:var(--fcp-text-primary)] uppercase tracking-wider mb-2">
                Claude Desktop Configuration (`claude_desktop_config.json`)
              </h3>
              <pre className="p-4 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)] font-mono text-xs overflow-x-auto text-cyan-300">
{`{
  "mcpServers": {
    "facets": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch", "http://localhost:8080/mcp"],
      "env": {
        "AUTHORIZATION": "Bearer fct_live_your_token_here"
      }
    }
  }
}`}
              </pre>

              <h3 className="text-xs font-semibold text-[color:var(--fcp-text-primary)] uppercase tracking-wider mt-6 mb-2">
                Available Agent Tools
              </h3>
              <ul className="list-disc pl-5 text-xs text-[color:var(--fcp-text-secondary)] space-y-1.5">
                <li><code className="text-cyan-400 font-mono">list_jobs</code>: Retrieve active and completed cost projections.</li>
                <li><code className="text-cyan-400 font-mono">get_job</code>: Get detailed status, bill metadata, and summary costs for a job.</li>
                <li><code className="text-cyan-400 font-mono">job_logs</code>: Stream execution logs and diagnostics in real time.</li>
              </ul>
            </section>
          </div>
        )}

        {activeTab === 'api' && (
          <div className="space-y-6 text-sm text-[color:var(--fcp-text-primary)] leading-relaxed">
            <section className="p-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)]">
              <h2 className="text-base font-bold mb-2">REST API Reference</h2>
              <p className="text-xs text-[color:var(--fcp-text-secondary)] mb-4">
                All endpoints require an active session cookie or a Bearer token generated under <span className="font-semibold">Settings → API Tokens</span>.
              </p>

              <div className="space-y-4 text-xs font-mono">
                <div className="p-3 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-bold">POST</span>
                    <span className="text-[color:var(--fcp-text-primary)]">/api/jobs</span>
                  </div>
                  <p className="text-[11px] text-[color:var(--fcp-text-secondary)] font-sans">
                    Submit a new AWS bill file (multipart/form-data) for cost projection analysis.
                  </p>
                </div>

                <div className="p-3 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-bold">GET</span>
                    <span className="text-[color:var(--fcp-text-primary)]">/api/jobs</span>
                  </div>
                  <p className="text-[11px] text-[color:var(--fcp-text-secondary)] font-sans">
                    List user cost projections with status, timestamps, and cost estimates.
                  </p>
                </div>

                <div className="p-3 rounded-lg bg-[var(--fcp-bg-canvas)] border border-[var(--fcp-border-subtle)]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-bold">GET</span>
                    <span className="text-[color:var(--fcp-text-primary)]">/api/jobs/:id/download</span>
                  </div>
                  <p className="text-[11px] text-[color:var(--fcp-text-secondary)] font-sans">
                    Download report artifacts (HTML or XLSX) by passing <code className="text-cyan-400">?format=xlsx</code>.
                  </p>
                </div>
              </div>
            </section>
          </div>
        )}

        {activeTab === 'faq' && (
          <div className="space-y-6 text-sm text-[color:var(--fcp-text-primary)] leading-relaxed">
            <section className="p-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)] space-y-4">
              <div>
                <h3 className="font-semibold text-[color:var(--fcp-text-primary)] text-sm mb-1">
                  How are AWS Savings Plans and EDP discounts handled?
                </h3>
                <p className="text-xs text-[color:var(--fcp-text-secondary)]">
                  Our pipeline compares unblended on-demand AWS rates to equivalent GCP On-Demand, 1-Year CUD, and 3-Year CUD rates. Credits, refunds, and Enterprise Discount Program (EDP) rows are parsed into the reconciliation summary to ensure total unblended spend matches the bill.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-[color:var(--fcp-text-primary)] text-sm mb-1">
                  What happens when a bill contains services without direct GCP analogues?
                </h3>
                <p className="text-xs text-[color:var(--fcp-text-secondary)]">
                  Specialized AWS services (e.g. AWS Marketplace, AWS Support, SES) carry a 1:1 passthrough rate with clear notation in Section B of the generated report, avoiding misleading under- or over-projections.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-[color:var(--fcp-text-primary)] text-sm mb-1">
                  Can I manually adjust a line item rate or suppress non-applicable items?
                </h3>
                <p className="text-xs text-[color:var(--fcp-text-secondary)]">
                  Yes! Authorized operators can open the <span className="font-semibold">Review & Overrides Console</span> (<code className="text-cyan-400">/jobs/:id/review</code>) to override target GCP SKUs, adjust unit rates, or suppress items with instant deterministic recalculation.
                </p>
              </div>
            </section>
          </div>
        )}
      </div>
    </Layout>
  )
}

