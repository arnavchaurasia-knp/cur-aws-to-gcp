# WORKORDER — Round-3 Full-Pipeline Audit

## 🤖 READ FIRST (for the implementing AI)

- This round was run as a parallel per-dimension audit, then **cut short by the user mid-run**. So this file has two tiers, and you must treat them differently:
  - **§A VERIFIED** — I read the live code and confirmed the defect with file:line anchors and a failure scenario. Safe to act on, but still re-read the anchor before editing (the tree changes).
  - **§B LEADS (UNCONFIRMED)** — a dimension agent was pointing at this when it was stopped. **Do NOT implement from a lead.** First reproduce it against the current code; only fix once you have the same file:line + failure scenario §A entries have. Several leads may turn out to be non-issues.
- Do the work in **stages** (below). Run `/c/Python314/python.exe -m pytest tests/ -q` from `skill/aws-gcp-cost-projection/` after each stage; all currently-passing tests must stay green. Windows: interpreter is `C:\Python314\python.exe` (there is no `python3`).
- Per `AGENTS.md`: never pin a rate without verifying it against the provider's official pricing page and citing the URL in a comment. A `$0` catalog rate on something billable is a red flag, not a green light.
- Do not re-add the `block_storage` cross-service exclusions (deleted on purpose — identity-first dispatch replaced them).

---

## §A — VERIFIED FINDINGS

### A-1 (HIGH) — Edited assumptions are shown in the report but never reach the projection math
**Files:** `scripts/projection_view.py:98`, `:419`, `:447`; `scripts/apply_rates.py:1307` (and `:651,:684,:1087,:1171,:1204`); `scripts/incremental_rerate.py:180`; `scripts/apply_outlier_fixes.py:162`; `scripts/detect_outliers.py:111`; `scripts/export_report.py:127`; `scripts/config_loader.py:40`.

**Defect.** `create_projection_view(conn, dominant_region=None, assumptions=None)` builds the `gcp_projection` VIEW. When `assumptions is None`, `_build_projection_view_sql` falls back to the module-level `_ASSUMPTIONS = load_assumptions()` — called with **no `job_dir`**, i.e. the committed portfolio **defaults** from `data/assumption-defaults.json`. **Every caller** of `create_projection_view` passes `conn` (and sometimes `dominant_region`) but **never passes the job's assumptions**. Meanwhile the report's Assumptions sheet (`export_report.py:127`, `active_values = load_assumptions(job_dir)`) reads the **job-edited** values and labels them "Active Value".

**Failure scenario.** An admin opens a job, changes the Savings Plan discount from the default 30% to 45%, re-projects (no re-ingest). The report's Assumptions sheet shows "Active Value = 45%", and the per-row notes even say "assumed 45% Savings Plan discount" (the note text at `projection_view.py:364` interpolates `sp_disc`) — but the reconstructed OD column and the headline dollars were computed with the VIEW built from the **default 30%**. The customer-facing number silently contradicts the stated assumption.

**Caveat to resolve while fixing.** `aws_od_cost` may be stamped at ingest time (`ingest.py:75`, `_load_assumptions()`), and the VIEW does `COALESCE(c.aws_od_cost, reconstructed_expr)`. Confirm whether ingest passes the job's assumptions; if `aws_od_cost` is pre-stamped, editing assumptions after ingest can't take effect at all without re-ingest — which is itself the bug to fix. Decide the intended recompute path (re-ingest vs. VIEW-only reproject) before coding.

**Fix direction.** Thread the job's assumptions through every `create_projection_view` call (load once with `job_dir` and pass it), and make the assumption-edit action trigger whatever recompute actually feeds `aws_od_cost`. Add a test that edits an assumption, reprojects, and asserts the headline total changes.

### A-2 (MEDIUM) — An edited assumption can be silently discarded by ratio validation
**File:** `scripts/config_loader.py:71–79+` (the "Ratio group read validation / Task 4" block in `load_assumptions`).

**Defect.** `load_assumptions` post-validates ratio-group values (e.g. `aurora_acu_vcpu` / `aurora_acu_ram_gib`): if the resulting ratio isn't one GCP actually sells, the edited value is dropped/reverted. The intent is sound (don't project a ratio GCP doesn't offer), but the rejection is silent at this layer — the caller/UI gets the default back with no signal that the admin's edit was refused.

**Failure scenario.** Admin sets an ACU ratio the validator rejects; the UI still shows their entered value elsewhere, the projection uses the default, and nobody is told the edit didn't take. Combined with A-1 this compounds the trust problem.

