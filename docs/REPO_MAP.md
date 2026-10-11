# Repo Map — one-line scope per file

Purpose: project an AWS Cost & Usage Report (CUR/CSV/PDF) onto GCP cost, line item
by line item, and produce a comparison report. Three layers: React UI → Go API
(orchestrates) → Python skill (6-phase pipeline over one DuckDB file).

> Status note (2026-10): a per-service charge-type registry (`data/service_specs.json`
> + `scripts/spec_dispatch.py`) is being built to replace `classify_mechanics.py`'s
> routing. See `skill/aws-gcp-cost-projection/docs/SERVICE_SPECS_DESIGN.md`.

---

## Go backend

### cmd/
- `cmd/server/main.go` — process entry: load config, preflight, open DB, wire auth/
  spawner/watcher/handlers, reap orphan jobs, start refreshers, serve HTTP (:8080), graceful shutdown.

### internal/jobs/ (the core — job lifecycle & pipeline orchestration)
- `orchestrate.go` — the 6-phase pipeline definition (`phaseSpecs`) + `run_all.py`
  template; one `agy` LLM call per phase with deterministic scripts and a SQL gate around each.
- `spawner.go` — launches the Python skill as a detached subprocess (`run_all.py`); refine/continue path.
- `watcher.go` — supervises a running job (liveness by file mtime), retries (max 3) from checkpoint, finalizes on report presence, detects quota exhaustion.
- `handler.go` — HTTP handlers for jobs: Create(upload), List, GetByID, Retry, Refine, Progress, Download, Runs, Summary, admin list.
- `duckdb.go` — read-only queries into a job's `projection.duckdb` (CLI, falls back to python duckdb); `QueryAWSSpend`, `QueryRunResults`.
- `progress.go` — reads a job's `progress.json` into the progress API shape (phase 1..6).
- `facets.go` — @facets.cloud-only internal panel (job logs, portfolio summary html/xlsx).
- `backfill.go` — one-time: synthesize a `run_results` row for old jobs lacking one.
- `catalog_refresher.go` — background: refresh the GCP SKU catalog if >7 days old.
- `compute_availability_refresher.go` — background: daily refresh of per-region GCP machine availability.
- `admin_handlers.go` — admin endpoints (cancel/delete jobs, roles, catalog status, audit, analytics).
- `proc_unix.go` / `proc_windows.go` — OS-specific process group kill + liveness check.

### internal/ (supporting packages)
- `db/db.go` — SQLite (pure-Go) open + schema (jobs, users, admin_audit, report_shares).
- `db/jobs.go` — job CRUD + state transitions + listings + usage stats.
- `db/users.go` — user upsert / role get-set / list (RBAC).
- `db/report_shares.go` — public report-share tokens.
- `auth/oauth.go` — Google OAuth login/callback; `DEV_AUTH_BYPASS` fake session; domain allow-list.
- `auth/session.go` — AES-GCM encrypted session cookie, 8h TTL; IsAdmin / IsFacetsEmployee.
- `auth/middleware.go` — require-auth and require-admin middleware.
- `config/config.go` — env var loading + defaults (port, model, timeouts, data dir, allowed domains).
- `skill/skill.go` — locate the skill dir (`$SKILL_DIR` → ./skill → ~/.gemini install).
- `preflight/check.go` — startup checks: agy, skill present, python3/duckdb/jq/gzip, key scripts readable.
- `logger/logger.go` — structured logging init.
- `metrics/metrics.go` — expvar counters (jobs submitted/done/failed/retried).
- `notify/resend.go` — email notifications (Resend). `notify/slack.go` — Slack webhook notifications.

---

## Python skill — `skill/aws-gcp-cost-projection/`

### Phase 1 — Ingestion
- `scripts/ingest.py` — parse the bill (CUR/CSV/parquet/json/excel/pdf/zip), dedup into
  `aws_li_catalog`, map regions, look up instance specs, classify `is_workload`, reconcile sum.
- `scripts/prepare_ingest_fallback.py` — when all parsers miss: dump a bounded preview to a manifest for the LLM to normalize into a flat CSV, then re-run ingest.
- `scripts/region_prefix.py` — decode AWS usage-type region prefixes (APS3→ap-south-1) when the bill omits a region column.

### Phase 2 — Mapping (classify → price)
- `scripts/classify_mechanics.py` — **(being replaced)** stamp each row's `mechanic_group`
  via ~37 ordered first-match rules; `_misc_reason()` writes per-row LLM guidance for misc rows.
