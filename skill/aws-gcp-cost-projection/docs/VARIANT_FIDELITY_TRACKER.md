# Variant-fidelity remaining work — WORKING TRACKER (uncommitted scratch)

Purpose: the running list of services whose **specific billing variants** are still collapsed or
mis-targeted — so we only model what's actually broken, not the already-GOLD services.
Source of truth: `docs/AUDIT_charge_type_variant_fidelity.md` (2026-11-12 audit) + §5 PARKED in
`docs/WORKORDER_per_service_full_modelling.md`. This tracker reconciles that audit against the work
done in this session's §4.x passes. **Update the STATUS column as each is modelled/verified.**

Legend: 🔴 still broken · 🟡 verify (may be partly done) · ✅ done this session · ⏸ deferred · 🥇 GOLD (don't touch)

---

## STILL-OPEN variant gaps (the real targets — pick from here)

| # | Service | The defect | Evidence (file:line) | Fix direction | Status |
|---|---|---|---|---|---|
| M14 | **DynamoDB** | ~~Throughput→misc/LLM; multi-region nam5 ($0.60M) not single-region Iowa ($0.30M) = 2× over; provisioned passthrough side bug.~~ **FIXED §4.18 (committed 6517302):** deterministic Firestore (route→dynamodb_capacity); single-region city SKU (Iowa $0.30M) via region→city map; Global Tables→nam5; side bug fixed (assumption defaults were 0.0→1.0). Both pages verified; 2× bug caught by official-page cross-check. | — | ✅ DONE §4.18 (full suite 1674) | ✅ |
| M11 | ~~Kinesis / SNS / SQS → Pub/Sub~~ | **SETTLED — correctly handled (both AWS+GCP pages verified 2026-11-18):** Pub/Sub = $40/TiB (data volume, 1KB-min/msg); SNS = $0.50/M req + per-delivery; SQS = $0.40/M. SNS/SQS request-COUNT → Pub/Sub $/TiB is NOT reliably mappable (a request = ≤64KB chunk → ~65× byte range, no size in CUR), so mapper forces honest passthrough (`:3757`). Kinesis *volume* (GB) already maps to Pub/Sub $/TiB cleanly. Per user ("map if possible else move on") → passthrough stays. | `:3757` | ✅ handled (passthrough, verified) | ✅ |
| M12 | ~~**EMR Serverless**~~ | **MODELLED §4.19 → READY FOR AGY (both pages verified 2026-11-18).** Was: vCPU-h/Memory-GB-h/Storage → Dataproc passthrough sku=None (unpriced). Now: deterministic DCU FORMULA — vCPU-h → Dataproc Serverless DCU **×600**, Memory-GB-h → DCU **×100** (0.6 DCU/vCPU + 0.1 DCU/GiB additive, ×1000 for per-milli-DCU SKU; premium exact catalog match proves ×1000). Storage → passthrough (shuffle SKU is GiB-mo vs AWS GiB-h, immaterial). No multi-region trap. Standard DCU only. ≤8 GiB/vCPU memory-tier assumption flagged. See [[reference_gcp_dataproc_serverless_rates]]. | `map_emr` §4.19.3 | DONE §4.19 — AGY implemented, Claude-verified (1692 tests; empirical: vCPU-h→$0.036, mem-GB-h→$0.006, storage→passthrough, cluster path not regressed). | ✅ |
| M13 | ~~**MWAA**~~ | **MODELLED §4.20 → READY FOR AGY (both pages verified 2026-11-18 via in-app browser).** Was: all rows → Composer passthrough sku=None (unpriced). Now: deterministic break-down to **Composer Gen 2** — class (mw1.micro…2xlarge) → per-component vCPU/RAM (AWS docs) → Compute mCPUs ($0.045/vCPU-h ×1000) + Compute Memory ($0.005/GiB-h); Environment row adds size-tier Environment Fee ($0.35/$0.55/$0.85); meta-DB → Database Storage ($0.17/GiB-mo). DB vCPU NOT mapped (env fee covers Cloud SQL). Gen 3 DCU rejected (no published vCPU→DCU coeff). See [[reference_gcp_composer_mwaa_rates]]. | `map_mwaa` §4.20.3 | DONE §4.20 — AGY implemented, Claude-verified (1711 tests; empirical mw1.large env $1.62/hr exact; fixed invalid passthrough_reason unmodeled_charge_type→service_policy_review). Committed 48e1f98. | ✅ |
| M10 | ~~**ElastiCache cluster-mode & CUD**~~ | **REFRAMED + MODELLED §4.21 → READY FOR AGY (both pages verified via bxw 2026-11-18).** Cluster-mode is NOT CUR-detectable (sharded/non-sharded both bill NodeUsage); CUD already handled globally (Memorystore CUD-eligible, apply_rates synthesis). **Real defect:** §4.15 Redis node path → legacy per-GiBy.h Memorystore for Redis over-projects **2–3×** vs node-based **Redis Cluster** (13 GiB HA $0.60→$0.19/hr). Fix: retarget Redis node-hr → cheapest Redis Cluster node whose capacity ≥ RAM, ×1.0 (replicas are separate rows, no HA ×2). Leave Serverless/Backup/Memcached/DAX/MemoryDB untouched. See [[reference_gcp_memorystore_rates]]. | `map_elasticache` §4.21.3 | DONE §4.21 — AGY implemented, Claude-verified (1715 tests; empirical 13 GiB→$0.19, 26→$0.57, Valkey labeled, Memcached not regressed). Committed 90695ca. | ✅ |
| M3 | ~~**Lambda provisioned concurrency**~~ | **VERIFIED OK 2026-11-18 (§4.7 covered it):** prov-concurrency → `map_lambda` is_pc branch → Cloud Run **Min-Instance Memory + Min-Instance CPU** SKUs (break_down, both `map`, SKUs resolve). Empirically confirmed. | `map_lambda:8299` | none — correct | ✅ |
| M4 | ~~**Lambda ephemeral storage**~~ | **VERIFIED OK 2026-11-18 (§4.7 covered it):** ephemeral → `map_lambda` is_ephemeral branch → `strategy="ignore"`, mult 0.0 (Cloud Run folds /tmp into memory). Empirically confirmed. | `map_lambda:8281` | none — correct | ✅ |
| H1 | ~~**RDS SQL Server / Oracle / DocumentDB**~~ | **VERIFIED OK 2026-11-18 — better than feared (NONE fall to MySQL):** empirical `map_managed_db` routing — sqlserver→**Cloud SQL for SQL Server**, oracle→**Oracle Database@Google Cloud (ECPU)**, docdb→**Firestore (MongoDB compatibility)**, mysql→MySQL, postgresql→PostgreSQL, mariadb→MySQL (correct substitution). All `map`, no MySQL mis-target. | `map_db_row`/`map_managed_db` | none — correct | ✅ |

## DONE this session (don't redo)

- ✅ **H2 SageMaker** → §4.2 · **H3 Backup** → §4.3 · **H4 MemoryDB** → §4.4 · **H5 DMS** → §4.6
- ✅ **M1 egress rate tiering** → §4.11 · **M2 TGW→NCC (+ data billable)** → §4.14 · **M5 DirectConnect speed tiers** → §4.16
- ✅ **M6 Redshift Spectrum→BQ $/TB** → §4.8 · **M7 Route 53 health checks** → §4.13 · **M8 EFS tiers** → §4.9
- ✅ **H1 RDS core** → §4.1 (engine else-branch fix = verify, see 🟡 above) · **ElastiCache core + Memcached dual-leg** → §4.15 (cluster-mode still open = M10)
- ✅ **ELB** → §4.12 · **Route 53** → §4.13 · **VPC/NAT** → §4.14 · **Lambda core** → §4.7 (prov-conc/ephemeral = verify, M3/M4)

## Deferred

- ⏸ **M9 FSx sub-tiers** → §4.10 — **RATES VERIFIED 2026-11-19 via bxw** ([[reference_gcp_fsx_netapp_parallelstore_rates]]): NetApp Volumes (ONTAP) Standard/Premium/Extreme $0.20/$0.294/$0.393 GiB-mo + Flex Regional (capacity+throughput+IOPS); Parallelstore (Lustre analogue) ~$0.14/GiB-mo provisioned. Dollar-safe interim still in place; these rates enable the real sub-tier completion in a future §4.10 pass.
- ⏸ **CloudWatch §4.17** — PARKED: audit tiers it 🥇 GOLD (dashboards/alarms/API/metrics IGNORE splits already correct). Only a test file + 2 tiny gaps (Synthetics, Logs Insights) remain — low value.
- ⏸ **MSK** — **RATES VERIFIED 2026-11-19 via bxw** ([[reference_gcp_managed_kafka_rates]]): GCP Managed Service for Apache Kafka is DCU-priced ($0.09/DCU-h, DCU=0.6×vCPU+0.1×GiB; storage $0.17/$0.10 GiB-mo; inter-zone $0.01/GiB). **Catalog still MISSING these SKUs** → a future MSK retarget (broker-hrs→DCU) needs synthetic rate injection; self-hosted-GCE proxy confirmed adequate meanwhile (the page confirms Managed Kafka runs on N2 CE vCPU+RAM). No longer blocked on page access.

## 🥇 GOLD — already variant-correct, DO NOT touch (would only regress)

EC2 · **Fargate** (4-class GKE Autopilot sweep + Cloud Run compare) · **EBS** (full volume/IOPS/throughput/snapshot fanout) ·
**S3** (all storage classes incl. 4 Intelligent-Tiering sub-tiers + cold-class region fallback) · **CloudWatch** (free-tier IGNORE splits) ·
**KMS** (sw/HSM/EKM/requests discriminated) · **WAF** (multi-component formula) · **Athena** · **Secrets Manager** · **xray**

## LOW — cosmetic / dollar-safe / rare (skip unless touched incidentally)

EBS sc1→Standard PD · S3 requests/retrieval passthrough · EFS Intelligent-Tiering · CloudTrail free mgmt-events not *declared* IGNORE ·
EKS extended-support · GWLB (done §4.12 as passthrough) · API Gateway REST/HTTP/WS collapse (dollar-safe) · Step Functions deterministic
Workflows SKU unused · SSM free std params · GlobalAccelerator DT-premium.

## SYSTEMIC bug found 2026-11-18 (audit other services)

- 🔴 **`resolve_sku` mis-prefers MULTI-REGION over single-region.** For a service with both single-region (e.g.
  `…Iowa`) and multi-region (e.g. `North America 5`/nam5) region-named SKUs, `resolve_sku(svc, generic_pattern, region)`
  returns the **multi-region** SKU → ~2× over-projection. Caught in DynamoDB→Firestore §4.18 (nam5 $0.60/M vs Iowa
  $0.30/M) only via official-page verification. Targeted fix = region-city pattern. **Audit every service that resolves
  region-named SKUs** (Firestore, Spanner, Bigtable, any with multi-region variants) for the same trap. See
  [[reference_gcp_firestore_rates]].

## SYSTEMIC fixes (do alongside service work, per the audit §3)

1. **Populate `service_usage_catalog.json` per service** → the completeness gate then protects every variant (was dynamodb-only; this
   session has been adding dims per service — keep it up). Highest-leverage.
2. **`gcp_candidates` best-fit** only exists for ElastiCache — extend to DynamoDB (Firestore/Bigtable/Spanner), RDS (Cloud SQL/AlloyDB),
   Redshift (BQ OD/Editions), FSx (Filestore/NetApp).
3. **Kill `else`-branch `strategy=map` on a guessed target** (the H1/H3 anti-pattern — latent silent mis-targets).
4. Keep rate granularity ≥ direction granularity (egress proven; apply elsewhere).

---
*Working file — not committed. Update STATUS as each variant is modelled/verified.*
