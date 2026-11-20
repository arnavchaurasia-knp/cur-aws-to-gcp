# TODO backlog — cost-projection modelling loop

Durable pick-up list. Source of truth for "what's next" once the current assignment closes.
Keep in sync with `VARIANT_FIDELITY_TRACKER.md` (variant gaps) and `WORKORDER_per_service_full_modelling.md` (§3 table + §7.1 log).

Legend: 🔴 real dollar error · 🟡 needs decision/verify · ⚪ low/cosmetic (dollar-safe)

---

## DONE

- ✅ **Systemic multi-region `resolve_sku` audit (2026-11-19).** Scanned the whole catalog for MULTI_REGIONAL SKUs
  the noise filter misses. **Findings:**
  - **Firestore Read Ops / Entity Writes** — multi-region "North America 5"/"Nam7" priced **exactly 2×** the
    regional "Iowa" SKU; the §4.18 city patterns are safe, but the **generic fallbacks** (`^Cloud Firestore Read
    Ops\b` / `Entity Writes\b`, `map_dynamodb_capacity`) matched both → 2× risk when the city pattern misses.
    **FIXED:** added opt-in `prefer_single_region` to `resolve_sku`/`lookup_sku_in_catalog` (`_geo_rank` promotes a
    SKU that genuinely serves the region as a single region); the two Firestore fallbacks pass it. Default behavior
    unchanged for all 139 other callers. Verified Iowa resolves; full suite green.
  - **BigQuery `^Analysis`** — only the regional us-central1 SKU matches → **safe, no change.**
  - **NOT flipped (deliberately):** blanket single-region preference would also change **PD Snapshot** (us-east4 →
    Virginia $0.05 vs US-multi $0.065) and **GCS Standard** (us-east1 → dual-region $0.022 vs US-multi $0.026).
    **GCP's default snapshot/bucket storage location is multi-regional**, so the cached multi-region SKUs may be
    *intentional*, not bugs — flipping them blanket would silently change deliberate mappings. Left the global
    default alone; see the new item below. See [[reference_gcp_firestore_rates]].

- ✅ **S3 (§4.24) full modelling — DONE+GATED 2026-11-20 (Claude, direct).** GCS tier rates verified exact vs
  official page (us-central1 ×730: Standard $0.0200 / Nearline $0.0100 / Coldline $0.0040 / Archive $0.0012).
  Fixed 3 real gaps: (1) s3_storage clause-3 `exclude_operation_re` lacked `Retrieval` → Glacier/IA retrieval
  REQUESTS swallowed as Archive storage; (2) IT-monitoring (unit=Objects→per_request) passthrough→ignore (Autoclass
  free); (3) S3 per-request passthroughs undeclared + escaping the guardrail. 31 catalog dims (0 spec-gaps),
  `test_s3_full_modelling.py` (17 tests). See [[reference_aws_gcp_service_comparison]].
  - ⚠️ **Guardrail blind spot found (follow-up).** `test_apply_static_mappings_undeclared_passthrough_guardrail`
    only flags dicts whose `strategy` is a literal `"passthrough"`. Passthroughs built with `strategy` as a
    conditional **expression** (e.g. the S3 per_request branch `strat if sku_id or strat != "map" else "passthrough"`)
    are invisible to it — they can carry no `passthrough_reason` and still pass. Worth extending the guardrail to
    also scan expression-valued strategy dicts. Overlaps `WORKORDER_passthrough_reason_declarations.md`.

- ✅ **EBS (§4.25) full modelling — DONE+GATED 2026-11-20 (Claude, direct).** All 8 GCP disk targets verified EXACT
  vs official page (us-central1 ×730): Balanced PD $0.100 / Hyperdisk Balanced Cap $0.080 / HB IOPS $0.00500 /
  HB Throughput $0.0400 / Hyperdisk Extreme IOPS $0.0320 / Storage PD $0.0400 / PD Snapshot $0.0500 / Regional
  Archive Snapshot $0.0190. AWS units verified (gp3/io1/io2 provisioned IOPS all $/IOPS-month → ×1.0). **FIXED real
  bug:** io1/io2 provisioned-IOPS rows were intercepted by the throughput-ceiling branch (`provisioned*1024 > 2400`
  true for an IOPS count) → mislabelled Extreme PD Throughput ×1024, bypassing the Hyperdisk Extreme IOPS handler;
  guarded with `not is_iops`. 17 catalog dims (0 gaps), `test_ebs_full_modelling.py` (12 tests).