- `scripts/aws_normalizer.py` — Layer-1 identity: `canonical_service(product, product_code)` (product-code-first, 3-tier).
- `scripts/spec_dispatch.py` — **(new)** per-service charge-type dispatch engine reading `data/service_specs.json`; faithful routing replacement, parity-tested.
- `scripts/apply_static_mappings.py` — deterministic pricers for flat_hourly/object_storage/
  per_request/block_storage/data_transfer; exports the shared cheapest-family engine.
- `scripts/family_mapper.py` — size EC2/RDS instances → GCP family + core/RAM (+GPU) SKUs.
- `scripts/apply_commitment_ignores.py` — mark RI/SP/credit/refund rows as ignore ($0).
- `scripts/service_classifier.py` — Layer-2: force the canonical GCP target per `data/service_map.json` (keep/review/ignore).
- `scripts/service_map.json` lookups live in `data/service_map.json` (see Data).
- `scripts/merge_mappings.py` — bulk-insert all group mapping files into `aws_li_to_gcp_li`; strip phantom SKU IDs.
- `scripts/calibrate_confidence.py` — apply per-service confidence ceilings (`data/confidence-caps.json`).
- `scripts/reconcile_capacity.py` — never-underprovision: bump any GCP target below the AWS vCPU/RAM up to spec.
- `scripts/fix_storage_misroute.py` — reroute non-storage rows the LLM dumped on Cloud Storage → passthrough.
- `scripts/verify_golden_mappings.py` — regression check of known-good mappings.
- `scripts/candidate_pool.py` / `candidate_tier.py` — scored, tiered GCP candidate generation for compute.
- `scripts/spec_match.py` — shared `meets_or_exceeds()` spec comparison (vcpu/ram/iops/arch/…).
- `scripts/sku_catalog.py` — single source for querying the bundled catalog (`data/catalog.duckdb`).
- `scripts/classify_transfer.py` — deterministic data-transfer direction (ingress/egress/inter-AZ/region).
- `scripts/field_state.py` — annotate per-field availability (present_in_cur / needs_api / not_available).
- `scripts/service_contracts.py` — validate required fields per service before pricing.
- `scripts/egress_rates.py` — pinned GCP egress rates (synthetic SKUs) to avoid non-deterministic catalog matching.

### Phase 3 — Review
- `scripts/auto_review.py` — suggestion engine (no DB write): flag illegal passthroughs & spec violations, precompute candidates.
- `scripts/apply_review_fixes.py` — the single DB-write point for review: apply the LLM's confirm/override/veto decisions.
- `scripts/llm_override_guard.py` — format gate for Phase-3 override decisions.

### Phase 4 — Rate-card fill
- `scripts/apply_rates.py` — build `gcp_sku_rates` (lazy), synthesize CUD/Preemptible/global rates, build the `gcp_projection` view.
- `scripts/ensure_catalog_coverage.py` — pre-check every needed (service,region,sku); optional API refresh.
- `scripts/incremental_rerate.py` — add rates only for newly-referenced SKUs, rebuild the view (cheap re-rate).
- `scripts/prefetch_skus.py` — maintainer-only: pre-resolve SKU IDs into `data/resolved_skus.json`.
- `scripts/validate_fix.py` — Layer-4: deterministic autofix + gates (phantom-zero, under-projection, echo, reconciliation); watcher backstop in `--check-only`.
- `scripts/projection_view.py` — the single source of truth for the `gcp_projection` view DDL.

### Phase 5 — Outlier triage
- `scripts/detect_outliers.py` — run 9 outlier queries, write markdown/JSON; never fails.
- `scripts/outlier_gate.py` — deterministic safety net: clamp implausible blowups to passthrough.
- `scripts/auto_triage.py` — suggestion engine for structural outliers (no DB write).
- `scripts/apply_outlier_fixes.py` — apply the LLM's outlier decisions (if present).

### Phase 6 — Report
- `scripts/render_report.py` — deterministic HTML/MD report + service-type pills; create `run_results`.
- `scripts/export_report.py` — export projection as XLSX/CSV (component-ranked to avoid double-count).
- `scripts/final_mapping_sanity_check.py` — last deterministic net for small-dollar managed-DB mispricings.
- `scripts/generate_portfolio_summary.py` — cross-job consolidated summary.

### Catalog / maintenance / misc
- `scripts/config_loader.py` — `load_data_config(name)` → `data/<name>.json` (the "edit JSON, no code" mechanism).
- `scripts/log_utils.py` — shared logging + `fatal()` (writes progress.json + exit).
- `scripts/confidence_engine.py` — aggregate per-row signals into HIGH/MEDIUM/LOW confidence.
- `scripts/build_catalog_index.py` — build `data/catalog.duckdb` from the SKU catalog.
- `scripts/catalog_health_check.py` / `check_catalog_age.py` / `weekly_catalog_refresh.py` — catalog freshness/refresh.
- `scripts/verify_compute_availability.py` — probe per-region GCP machine availability.
- `scripts/gcp_sweep.py` — live-API cheapest-machine sweep (unexercised without GCP creds).
- `scripts/assumptions.py` / `explain_row.py` / `job_inspect.py` / `compare_runs.py` — introspection/debug helpers.
- `scripts/reconcile_capacity.py`, `region_prefix.py` — (listed above).
- `scripts/find-sku.sh` — CLI SKU lookup used by the LLM. `scripts/refresh-catalog.sh` / `build-catalog-index.sh` — catalog build/refresh. `preflight.sh` — skill preflight.

