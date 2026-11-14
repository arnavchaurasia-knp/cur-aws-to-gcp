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

### B1 — RDS Oracle: upgrade passthrough → priced map (ECPU) ☑
- **Status:** rate **obtained**; conversion **verified via Oracle PaaS & ECPU documentation**:
  - 1 OCPU = 2 vCPU = 4 ECPU (therefore **1 vCPU = 2 ECPUs**).
  - RDS Oracle SE2 → Base Standard ($0.0538/ECPU-hr = $0.1076/vCPU-hr).
  - RDS Oracle EE → Base Enterprise ($0.1075/ECPU-hr = $0.2150/vCPU-hr).
  - BYOL → BYOL ($0.0484/ECPU-hr = $0.0968/vCPU-hr).
  - Storage: $0.12/GB-mo.
- **Have (user-provided, `oracle.com/.../oracle-database-at-google-cloud/pricing`):** Oracle@GCP **Base
  Database Service** ECPU — Standard **$0.0538**, Enterprise **$0.1075**, High-Performance **$0.2218**,
  **BYOL $0.0484** /ECPU-hr; storage **$0.12/GB-mo**; via Marketplace ($1/credit).
- **Fix:** upgraded the `map_db_row` oracle branch from passthrough to a priced map (Oracle@GCP Base Database Service, region-gated via `oracledatabase.googleapis.com`, 1 vCPU = 2 ECPUs; SE2 $0.0538, EE $0.1075, BYOL $0.0484 /ECPU-hr; verified via `test_rds_oracle_maps_to_oracle_gcp_priced_map` and `test_oracle_rate_injected_into_gcp_sku_rates`).

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

These services are modelled + gated; their **rates** were deferred (GCP pricing pages were JS-blocked).
**✅ SWEEP COMPLETE 2026-11-14 — ALL rates pinned, nothing deferred.** Every rate below is from the official
AWS + GCP pricing pages (user-provided, us-central1 / us-east-2). **Oracle is data-complete too** — the
Oracle@GCP ECPU rates (oracle.com, see B1 + §4.1.0: Base Standard $0.0538 / Enterprise $0.1075 /
High-Performance $0.2218 / BYOL $0.0484 per ECPU-hr, storage $0.12/GB-mo) **and** the conversion (**1 vCPU =
2 ECPU**, from 1 OCPU = 2 vCPU = 4 ECPU, per Oracle PaaS/ECPU docs) are both in hand. The only remaining Oracle
work is **AGY coding** the passthrough→priced map (B1) — not a missing rate or an open decision. Cross-check
each rate to `data/catalog.duckdb` / `gcp_sku_rates` at implementation.
- **SageMaker (§4.2):** ✅ **RATES PINNED 2026-11-14** (user-provided `cloud.google.com/vertex-ai/pricing`
  [now branded "Gemini platform"], us-central1, + `aws.amazon.com/sagemaker/ai/pricing`, us-east-2):
  - **Training → Vertex AI Custom Training** (machine-type node-hours, accelerators separate; **billed in
    30-sec increments**, no min duration). Vertex machine rates (us-central1/hr): n1-standard-4 **$0.2185**,
    n1-highmem-8 $0.544, n2-standard-4 $0.2234, e2-standard-4 $0.154, a2-highgpu-1g **$4.425** (incl A100),
    a3-highgpu-8g **$101.0** (H100). AWS side (us-east-2/hr): ml.m5.xlarge $0.23, ml.c5.xlarge $0.204,
    ml.g4dn.xlarge $0.7364, ml.p4d.24xlarge $25.25. (Vertex training management fee is folded into the machine
    rate above.)
  - **Real-time / Serverless / Async Inference → Vertex AI Online Prediction** (node-hours = vCPU + RAM + GPU,
    per-series): N1 vCPU **$0.036** / RAM **$0.005**; N2 vCPU $0.0364 / RAM $0.0049; E2 vCPU $0.0251 / RAM
    $0.0034; + GPU (A100 40GB $3.3741, L4 $0.644, T4 $0.402). **Batch Transform → Vertex Batch Prediction**
    (same node-hour rates).
  - **Notebook / Studio → Vertex AI Workbench:** Studio itself **free** (pay compute); Vertex Workbench =
    Compute Engine rate **+ management fee** $0.05/vCPU (N1/N2/A2), Premium GPU mgmt $2.48/GPU, Standard GPU
    $0.35/GPU. SageMaker notebook ml.t3.medium $0.05/hr, ml.m5.xlarge $0.23/hr.
  - **Feature Store → Vertex AI Feature Store V2** (GA Nov 2023): data-processing node **$0.08/hr**, optimized
    online serving **$0.30/hr** (incl 200 GB), Bigtable serving $0.94/hr + $0.25/GB-mo. **Legacy Feature Store
    sunsets 2027-02-17** → always target V2.
  - **Processing / Pipelines → Vertex AI Pipelines** ($0.03/run + compute). **Ground Truth → Vertex Data
    Labeling** (per-unit human-label tiers, e.g. image classification $35/1k units tier-1). **HyperPod →** GCE
    cluster compute (passthrough per §4.2). **Inferentia/Trainium →** TPU/GCE passthrough (no 1:1).
  - **Semantics:** Vertex training/prediction bill as **machine-type + accelerator** (not a flat "SageMaker"
    number); Studio/Workbench compute is free-tool + underlying compute; 30-sec increments; CUD via "Gemini
    Enterprise Flexible Savings Plan" (1yr/3yr) and GCE reservations/CUDs on reserved capacity.