- ✅ **Fargate (§4.26) full modelling — DONE+GATED 2026-11-20 (Claude, direct).** GKE Autopilot rates verified EXACT
  vs official page (us-central1 Default): GP vCPU $0.0445 / GP Memory $0.0049225 / Scale-Out Arm vCPU $0.0356 /
  Scale-Out Arm Memory $0.003938 / Pod Ephemeral Storage $0.0000548. **FIXED 2 bugs:** (1) ephemeral storage
  (usage_type has "GB-Hours") was priced as pod MEMORY → ~90× over-projection; now → Autopilot Pod Ephemeral Storage
  (storage). (2) ARM memory sweep included a phantom "Autopilot Arm Pod Memory" ($0.00352, NOT on official page);
  removed so ARM memory uses Scale-Out Arm ($0.003938). 10 catalog dims, `test_fargate_full_modelling.py` (7 tests).
  - ⚠️ **Follow-up (minor):** Windows Fargate dispatches cleanly to ecs_fargate_any WITH product_code set, but the
    mapper prices it as Linux Autopilot (no Windows-container premium; GKE Autopilot has no Windows pods). Acceptable
    approximation; a dedicated Windows-container decision (passthrough vs GKE Standard Windows node pool) is deferred.

## NEXT (pick after the above)

- ✅ **PD Snapshot & GCS → REGIONAL targets — DONE+GATED 2026-11-19 (commit 4ef2a08).** AGY implemented, Claude-
  verified (1732 tests). EBS snapshot → regional PD snapshot (us-east4 → Virginia, not multi-region US); S3 Standard
  → cheapest REGIONAL GCS (us-east1 → $0.022, down from US-multi $0.026); DynamoDB Global Tables stays multi-region.
  `prefer_single_region` extended to snapshot/backup/GCS mappers. Spec: `docs/WORKORDER_regional_vs_multiregion_targets.md`.

- ✅ **NAT gateway-hours per-VM-hour — DONE 2026-11-19 (commit f38a082).** Rate verified (cloud.google.com/nat/pricing:
  $0.0014/VM-hr, 32-VM cap → $0.044/hr flat, data $0.045/GiB). Per-VM model + 32-cap multiplier was already correct;
  the gap was that the BILL_CALIBRATED `nat_vms_per_gateway` derivation was dead code AND counted DISTINCT
  instance_type (undercount). Replaced with concurrent-VM estimate = Σ(compute instance-hours)/period/gateways
  (`_estimate_vms_per_gateway`), wired in with committed default 32 as fallback (no resource_id in CUR). 1746 tests.
  SP/RI netting already DECIDED (OD-vs-OD, [[project_pricing_and_cutover_decisions]]).

