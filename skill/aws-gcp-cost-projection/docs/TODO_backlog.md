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

- 🟡 **PD Snapshot & GCS: regional vs multi-region target (NEEDS OFFICIAL-PAGE VERIFICATION).** Surfaced by the
  multi-region audit. EBS snapshots are regional; GCP PD snapshots **default to multi-region** storage but can be
  regional. Decide per service whether EBS→PD-snapshot and S3→GCS-Standard should target regional (cheaper, matches
  AWS's regional scope) or keep GCP's multi-region default. Same question for GCS dual-region. Verify GCP snapshot /
  GCS storage-location pricing + default behavior from the official pages (via bxw) before changing — do NOT flip
  blind. If regional is chosen, extend `prefer_single_region` to those mappers (mechanism already exists).

- 🟡 **NAT gateway-hours per-VM-hour decision (USER INPUT NEEDED).** Cloud NAT bills per-VM-hour, not per-gateway;
  needs a VM-count assumption. Anchor: Cloud-NAT-for-LB ">32 VM" (see [[reference_gcp_cloud_lb_rates]]).
  SP/RI netting is already DECIDED (OD-vs-OD, [[project_pricing_and_cutover_decisions]]) — not open.

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
