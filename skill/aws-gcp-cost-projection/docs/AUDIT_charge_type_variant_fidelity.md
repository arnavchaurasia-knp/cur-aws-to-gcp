# AUDIT — Charge-type / feature-variant mapping fidelity (all services)

Status: **AUDIT COMPLETE (2026-11-12).** Read-only pass; no code changed. Produced by a 5-cluster
parallel audit (storage / databases+cache / compute+containers / networking / analytics+ops+security),
each reading `data/service_specs.json` + the mapper functions in `scripts/apply_static_mappings.py`
against each service's real AWS billing-dimension universe.

Companion to `WORKORDER_best_fit_target_selector.md` (which product to pick among candidates) and
`WORKORDER_spec_partition_completeness.md` (prove the charge-type partition is complete). This audit
answers a different question: **within each service, are the distinct billing variants (S3 storage
classes, EBS volume types, RDS engines, Lambda dimensions, egress destinations…) actually
distinguished and each routed to the _correct_ GCP equivalent — or collapsed to one rate/target?**

All rate-sensitive claims are marked `[verify rate]` — per AGENTS.md they must be confirmed against the
provider's official pricing page before any rate is pinned in code. This audit identifies *structural*
gaps, not rates.

---

## 0. TL;DR

The answer to "has per-variant fidelity been built for every service?" is **no — it's uneven, and the
unevenness is not random.** Three structural facts explain the whole picture:

1. **The compute path set a high bar and a few services meet it.** `cheapest_in_scope()`
   (`apply_static_mappings.py:1530`) is a real cheapest-safe-best-fit sweep. EC2 (gold), **Fargate**
   (`map_per_request` Fargate block, near-gold — sweeps 4 GKE Autopilot classes *and* compares Cloud
   Run), **MSK** broker-hours, and **EBS** (full volume-type + IOPS + throughput + snapshot-tier
   fanout) all reach genuine variant-level fidelity.

2. **The per-variant _completeness gate_ is populated for exactly one service.**
   `data/service_usage_catalog.json` enumerates **dynamodb only** (13 usage-types). Every other
   service's variant universe is untested by `test_spec_partition` — so a collapsed or missed variant
   rots silently in the `spec_gap`/catch-all path with no alarm. This is the single highest-leverage
   systemic fix.

3. **The deterministic mappers hardcode one GCP target per charge-type.** The `gcp_candidates`
   cross-product best-fit framework exists in `service_specs.json` for **ElastiCache only**. Everywhere
   else, where multiple GCP products legitimately compete (RDS→Cloud SQL/AlloyDB, DynamoDB→
   Firestore/Bigtable/Spanner, Redshift→BQ on-demand/Editions), the target is either hardcoded or
   punted to the LLM without a priced comparison.

**Net:** service-level mapping is essentially complete; charge-type/variant-level fidelity ranges from
gold (EC2/Fargate/EBS/S3/CloudWatch/KMS/WAF) down to single-label passthrough (SageMaker). Your
instinct is correct: the deterministic mappers are exactly where hardcoding hurts, and closing the
remaining variant gaps is the long tedious work that guarantees the best pick.

---

## 1. Quality tiers (how each service actually maps, best → worst)

| Tier | Meaning | Services |
|---|---|---|
| **GOLD** | Real best-fit sweep OR full variant fanout to correct per-variant targets | EC2, Fargate, EBS, S3, CloudWatch, KMS, WAF, Athena, MSK, Secrets Manager, xray |
| **GOOD** | Core dimensions distinguished + correct; minor variant collapsed | Lambda (compute), EKS, ECS, EMR (provisioned), ElastiCache, Route 53 (zones/queries), ELB (ALB/NLB), VPC-NAT, Glue, QuickSight, Shield |
| **PARTIAL** | Right service, but a material variant is collapsed, flattened, or an assumption stands in for real data | EFS (Basic-tier pinned), FSx (no sub-tier), MemoryDB (16 GiB flat), DirectConnect (10G flat), datatransfer (flat egress rate), TGW, Redshift (mostly passthrough) |
| **MIS-TARGET / COLLAPSE** | Variant sent to the wrong GCP product, or every sub-type collapsed to one label | **RDS SQL Server/Oracle→MySQL**, **DocumentDB→MySQL**, **SageMaker→one Vertex passthrough**, **DMS homogeneous (free) over-projected** |
| **PASS (by design)** | No clean GCP equivalent; passthrough-at-AWS-cost is dollar-safe | API Gateway, CloudFront requests, GuardDuty/SecurityHub/Inspector, SES, MQ, AppSync, FSx ONTAP/OpenZFS, S3 requests/retrieval |

---

## 2. Severity-ranked gap register (the actual work)

### HIGH — wrong GCP product or materially wrong dollars