- ⚙ **passthrough_reason declarations (54 undeclared passthroughs) → WORKORDER WRITTEN.** LOW-sweep audit found 54
  inline passthroughs with no `passthrough_reason` → default `unclassified` (5% defect-budget consumers; dollar-safe).
  Fixed SSM (→ unresolved_sku). The rest need per-row judgement (don't auto-stamp) — spec in
  `docs/WORKORDER_passthrough_reason_declarations.md`. Overlaps [[project_passthrough_guard_tier_redesign]].

- ⚪ **LOW / cosmetic sweep (all dollar-safe today; declaration tidy-ups only):**
  - CloudTrail free management-events → explicitly declared IGNORE
  - S3 requests / retrieval → passthrough (confirm declared)
  - EBS sc1 → Standard PD
  - EFS Intelligent-Tiering
  - API Gateway REST / HTTP / WS collapse (dollar-safe)
  - Step Functions → deterministic Cloud Workflows SKU (currently unused)
  - SSM free standard parameters
  - GlobalAccelerator DT-premium
  - FSx for OpenZFS target choice (NetApp Volumes vs Filestore) — needs a decision
  - EKS extended-support

## SYSTEMIC (do alongside service work)

0a. 🔴 **Built-systems wiring audit → `docs/AUDIT_built_systems_wiring.md` (0/10 decided).** The unit-conversion
   finding is not unique: several SSOT/phase/unifier systems were built then never wired, so the ad-hoc code they were
   meant to replace still stands. Confirmed bypassed: `spec_match` (built to replace 3 spec-comparison copies, 0
   callers), `sku_catalog` (catalog-query SSOT, 0 importers — reads done ad-hoc), `candidate_tier` (Phase L, unwired),
   `service_contracts` (Phase J pre-mapping validator, not orchestrated), `spec_parity`/`validate_report` (built,
   not in CI). Every future fix must route through the intended system, not inline a 4th copy. See
   [[project_unit_conversion_engine]].

0. 🔴 **Unit-conversion engine + three-layer multiplier architecture → WORKORDER WRITTEN (0%).**
   `docs/WORKORDER_unit_conversion_engine.md`. The conversion engine (`unit_equivalence.conversion_factor`) exists but
   has 0 callers; 230 hand-coded `unit_multiplier` sites across 45 mechanics; GB_TO_GIB has drifted from the exact
   table value; physical-unit / SKU-granularity / semantic-component are conflated into one number. Staged P0–P6,
   per-mechanic tracker, exhaustive-unit gate (every catalog unit typed, generated from the billing API). Highest
   structural leverage — makes the io1/io2-IOPS and Fargate-ephemeral bug classes impossible. See
   [[project_unit_conversion_engine]].


1. Populate `service_usage_catalog.json` per service → completeness gate protects every variant. Highest leverage.
2. Extend `gcp_candidates` best-fit beyond ElastiCache (DynamoDB, RDS, Redshift, FSx).
3. Kill `else`-branch `strategy=map` on a guessed target (latent silent mis-targets).
3b. **Deterministic `_select` + ambiguous-resolution diagnostic → WORKORDER WRITTEN, READY FOR AGY**
   (`docs/WORKORDER_deterministic_sku_select.md`). Generalizes the multi-region fix: a generic `desc_pattern`
   matching several priced variants is resolved by catalog FILE ORDER (non-deterministic across refreshes,
   silently wrong). Audit: 28 services show same-region rate splits; real unguarded axes = **Spot** (`"spot"`
   not in `_NOISE_QUALIFIERS`, only `preemptible`), **Premium/Standard tiers**, **editions**, + a `Cloud IDS`
   21× oddity. Fix = add `"spot"` to the denylist + make `_select` deterministic (specificity→rate→sku_id) +
   emit `ambiguous_sku_resolution` diag. One structural change defends every service.
4. Keep rate granularity ≥ direction granularity.

---

## PROCESS NOTES

- **CUR docs — visit carefully, expect JS-rendered pages.** AWS CUR / Cost & Usage Report docs (and most AWS/GCP
  pricing pages) render tables via JavaScript, which plain WebFetch/web-fetch MISSES (same issue hit in prior
  sessions). **Use `bxw`** (Windows Go CLI at `C:\Users\arnav\Downloads\bxw\bxw.exe`, drives real Chrome over CDP)
  or the in-app browser to read them properly. The AWS docs `.md` twin trick
  (`https://docs.aws.amazon.com/.../page.md`) also flattens JS-tabbed tables. See [[reference_bxw_browser_tool]].
  When we revisit CUR column/field semantics, read the live docs this way — do not trust cached/remembered field
  lists.
- **Official-page tiebreaker (AGENTS.md):** every rate/unit/free-tier/billing fact verified against the provider's
  official pricing page before pinning; a $0 catalog SKU on something the provider charges for is a red flag.
- **Loop:** Claude models + verifies rates → AGY implements → operator says "verify" → Claude runs the battery
  (ast/import/no-truncation → targeted → full suite → empirical spot-check) → commit main + push origin2 main:AGY.