### Data (`data/`) — all editable JSON; scripts pick up changes next run
- `service_specs.json` — **(new)** per-service charge-type registry (identity → charge_type → mechanic + fields).
- `llm_guardrails.json` — **(planned)** single source of universal LLM guardrails.
- `service_map.json` — Layer-2 canonical AWS→GCP target + mode (keep/review/ignore).
- `aws-aliases.json` — product-name → canonical key. `aws-product-codes.json` — AWS ProductCode → canonical key.
- `aws-to-gcp-service-map.json` — service_hint → GCP service for the misc agent.
- `service-classification.json` — passthrough / self-host-on-GCE / sizing-sensitive lists + misc hints.
- `family_map.json` / `instance-family-map.json` — AWS instance family → GCP machine family + arch.
- `ec2-instance-types.json` / `rds-instance-types.json` / `instance-specs.json` — instance vCPU/RAM/arch tables.
- `gcp-model-config.json` — GCP compute/GPU/Memorystore model config. `compute_family_availability.json` — per-region availability.
- `egress-rates.json` — pinned egress rates. `cud_pct.json` — CUD discount multipliers.
- `storage-sku-keywords.json` — storage keyword/class maps. `transfer-direction-map.json` — transfer pattern → direction.
- `confidence-caps.json` — per-service confidence ceilings. `review-config.json` / `validation-rules.json` — phase 3/5 policy thresholds.
- `resolved_skus.json` — precomputed SKU-resolution cache. `services.json` — curated GCP service allow-list (110).
- `catalog.duckdb` + `skus/*.json.gz` — the bundled GCP pricing catalog (~85,952 SKUs). `CATALOG_META.json` — catalog provenance.

### phases/ (human docs) & reference/
- `phases/01-ingestion.md` … `06-report.md` — the original narrative spec per phase (intent; the orchestrator is the real runtime).
- `SKILL.md` — the skill's own guide (original agent-driven design; orchestrate.go is the productized version).
- `docs/SERVICE_SPECS_DESIGN.md` — the per-service registry design + migration plan.
- `docs/WORKORDER_misc_llm_guardrails.md` — work order to port misc guidance into the registry.

### tests/
- `tests/test_spec_dispatch.py` — **(new)** parity: spec dispatch == classify_mechanics across 43 services.
- `tests/test_classify_mechanics.py` — legacy classifier unit tests.
- `tests/test_ingest_*.py` — ingestion faithfulness/aggregation. `tests/test_static_mappers.py` — pricer tests.
- `tests/test_family_mapper_compute.py`, `test_managed_db_contract.py`, `test_spec_match.py`, `test_sku_catalog.py`,
  `test_aws_normalizer.py`, `test_aws_product_codes.py`, `test_resolve_product_codes.py`,
  `test_sku_pattern_coverage.py`, `test_resolved_skus_cache_freshness.py`, `test_verify_compute_availability.py` — targeted unit tests.

---

## Frontend — `frontend/` (React 19 + Vite + Tailwind)
- `src/main.tsx` / `src/App.tsx` — entry + router/auth shell (polls `getMe`).
- `src/pages/` — `Upload.tsx` (home), `JobStatus.tsx` (5s progress poll + totals), `SharedReport.tsx` (public),
  `Login.tsx`, `Facets.tsx`, `Admin*.tsx`.
- `src/components/` — `DropZone` (upload), `Summary` (markdown report), `RunHistory` (version diff), `Nav`, `JobList`.
- `src/api/` — `jobs.ts` / `reports.ts` / `auth.ts` / `admin.ts` / `facets.ts` (all hit `/api`, proxied to :8080).
- `src/lib/phases.ts` — the 6 phase labels (mirrors Go `TotalPhases`).
- `vite.config.ts` — dev server :5173 + `/api` → :8080 proxy.

---

## Top-level
- `README.md` — project overview (note: describes the 6 phases; some paths are illustrative).
- `AGENTS.md` — accumulated audit notes + implementation status (historical work log; verify claims against code).
- `Makefile` — build/run shortcuts. `.env.example` — required/optional env vars.
- `docs/REPO_MAP.md` — this file.