| # | Service | Gap | Evidence | Fix direction |
|---|---|---|---|---|
| H1 | **RDS / DocumentDB** | SQL Server, Oracle, and DocumentDB (MongoDB-compat) all fall to `map_db_row`'s `else` branch and are **priced as Cloud SQL _MySQL_** ("engine could not be determined", conf 0.55) — contradicts `managed-db.md`. | `apply_static_mappings.py:9124-9129` | Explicit engine routing: sqlserver→Cloud SQL SQL Server; oracle→no-equiv/passthrough; docdb→MongoDB-compat target (Atlas marketplace / Firestore w/ disclosure), never relational MySQL. |
| H2 | **SageMaker** | Every sub-service (training / real-time endpoint / batch transform / notebook / Feature Store / Gp3 volume) collapsed to **one "Vertex AI" passthrough**, conf ≤0.4; Gp3 volume also excluded from block_storage so storage dim is dropped. Largest unhandled service by spend (~$90K in the global audit). | spec `sagemaker_all`:2877; `:2882-2918` | Split: training→Vertex Custom Training (machine-type sub-SKUs), real-time→Online Prediction, batch→Batch Prediction, notebook→Workbench, Gp3→block_storage/PD. |
| H3 | **AWS Backup** | Spec block exists but **no dedicated mapper**; vault storage bytes hit the empty-volume-type default in `map_block_storage` and are priced as **Hyperdisk Balanced live-disk capacity**, not snapshot/GCS → over-projection. | `:4005` → `EBS_VOLUME_MAP.get('',DEFAULT)` `:600` | `map_backup` with source-type split: EBS-src→PD Snapshot, object/file→GCS tier by retention, RDS-src→Cloud SQL Backups. |
| H4 | **MemoryDB** | Node RAM flattened to a **flat 16 GiB assumption** (`memorydb_node_gib`, default 16) regardless of real node_type, **and always mapped to Redis _Basic_** though MemoryDB is inherently durable/Multi-AZ. | `:5425`, `:5662-5666` | Read real node_type→RAM; map to Standard HA (durable) tier, not Basic. |
| H5 | **DMS** | Homogeneous migrations are **free on GCP DMS** but all replication-instance-hours passthrough at full AWS cost (over-projection); CDC/heterogeneous is per-GiB (unit mismatch). Spec even documents the free tier but code can't tell them apart. | spec `dms_all`; service_map:334 | Engine-pair signal → homogeneous=IGNORE, CDC=FORMULA $/GiB `[verify rate]`. |

### MED — variant collapsed, flattened, or mispriced (dollar-directional)

| # | Service | Gap | Evidence |
|---|---|---|---|
| M1 | **datatransfer (egress)** | Inter-region and internet egress each use **one flat pinned $/GB** across all region-pairs / destinations / volume-tiers. Direction is detected per-pair; the *rate* is not. CloudFront already proves a tiered rate table works. | `egress_rates.py:33-34`; `apply_static_mappings.py:4322` |
| M2 | **Transit Gateway** | (a) TGW-hours map to **Cloud VPN Tunnel** though the spec says **NCC Hub** (target/spec disagreement); (b) TGW data processed is **$0-ignored** despite being billable — an explicit AGENTS.md red-flag. | `FLAT_HOURLY_MAP:424`; `:4165-4190`; spec:3244 |
| M3 | **Lambda provisioned concurrency** | "Min.Instance" rows explicitly excluded from the mem/cpu match → priced as request-based compute or passthrough, not Cloud Run min-instance/idle billing. | `:6979-6981` |
| M4 | **Lambda ephemeral storage** | Passthrough at AWS cost; on GCP Cloud Run Functions there's no separate ephemeral charge → should be IGNORE/near-zero, not AWS-cost passthrough. | `:7052-7065` |
| M5 | **DirectConnect** | All port-hours priced as a **single 10G** assumption → 1G over-, 100G under-projected. Bandwidth is the one real variant and it's flattened. | `FLAT_HOURLY_MAP:453-454` |
| M6 | **Redshift Spectrum** | Per-TB-scanned has no rule → falls to unrecognized→passthrough. Should mirror Athena→BigQuery $/TB. | `:6033-6045` |
| M7 | **Route 53 health checks** | Per-month health-check fee mispriced against the **per-query** DNS SKU via the catch-all pattern. | `map_per_request:2962`; PER_REQUEST_MAP:547 |
| M8 | **EFS tiers** | Mapper hard-pins Filestore **Basic** (SSD/HDD); Zonal/Regional/Enterprise never selected. The $0-throughput and $0-data-access decisions are only correct *because* Basic is pinned. | `map_efs:6656`; `:6759-6780` |
| M9 | **FSx sub-tiers** | One flat target per filesystem type; Lustre/Windows SSD-vs-HDD, throughput-capacity, provisioned IOPS all folded into capacity. | `map_fsx:6803` |
| M10 | **ElastiCache cluster/CUD** | Redis **cluster-mode (sharded)** not detected (no Memorystore Redis Cluster target); committed/reserved nodes priced at OnDemand (no CUD). | `map_elasticache:5610-5640` |
| M11 | **Kinesis / SNS / SQS → Pub/Sub** | Request/payload-unit **counts** mapped to Pub/Sub "Message Delivery" at mult=1.0, but Pub/Sub throughput is $/TiB. If the SKU is $/TiB this is a unit error. | PER_REQUEST_MAP:510-512; `:6605` |
| M12 | **EMR Serverless** | Direct vCPU-h/GB-h (no backing EC2 row) routes to `emr_all` → only the mgmt premium is priced → under-projection. | `map_emr:7117` |
| M13 | **MWAA** | Correct product (Composer) identified but left as **unpriced passthrough** — no env-class→Composer-size sizing. | `map_mwaa:4813` |
| M14 | **DynamoDB throughput target** | Firestore-vs-Bigtable-vs-Spanner is **LLM-only**; the deterministic `map_dynamodb_capacity` (always Firestore) is **orphaned/dead** — no charge_type emits its mechanic. | `:4736`; dispatch:9302 |

