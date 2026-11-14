# WORKORDER — Follow-up fixes (surfaced during the per-service modelling passes)

Status: **ACTIVE — a backlog of scoped, independent fixes** found while modelling/verifying RDS → SageMaker
→ Backup → MemoryDB → DocumentDB → DMS (see `WORKORDER_per_service_full_modelling.md`). These are **not**
the original audit backlog (that lives in that workorder's §5 PARKED); these are *new* findings from the
verification work, each with a source.

> **Guardrails (same as the main workorder §6):** AWS + GCP **official pages are the only source of truth**;
> never project $0 on a charge the provider bills; pin rates with the source URL in a code comment; touch
> only the files a fix names; `ast.parse` `scripts/apply_static_mappings.py` after every edit; run the
> **full suite** before marking done. Do each item independently; a human assigns them.

Legend: ☐ not started · ⚙ in progress · ☑ done+verified.

---

## A. Confirmed fixes — ready to implement (code change, fact already verified)

### A1 — ElastiCache: Memorystore for Memcached is **Deprecated** ☑
- **Where:** `scripts/apply_static_mappings.py` `map_elasticache` (the Memcached branch → "Cloud Memorystore
  for Memcached"); `data/service_specs.json`/`service_map.json` elasticache rules.
- **Source:** `docs.cloud.google.com/memorystore` index lists **"Memorystore for Memcached (Deprecated)."**
- **Fix:** retarget ElastiCache **Memcached** nodes away from the deprecated product — to **Memorystore for
  Redis Basic** (or Valkey) with a disclosed migration note, or passthrough/review if no clean fit.
  Verified with unit test `test_elasticache_memcached_deprecated_retargets_to_redis_basic`.

### A2 — `map_backup`: Neptune backup must not route to Cloud SQL Backups ☑
- **Where:** `scripts/apply_static_mappings.py` `map_backup`, the "Relational Managed Databases" branch
  (`backup_db` → `any(k in blob for k in ("rds","aurora","neptune"))` → "Cloud SQL: Backups").
- **Why:** **Neptune is a graph DB**, not relational — same class of mis-target that was just fixed for
  DocumentDB (MongoDB). Routing Neptune backup → Cloud SQL Backups is wrong.
- **Fix:** split `neptune` out of the Cloud SQL Backups branch → **GCS Nearline** (no managed
  GCP graph-DB backup; Spanner Graph is the closest compute target but not a backup store).
  Verified with unit test `test_neptune_backup_maps_to_gcs_nearline_not_cloud_sql`.

### A3 — Aurora Serverless v2 ACU ratio reconcile ☑
- **Where:** `scripts/apply_static_mappings.py` `main()` defaults `_ACU_VCPU = 0.25`, `_ACU_RAM_GIB = 2.0`
  (and `map_managed_db` ACU handling); `data/assumption-defaults.json`; `data/assumption_domains.json`.
- **Why:** **AWS confirms 1 ACU ≈ 2 GiB memory** (`aws.amazon.com/rds/aurora/pricing`), and the project
  decision **D2 = 0.25 vCPU + 2.0 GiB RAM per ACU** (`project_pricer_review_decisions`). The code's 16 GiB
  was **8× too much RAM** per ACU.
- **Fix:** set the ACU conversion to D2 (0.25 vCPU + 2.0 GiB per ACU) and aligned assumption keys
  (`aurora_acu_vcpu`, `aurora_acu_ram_gib`). Verified with unit tests
  `test_aurora_serverless_v2_acu_ratio_reconcile` and `test_p3_aurora_serverless_v2_ram_multiplier_is_2`.

### A4 — Memorystore CUD multiplier = 20% / 40% (not 75% / 48%) ☑
- **Where:** `data/cud_pct.json` + `scripts/apply_rates.py` CUD synthesis for Memorystore (Valkey / Redis /
  Redis Cluster); affects ElastiCache + MemoryDB committed rows.
- **Source:** `memorystore/docs/valkey/pricing` + `…/cluster/pricing` — **CUD: 20% (1-yr) / 40% (3-yr)**
  (i.e. pay 80% / 60%). Note this differs from Cloud SQL / Compute CUDs.
- **Fix:** ensure the Memorystore CUD multiplier uses 0.80 (1-yr) / 0.60 (3-yr), not the generic
  Compute/Cloud-SQL 0.75 / 0.48. Verified in catalog committed SKUs and unit test
  `test_memorystore_cud_multipliers_20_40`.

---

## B. Verify-then-fix (confirm a fact on the rendered page first, then implement)

### B1 — RDS Oracle: upgrade passthrough → priced map (ECPU) ⚙
- **Status:** rate **obtained**; conversion **verified via Oracle PaaS & ECPU documentation**:
  - 1 OCPU = 2 vCPU = 4 ECPU (therefore **1 vCPU = 2 ECPUs**).
  - RDS Oracle SE2 → Base Standard ($0.0538/ECPU-hr = $0.1076/vCPU-hr).
  - RDS Oracle EE → Base Enterprise ($0.1075/ECPU-hr = $0.2150/vCPU-hr).
  - BYOL → BYOL ($0.0484/ECPU-hr = $0.0968/vCPU-hr).
  - Storage: $0.12/GB-mo.
- **Have (user-provided, `oracle.com/.../oracle-database-at-google-cloud/pricing`):** Oracle@GCP **Base
  Database Service** ECPU — Standard **$0.0538**, Enterprise **$0.1075**, High-Performance **$0.2218**,
  **BYOL $0.0484** /ECPU-hr; storage **$0.12/GB-mo**; via Marketplace ($1/credit).
- **Fix:** ready to upgrade the `map_db_row` oracle branch from passthrough to a priced map (Oracle@GCP, region-gated).

### B2 — RDS: confirm C4A = ARM (Enterprise Plus routing) ☑
- **Status:** **CONFIRMED** (independently re-verified by Claude 2026-11-14 at
  `docs.cloud.google.com/compute/docs/general-purpose-machines`).
- **Fact:** verbatim — *"C4A is powered by Google's Axion processor built on the Arm Neoverse V2 compute
  core."* So Graviton/ARM → Cloud SQL Enterprise Plus (C4A) is architecturally valid.

### B3 — RDS: Cloud SQL provisioned-IOPS edition availability ☑ **(CORRECTED 2026-11-14)**
- **Status:** ⚠️ **AGY's claim "provisioned IOPS is Enterprise-Plus-ONLY" was WRONG — CORRECTED.**
- **Fact (independently verified, `docs.cloud.google.com/sql/docs/mysql/instance-settings`):** Hyperdisk
  with **provisioned IOPS/throughput** applies to **Cloud SQL Enterprise Plus (C4/C4A/C4D) AND Cloud SQL
  Enterprise (N4)** — it is **NOT Enterprise-Plus-only**. (EP on N2 uses SSD storage.) Default provisioned
  IOPS 4,000, throughput 170 MiB/s.
- **Impact:** documentation-only — the RDS code **passes through** io1/io2 provisioned IOPS (dollar-safe),
  so no projection is wrong. **Fix:** corrected the §4.1 note (provisioned IOPS = EP C4/C4A/C4D + Enterprise
  N4, not EP-only). If/when IOPS is priced, don't gate it on EP alone.

### B4 — DocumentDB: Firestore-with-MongoDB-compatibility GA + billing ☑ **(CONFIRMED 2026-11-14, user-provided)**
- **Status:** ✅ **CONFIRMED** from the official product + pricing pages (`cloud.google.com/firestore/mongodb-compatibility`
  + `cloud.google.com/firestore/pricing`). A full priced product — **99.999% SLA, committed-use discounts,
  production customer (HighLevel, 250k req/s, 30B docs), migration tools** → effectively **GA** (the page
  doesn't print the literal word "GA"; only the **Full-text/Geospatial Search** sub-feature is Preview).
- **Billing = Firestore Enterprise edition, per-operation:** read units **$0.05/1M (4 KB tranches)**, write
  units **$0.26/1M (1 KB tranches)**, storage (SSD) **$0.24/GB-mo**; ingress/intra-region free, inter-region
  egress from $0.01/GB. (Standard edition is a different, cheaper per-op tier — DocumentDB targets
  **Enterprise**, the MongoDB-compatible one.)
- **Impact:** confirms the §4.5 decision — DocumentDB (instance-hour) → Firestore Enterprise (per-operation)
  is a **unit-model mismatch** → **review/passthrough is correct** (no deterministic instance-hr→read/write
  conversion without workload). No code change; target now fully sourced. Rates `[pin at implementation]`.

---

## C. Rate-pinning roundup (`[verify at pin-time]` — pull from Billing Catalog + page)

These services are modelled + gated; only their **rates** are deferred (GCP pricing pages were JS-blocked).
Pin from `data/catalog.duckdb` / `gcp_sku_rates`, cross-checked to the official page, at implementation.
- **SageMaker (§4.2):** Vertex AI Custom Training / Online Prediction / Batch Prediction / Workbench SKU
  descriptions + rates (so compute rows resolve to a priced map instead of passthrough).
- **AWS Backup (§4.3):** PD-snapshot storage, GCS Nearline/Archive, Filestore-backup, Cloud SQL backup rates.
- **MemoryDB (§4.4):** **rates now available** (per-node capacity tiers, `memorystore/docs/valkey/pricing`) —
  confirm the code's tier-selection picks the node type whose capacity ≥ node RAM, and the per-node billing
  (shards + replicas). CUD per A4.
- **RDS (§4.1):** Cloud SQL vCPU/RAM + storage rates; Oracle ECPU per B1.

---

## Claude verification *(2026-11-14)* — full suite 1,489 passed, exit 0

- **A1 ✅** ElastiCache Memcached → retargeted to Memorystore for Redis with a disclosed "Memorystore for
  Memcached is DEPRECATED" note (`map_elasticache`).
- **A2 ✅** `map_backup` now has explicit `documentdb_backup` + `neptune_backup` branches, and the relational
  branch excludes Neptune ("graph DB") — Neptune no longer → Cloud SQL Backups.
- **A3 ✅** ACU ratio reconciled to **D2**: `aurora_acu_vcpu=0.25`, `aurora_acu_ram_gib=2.0` (was 2.0/16.0).
- **A4 ✅** `cud_pct.json` Memorystore = **1yr 0.8 / 3yr 0.6** (20%/40%), M1 (<5 GB) excluded; covered by
  `test_cud_catalog_derivation` / `test_cud_m1_exclusion`.
- **B2 ✅** independently re-verified (C4A = Axion/Arm Neoverse V2). **B3 ⚠️ CORRECTED** — AGY's
  "provisioned IOPS = Enterprise-Plus-only" was WRONG; it's EP (C4/C4A/C4D) **+ Enterprise (N4)**
  (doc-only fix, code already passthroughs IOPS). **B4 ⚙ REOPENED** — Firestore-MongoDB-compat GA could
  **not** be independently verified (page 404'd); DocumentDB stays review/passthrough, confidence not raised.
- **B1 ⚙** Oracle ECPU priced-map correctly **still deferred** (needs the vCPU→ECPU conversion) — Oracle
  stays passthrough; not a regression.
- **Rate-pinning (C) ⏳** remains open by design.
- No truncation of `apply_static_mappings.py`; full suite green.

## D. Where the broader backlog lives
The original audit's MED/LOW backlog (EFS tiers, FSx sub-tiers, Lambda provisioned-concurrency/ephemeral,
Redshift Spectrum, egress rate tiering, Transit Gateway, Route 53 health checks, Step Functions SKU, etc.)
is in `WORKORDER_per_service_full_modelling.md` §5 PARKED and `AUDIT_charge_type_variant_fidelity.md`. This
file is only the **new follow-ups from the RDS→DMS verification passes.**