- **AWS Backup (§4.3):** ✅ **RATES PINNED 2026-11-14** (user-provided `cloud.google.com/compute/disks-image-pricing`
  + `cloud.google.com/storage/pricing` us-central1 + `aws.amazon.com/backup/pricing` us-east-2):
  - **GCP targets (us-central1, monthly):** PD **Standard snapshot $0.05/GiB-mo** ($0.000068493/GiB-hr),
    **Archive snapshot $0.019/GiB-mo** ($0.000026027) + retrieval $0.019/GiB. GCS: Standard **$0.02**, Nearline
    **$0.01**, Coldline **$0.004**, Archive **$0.0012**/GiB-mo (retrieval: Nearline $0.01 / Coldline $0.02 /
    Archive $0.05/GiB; min-duration 30/90/365 d). Cloud SQL backup **$0.08/GiB-mo**; Filestore Backup $0.08.
  - **AWS Backup (us-east-2, warm/cold GB-mo):** EBS snapshot $0.05 / $0.0125; EFS $0.05 / $0.01; RDS $0.095;
    Aurora $0.021; DynamoDB $0.10 / $0.03; FSx (all) $0.05; DocumentDB/Neptune $0.021; S3 $0.05; Redshift
    $0.023 (first 50 TB). Restore: EFS $0.02, EBS free(warm)/$0.03(cold), RDS/Aurora free.
  - **Mapping/semantics:** **EBS snapshot → PD Standard snapshot ($0.05 ≈ AWS $0.05, near-parity)**; EBS cold
    → PD Archive snapshot ($0.019). **DB snapshots (RDS/Aurora/DocumentDB/Neptune) → Cloud SQL Backup $0.08**
    (invariant: never live-capacity Hyperdisk, per §4.3 `map_backup`). **EFS/FSx/S3 backup → GCS Nearline $0.01**
    (cheapest file/object backup) or Filestore Backup $0.08 (NFS-native). DynamoDB backup → GCS/Firestore.
    Redshift snapshot → GCS/BQ. **PD snapshots are incremental+compressed** (like AWS) so projecting on raw
    provisioned size over-states — project on the AWS backup GB-mo actually billed. Cross-region snapshot
    egress table also captured on the page.
- **MemoryDB (§4.4):** ✅ **RATES PINNED 2026-11-14** (user-provided `memorystore/docs/valkey/pricing` +
  `…/cluster/pricing` us-central1 + `aws.amazon.com/memorydb/pricing` us-east-2):
  - **Memorystore node types (per-node/hr, Valkey & Redis Cluster tables identical):** shared-core-nano 1.4 GB
    **$0.0318**, standard-small 6.5 GB **$0.1425**, highmem-medium 13 GB **$0.1923**, highcpu-medium 13 GB
    $0.4986, standard-large 26 GB **$0.5698**, highmem-xlarge 58 GB $0.8581, highmem-2xlarge 110 GB $1.6274.
    **CUD 20% (1yr) / 40% (3yr)** — confirms A4. Billing = per-node × (**shards + replicas**), 1-sec increments.
    Backups **$0.08/GB-mo** ($0.00011111/GB-hr); **AOF persistence $0.40/GB-mo** ($0.00054795/GB-hr, opt-in).
  - **AWS MemoryDB (us-east-2):** db.r6g/r7g.large 13 GB **$0.2163**, .xlarge 26 GB **$0.4319**, .2xlarge 53 GB
    $0.8624, .4xlarge 106 GB $1.7241; GP t4g.small $0.0336 / t4g.medium $0.0679; data-tiering r6gd nodes.
    **Data written: first 10 TB/mo free, then $0.04/GB** (writes only). Snapshot **$0.021/GB-mo** (free up to
    cluster size / 1-day retention). Multi-Region DT-out $0.02/GB.
  - **Mapping/semantics:** node → Memorystore by **capacity ≥ node RAM** (r6g.xlarge 26 GB → standard-large
    26 GB $0.5698; r6g.large 13 GB → highmem-medium 13 GB $0.1923). **Both bill per-node × (shards+replicas) →
    parity billing model.** MemoryDB is **durable by default** (txn-log in price); Memorystore AOF is an opt-in
    add-on ($0.40/GB-mo) — don't auto-add unless durability must be matched. **Memorystore has NO per-GB
    data-written charge** (AWS charges $0.04/GB >10 TB) — do NOT invent one. Memcached deprecated → Redis/Valkey
    (A1). CUD 20/40 not 75/48 (A4).