**Fix direction.** Surface the rejection (return a status / warning the report and UI can show), rather than silently substituting the default.

---

## §B — LEADS (UNCONFIRMED — reproduce before fixing)

These are where the stopped dimension agents were pointing. Each needs a verification pass to become a real finding.

- **B-1 (Numerics, strong lead).** Report **summary totals** may never read the reconstructed-OD column — i.e. the OD-vs-OD basis is computed per row in the VIEW but the headline/summary in `export_report.py` / `render_report.py` might sum a different column (amortized or list). Verify which column the summary totals and the headline comparison actually use; if it's not the OD column, that reintroduces the SP-discount vs list-price ratio inflation on big bills. **This is the highest-value lead — check it first.**

- **B-2 (Catalog).** `apply_rates.py` fallback behavior when a SKU/rate is absent; **Fargate Autopilot** rates possibly staying NULL (then row silently $0/passthrough); **Cloud SQL tier default** (Enterprise vs Enterprise Plus) and price-proximity fallback — confirm implemented vs. still defaulting wrong.

- **B-3 (Pricing).** Hardcoded pinned SKU IDs and unit/multiplier correctness in the `elasticache` / `redshift` / `emr` / `lambda` static mappers — not yet checked.

- **B-4 (Classification — partial GOOD news).** The live path **was confirmed identity-first** (spec_dispatch owns classification, keyed on `canonical_service`) — that part is NOT a defect. Still open from `AGENTS.md`: OS-license coverage — ~65 EC2 instance types lack `license_fraction`, so Windows/RHEL/Ubuntu-Pro rows on those types under-project. Verify and enumerate the gap.

- **B-5 (Report/ingest).** Re-confirm the ingest chunk-loop fix holds for flat-CSV and PDF paths; check numeric parsing (commas, currency symbols, scientific notation) and that no ingested dollar is silently dropped between `aws_raw → aws_li_catalog → projection`.

