# WORKORDER — EBS snapshot & S3 → REGIONAL GCP targets (not multi-region)

Status: COMPLETE · Implemented & Verified by AGY · 2026-11-19 · Full test suite 1732 passed.
Origin: surfaced by the multi-region `resolve_sku` audit (commit `2c1e7a3`, [[project_multiregion_resolve_sku_guard]]). That audit fixed Firestore and deliberately left PD-snapshot/GCS alone pending official-page verification. **Verification is now done (below) — the decision is REGIONAL.**

---

## Decision (verified against official pages 2026-11-19 via in-app browser)

AWS EBS snapshots and S3 buckets are **regional-scoped** (single region; no cross-region replication unless the user explicitly copies/replicates). The correct GCP target is therefore the **regional** (single-region) SKU, NOT multi-region. Two independent confirmations:

1. **GCP explicitly recommends regional snapshots.** `cloud.google.com/compute/disks-image-pricing`, Multi-regional standard snapshots section, verbatim: *"If you don't require the additional replication and resilience of multi-regional standard snapshots, we recommend using regional standard snapshots by specifying a regional location when snapshots are created."* An EBS snapshot has no such multi-region replication, so regional is the faithful mapping.
2. **Regional is cheaper** — the cached multi-region SKUs over-project:

| Charge | AWS scope | GCP Regional (Iowa) | GCP Multi-region | Over-projection if multi used |
|---|---|---|---|---|
| Standard disk snapshot | regional | **$0.000068493/GiB-h (~$0.05/GiB-mo)** | $0.000113699/GiB-h (~$0.083, Asia) | ~1.3–1.7× |
| GCS Standard storage | regional | **$0.000027397/GiB-h (~$0.020/GiB-mo)** | $0.000035616/GiB-h (~$0.026, Asia) | ~1.3× |

(GCS dual-region sits between: $0.000030137/GiB-h ~$0.022/GiB-mo. Still not single-region; regional is the target.)

Sources (pin in code comments): `https://cloud.google.com/compute/disks-image-pricing#regional-standard-snapshot-pricing`, `https://cloud.google.com/storage/pricing`.

---

## The mechanism already exists

Commit `2c1e7a3` added opt-in `prefer_single_region` to `resolve_sku()` and `lookup_sku_in_catalog()` (`_geo_rank` promotes a SKU that is `geoTaxonomy.type == "REGIONAL"` AND explicitly lists `gcp_region`, over MULTI_REGIONAL/GLOBAL SKUs that merely cover it; cache key gets a `|psr` suffix and skips the legacy 3-part key). This workorder just extends that flag to the snapshot + GCS mappers.

## Changes (all in `scripts/apply_static_mappings.py` unless noted)

### 1. Thread the flag through `resolve_sku_with_fallback` (the GCS path uses it)
`resolve_sku_with_fallback(candidates, gcp_region)` (~:2155) calls `resolve_sku(...)` with no flag. Add a `prefer_single_region=False` param and pass it through to the inner `resolve_sku` call. Default stays off.

### 2. EBS snapshot → regional PD snapshot
- **`map_block_storage`** snapshot branch (~:4372, `desc = "Storage PD Snapshot"` → `resolve_sku(...)`): pass `prefer_single_region=True`. (The regional-archive branch at ~:4370, `"Regional Archive Snapshot Data Storage"`, already names a regional SKU — leave it, but verify it still resolves.)
- **`map_backup`** EBS/AMI warm-backup branch (~:11273, `resolve_sku(GCP_COMPUTE_ENGINE, "Storage PD Snapshot", region)`): pass `prefer_single_region=True`.

### 3. S3 → regional GCS
- **`map_object_storage`** three `resolve_sku_with_fallback(candidates, gcp_region)` call sites (~:2380, :2436, :2489): pass `prefer_single_region=True` now that the param exists.
- **Catalog quirk to handle:** some GCS dual-region SKUs carry `geoTaxonomy.type == "REGIONAL"` (e.g. "Standard Storage South Carolina Dual-region"), so `_geo_rank` alone won't distinguish plain-regional from dual-region. BUT `"dual-region"` is already in `_NOISE_QUALIFIERS`, so `_select` pass-1 (non-noise) already prefers the plain regional SKU when one exists for the region. Expected outcome: plain "Standard Storage <City>" wins; dual-region is only chosen if the region has no plain single-region SKU (acceptable — still cheaper than multi). Verify per region; do NOT add new noise terms.

### 4. Leave intentional multi-region alone
- **DynamoDB Global Tables** replication writes correctly target multi-region nam5/Europe 3 (`map_dynamodb_capacity`, is_replication branch ~:5398) — do NOT add the flag there.
- GCS buckets the user explicitly created as multi/dual-region are not detectable from the CUR line; regional is the right default for the common case.

## Tests to add/extend
- EBS snapshot row (e.g. `USE1-...SnapshotUsage`, GB-Mo) → `map_block_storage` → resolved SKU is REGIONAL "Storage PD Snapshot in <city>", not "in US"/"in Asia" (assert `_geo_type == "REGIONAL"`), for ≥2 regions (us-east4, asia-south1).
- AWS Backup EBS row → `map_backup` → regional PD snapshot.
- S3 Standard storage row → `map_object_storage` → regional GCS Standard (not US Multi-region), for ≥2 regions.
- Guard: DynamoDB Global Tables replication still → nam5 multi-region (unchanged).
- Extend `tests/test_resolve_sku_single_region.py` or the per-service modelling tests.

## Verification battery (operator runs on "verify")
`ast.parse` + import + no-truncation (`map_block_storage`/`map_object_storage`/`map_backup`/`main` present) → targeted tests → full suite (expect ~1729 green) → empirical spot-check (EBS snapshot us-east4 → Virginia regional ~$0.05; S3 us-east1 → regional GCS ~$0.020) → commit main + push origin2 main:AGY.

## Dollar impact
Removes a systematic ~30% over-projection on EBS-snapshot and S3-storage lines (the two highest-row-count storage charge types on most bills). Direction: over-projection removed (projections go DOWN to the correct regional rate).