### LOW — cosmetic, dollar-safe, or rare

EBS `sc1`→Standard PD (no colder GCP HDD); S3 requests/retrieval passthrough (unit mismatch, by
design); EFS Intelligent-Tiering not truly separated (falls to SSD default); CloudTrail free
mgmt-events not *declared* IGNORE (already $0); EKS extended-support surcharge collapsed into base fee;
Gateway Load Balancer → Classic/passthrough; API Gateway REST/HTTP/WebSocket collapsed (passthrough is
dollar-safe); Step Functions transitions route to LLM though a deterministic Workflows SKU entry exists
unused (PER_REQUEST_MAP:528); SSM standard params free-on-GCP not modeled; globalaccelerator DT-premium
→ generic egress (no GCP equivalent).

---

## 3. Systemic root causes (fix these, not just the symptoms)

1. **Completeness gate covers 1/55 services.** `service_usage_catalog.json` = dynamodb only. Until each
   multi-variant service's usage-type universe is enumerated into that fixture (the
   `WORKORDER_spec_partition_completeness.md` method), collapsed/missed variants have no CI alarm. **This
   is the highest-leverage fix** — it converts every HIGH/MED above into a test that stays fixed.

2. **No `gcp_candidates` for services where GCP products compete.** The cross-product best-fit framework
   is built for ElastiCache only. RDS (Cloud SQL editions / AlloyDB), DynamoDB (Firestore/Bigtable/
   Spanner), Redshift (BQ on-demand/Editions), FSx (Filestore/NetApp) all need it — see
   `WORKORDER_best_fit_target_selector.md` §4 (the two work-orders are complementary: partition decides
   the *mechanic*, selector decides the *target*).

3. **`else`-branch defaults that silently mis-target.** The RDS/DocumentDB→MySQL default (H1) and the
   Backup→Hyperdisk-capacity default (H3) are the same anti-pattern: an unmatched variant lands on a
   confident wrong target instead of an explicit passthrough/review. Any `else` that emits `strategy=map`
   with a hardcoded target is a latent H1/H3.

4. **Rate granularity lags direction granularity (egress).** The pipeline correctly *labels* egress by
   region-pair/destination but prices it flat (M1). Tiered rate tables are proven feasible
   (CloudFront, NAT) — extend them to inter-region/internet egress.

---

## 4. Recommended order (materiality × tractability)

1. **H1 RDS/DocumentDB engine routing** — mechanical, high $, removes a silently-wrong target. (Add
   sqlserver/oracle/docdb branches; kill the MySQL `else` default.)
2. **H3 AWS Backup `map_backup`** + **H4 MemoryDB node sizing** — both are "stop using a wrong default."
3. **Populate `service_usage_catalog.json`** for the high-$ multi-variant services (rds, s3, ebs, efs,
   lambda, redshift, cloudwatch, elasticache) + extend the completeness gate. Locks in every fix above.
4. **H2 SageMaker sub-type split** + **H5 DMS homogeneous/CDC split** — need a new signal (sub-type /
   engine-pair) before they can price correctly.
5. **M1 egress rate tiering** + **M2 TGW** (target fix + stop the $0-on-billed-charge).
6. The remaining MED rows as capacity allows; LOW rows only if touched incidentally.

Every rate pinned from a page cites its official URL in a comment (AGENTS.md). Never project $0 on a
charge the provider bills (M2 TGW-data is a live instance of that rule).

---

## Appendix — what's already GOLD (don't regress these)

- **S3**: full storage-class fanout incl. all 4 Intelligent-Tiering sub-tiers, correct GCS class per
  class (Glacier→Coldline, Deep Archive→Archive), **plus cold-class region fallback**
  (`resolve_sku_with_fallback`, `_GCS_CLASS_FALLBACKS:2205`).
- **EBS**: gp2/gp3/io1/io2/Block-Express + provisioned IOPS + throughput + snapshot-standard-vs-archive,
  per-region Extreme-SKU-generation selection. The two historically-flagged bugs (gp3-IOPS,
  io2-IOPS ignored) are **fixed**.
- **Fargate**: 4-class GKE Autopilot sweep + Cloud Run comparison + self-host disclosure — the model the
  CAND services should copy.
- **CloudWatch**: correct free-tier IGNORE splits (dashboards/alarms/API/DB-Insights), unit-aware,
  PDF-bill-aware.
- **KMS**: software / HSM / external-key-store / requests all discriminated with anchored regex.
- **WAF**: real multi-component FORMULA (policy + rule + requests + enterprise), not collapsed.