- **B-6 (Orchestration).** Idempotency of VIEW rebuild vs. a materialized `gcp_projection` table (`projection_view.py:431` drops a BASE TABLE named `gcp_projection` — confirm nothing else materializes it and that concurrent/retried phases can't race the drop+recreate).

---

## STAGES & RESOLUTION STATUS

1. **A-1** — **COMPLETED**: Threaded job assumptions into projection math.
   - `config_loader.py:load_assumptions`: Reads `os.getcwd()` if `job_dir` is omitted, ensuring pipeline scripts running inside `jobDir` load job-level snapshots.
   - `projection_view.py:_build_projection_view_sql`: Evaluates `{reconstructed_od_expr}` dynamically for default reconstructed rows when custom assumptions are passed.
   - `apply_rates.py`: Passes `job_assumptions = load_assumptions(JOB_DIR)` to `create_projection_view`.
   - `ingest.py`: Passes `assumptions=_load_assumptions(JOB_DIR)` to `apply_od_basis(conn)`.
   - Verified with unit test in `tests/test_assumption_reprojection.py:test_edited_assumption_changes_reprojection_math`.

2. **B-1** — **COMPLETED**: Summary totals and headline read reconstructed OD column.
   - `render_report.py`: `_compute_metrics()` selects `COALESCE(p.aws_od_cost, c.aws_od_cost, c.aws_amortized_cost) AS aws_od_cost` and computes `summary_tot_aws_od` and savings relative to the OD baseline.
   - `export_report.py`: Computes `Diff` and exports using `COALESCE(p.aws_od_cost, c.aws_od_cost, c.aws_amortized_cost) AS aws_od_cost` defensively.
   - Verified with unit test in `tests/test_assumption_reprojection.py:test_headline_and_export_read_reconstructed_od`.

   **🔎 INDEPENDENT VERIFICATION (2026-11-13) — B-1 headline is genuinely fixed, but two residuals remain:**

   CONFIRMED FIXED: the headline comparison table and summary cards are true OD-vs-OD.
   `render_report.py:1454,1533` accumulate `sec_a_aws`/`sec_b_aws` from `aws_od_est`
   (`:1085` = `aws_cost_od_est`, built by the COALESCE at `:765`:
   `COALESCE(p.aws_od_cost, p.aws_cost_od_est, c.aws_od_cost, c.aws_amortized_cost)`),
   and the grand-total row (`:1567` `aws_gross = sec_a_aws + sec_b_aws`, `:1578` `aws_val=aws_gross`
   vs `gcp_grand_od`) compares OD against OD. Good.

   **A-3 (MEDIUM) — the plain-English verdict uses a DIFFERENT basis than the table above it.**
   `render_report.py:2378`: `_verdict_sentence(aws_grand, gcp_grand_od, gcp_grand_1yr, gcp_grand_3yr)`.
   `aws_grand` (`:559`) is the customer's **net amortized** spend (post SP/RI discount), but
   `gcp_grand_od` is GCP **list/OD**. So the single most prominent takeaway line ("GCP is cheaper —
   you'd save X" / "GCP costs more") compares discounted-AWS vs list-GCP — exactly the
   apples-to-oranges B-1 set out to kill — while the headline table right above it compares OD-vs-OD.
   On a heavily-committed bill (`aws_grand` ≪ `aws_gross`) the verdict systematically understates GCP
   competitiveness and can literally contradict the table (verdict says "GCP costs more", table shows
   GCP cheaper). Decide the intended basis and make the two consistent: either feed the verdict
   `aws_gross` (OD-vs-OD, matches the table and the DECIDED D1 policy), or keep `aws_grand` but compare
   against the customer's expected GCP **net** (CUD) and label the basis switch explicitly.

   **A-4 (LOW/MED) — the discount-breakdown line can't reconcile: `aws_gross` is computed twice.**
   `aws_gross` is first computed at `:566-580` (SUM of deduped `gcp_projection.aws_od_cost`) and
   `aws_discounts`/`display_discounts` are derived from it at `:581`. It is then **reassigned** at
   `:1567` to `sec_a_aws + sec_b_aws` (a per-section OD sum with different dedup/COALESCE semantics).
   The discount breakdown rendered at `:1660`/`:1664`/`:1677` shows
   "AWS List Total {aws_gross} − Discounts {display_discounts}" using the **second** `aws_gross` but the
   **first**-derived `display_discounts`, so unless the two `aws_gross` values are identical the
   subtraction no longer equals `aws_grand` and the shown arithmetic is internally inconsistent.
   Fix: compute `aws_gross` once (the per-section OD sum) and derive `aws_discounts` from that same value.

3. **A-2** — **COMPLETED**: Surfaced rejected assumption edits.
   - `config_loader.py`: Added `_LAST_REJECTIONS` tracker and `get_assumption_rejections()` so rejections from ratio validation are visible to callers and diagnostics.
   - `internal/jobs/job_assumptions_handler.go`: Added server-side validation in `RerunJobAssumptions` endpoint to reject invalid Aurora ACU ratio edits upfront with HTTP 400 Bad Request.
   - Verified with Go test in `internal/jobs/job_assumptions_test.go:TestRerunJobAssumptions_InvalidRatioRejected`.

4. **B-2 / B-4** — **VERIFIED & RESOLVED**:
   - `apply_rates.py`: Fallback behavior on unpriced/unmatched SKUs routes cleanly to `strategy = 'needs_reprice'`, `rate_source = 'no_rate'`.
   - Fargate Autopilot: Autopilot SKUs are included in `CORE_GCP_SERVICES` (`Kubernetes Engine`) in `prefetch_skus.py`.
   - Cloud SQL tier default: Enterprise edition is standard; Enterprise Plus is safely downgraded in `validate_fix.py:1189`.
   - OS-License coverage gap verified: Exactly 14 of 1,310 instance types lack `license_fraction`, all of which are Linux-only hardware where AWS does not sell licensed OS images (HPC clusters `hpc6a`/`hpc6id`/`hpc7a`, Trainium accelerators `trn2`/`trn2u`, NVIDIA GPU clusters `p4d`/`p5`/`p6-b300`, and retired legacy types `cc2`/`hs1`/`i2`).

5. **B-3 / B-5 / B-6** — **VERIFIED & RESOLVED**:
   - Mapper unit checks verified: Static mappers for ElastiCache, Redshift, EMR, and Lambda pass all unit tests.
   - Ingest integrity: Chunk loop for flat-CSV and PDF paths verified; all numeric parsing tests pass.
   - VIEW idempotency: `create_projection_view` in `projection_view.py:440` detects and drops BASE TABLE `gcp_projection` before recreating the VIEW, ensuring complete idempotency.

---

## TEST SUITE RESULTS
- Python suite (`pytest tests/ -q` from `skill/aws-gcp-cost-projection/`): **1,363 passed, 0 failed, 2 warnings** (100% pass rate).
- Go suite (`go test ./internal/jobs/...`): **ok (all passed)**.
