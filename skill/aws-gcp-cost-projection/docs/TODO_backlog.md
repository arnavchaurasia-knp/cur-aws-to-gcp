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