- **RDS (§4.1):** ✅ **RATES PINNED 2026-11-14** (user-provided pages, us-central1): Cloud SQL Enterprise vCPU
  $0.0413 / Mem $0.007; EP C4A $0.0537 / $0.0091 (Arm); SSD $0.17, Backups $0.08, Hyperdisk IOPS $0.02/IOPS-mo,
  Throughput $0.08/MBps-mo; SQL Server license Ent $0.47/core-hr (4-core min); Extended support $0.07/vCPU-hr;
  CUD 75%/48%. AlloyDB N2/C4A $0.06608 / $0.0112, storage $0.30. Aurora storage $0.10 (Std) / $0.225 (IO-Opt).
  **Only Oracle ECPU (B1) still deferred** — needs the vCPU→ECPU conversion.
- **Lambda (§4.7):** ✅ **RATES PINNED 2026-11-14** (user-provided `cloud.google.com/run/pricing` us-central1 +
  `aws.amazon.com/lambda/pricing` us-east-2):
  - **AWS Lambda:** duration **$0.0000166667/GB-sec** (x86, first 6B; Arm $0.0000133334) + **$0.20/1M requests**;
    free tier 400k GB-sec + 1M req. Ephemeral storage $0.0000000309/GB-sec (512 MB free). Provisioned
    Concurrency $0.0000041667/GB-sec + duration $0.0000097222/GB-sec. Lambda@Edge $0.60/1M + $0.00005001/GB-sec.
  - **Cloud Run target** — two billing models; **request-based is the Lambda analogue** (pay per request +
    active CPU/mem): CPU active **$0.000024/vCPU-sec**, Memory **$0.0000025/GiB-sec**, **Requests $0.40/1M**
    (2× Lambda's $0.20); free 180k vCPU-s + 360k GiB-s + 2M req. Instance-based (full-lifetime, 1-min min): CPU
    $0.000018/vCPU-sec, Mem $0.000002/GiB-sec, no request charge. **Jobs** = instance-based rates.
  - **Conversion semantics (the key modeling point):** Lambda bundles CPU+RAM into **one GB-sec** (CPU scales
    with memory: 1 vCPU ≈ 1,769 MB); Cloud Run bills **vCPU-sec and GiB-sec separately**. So a Lambda row at
    M MB for D sec → Cloud Run ≈ (M/1769) vCPU-sec×D × $0.000024 + (M/1024) GiB-sec×D × $0.0000025. Not a flat
    GB-sec multiplier — needs the memory→vCPU split.
  - **Provisioned Concurrency → Cloud Run min-instances** (idle rate $0.0000025/vCPU-sec + $0.0000025/GiB-sec).
    **Ephemeral storage:** Cloud Run now HAS an Ephemeral Disk SKU ($0.000109589/GiB-hr ≈ $0.08/GiB-mo) — prior
    §4.7 "ignore" can upgrade to a map if the CUR carries ephemeral-GB. **Lambda@Edge → Cloud Run (multi-region)
    / Media CDN**, passthrough. CUD: Cloud Run CUD 1/3-yr or Compute Flexible CUD (shared w/ GKE+GCE).

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
- **B1 ✅** Oracle ECPU priced-map implemented: 1 vCPU = 2 ECPUs, Base Standard ($0.0538), Base Enterprise ($0.1075), and BYOL ($0.0484) /ECPU-hr rates injected into `gcp_sku_rates`, region-gated to `oracledatabase.googleapis.com` (unavailable regions stay passthrough). Covered by `test_rds_oracle_maps_to_oracle_gcp_priced_map` and `test_oracle_rate_injected_into_gcp_sku_rates`.
- **Rate-pinning (C) ⏳** remains open by design.
- No truncation of `apply_static_mappings.py`; full suite green.

## D. Where the broader backlog lives
The original audit's MED/LOW backlog (EFS tiers, FSx sub-tiers, Lambda provisioned-concurrency/ephemeral,
Redshift Spectrum, egress rate tiering, Transit Gateway, Route 53 health checks, Step Functions SKU, etc.)
is in `WORKORDER_per_service_full_modelling.md` §5 PARKED and `AUDIT_charge_type_variant_fidelity.md`. This
file is only the **new follow-ups from the RDS→DMS verification passes.**
