# Repo-Wide Assumptions Audit — AWS→GCP Cost Projection

_Generated 2026-10-14. A full sweep of every baked-in assumption in the projection pipeline — the kind of thing the Aurora "1 ACU = 2 GiB + 0.25 vCPU" conversion is one example of. Each entry: what AWS thing maps to what GCP thing, the implicit claim, the type, and whether it is fragile/disclosed._

Legend for **Type**: `CONST` hardcoded number · `FORMULA` derived conversion · `DEFAULT` fallback when data absent · `PASSTHROUGH` AWS cost carried 1:1 as GCP estimate · `NO-EQUIV` "no GCP equivalent" substitution/drop · `GATE` validation threshold · `ROUTING` regex/substring identity assumption · `NETTING` commitment/discount accounting choice.

---

## 0. The two most load-bearing (and most quoted) assumptions

| # | Assumption | Location | Status |
|---|---|---|---|
| **A** | **1 Aurora ACU = 2.0 GiB RAM + 0.25 vCPU** → Cloud SQL unbundled vCPU+RAM (`unit_multiplier` 0.25 core / 2.0 RAM) | `apply_static_mappings.py:4780, 4967, 4981, 5017, 5031` | 2.0 GiB is AWS-documented; **0.25 vCPU is explicitly un-sourced** (anchored to R-class 8:1, "assumes N4"; GP custom actually needs ~0.31). conf 0.65, `is_assumption=True` |
| **B** | **Inferentia/Trainium chip → Cloud TPU v5e chip at 1:1**, price-only, no benchmark exists | `apply_static_mappings.py:8145-8215`, `tpu-rates.json`, `accelerator-chips.json` | Self-flagged "PRICE assumption only, NOT performance equivalence." conf 0.3. **Conflicts** with 3 other treatments (see §10) |

---

## 1. Global unit constants (pervasive magic numbers)

| Assumption | Location | Type | Note |
|---|---|---|---|
| **730 hours = 1 month** (local SSD GiB↔mo, NAT gw count, WAF/Armor flat fees) | `apply_static_mappings.py:8053, 2407, 2457, 2491` | CONST | Baked in repeatedly |
| **1 GiB = 1.074 GB** (`GB_TO_GIB = 1/1.074`) — every byte-denominated conversion | `apply_static_mappings.py:49` | CONST | True factor is 1.073741824; 1.074 is a rounding |
| **TB = 1000 GB** (not 1024) in EC2 storage parse | `refresh_ec2_specs.py:75-96` | CONST | |
| Athena blank/unknown `pricing_unit` → **assume decimal TB** | `apply_static_mappings.py:6119-6121` | DEFAULT | Silently guesses the unit |
| Blank unit/usage_type → **assume the common case** (hourly for compute, decimal-TB for Athena, etc.); `_HOURLY_UNITS` accepts `""` | `classify_mechanics.py:109`; many mappers | DEFAULT | Load-bearing for PDF/flat-CSV bills; mis-gates genuine non-hourly blank rows |

---

## 2. Compute family mapping (AWS family → GCP family)

| AWS → GCP | Location | Type | Implicit claim / risk |
|---|---|---|---|
| **Memory-optimized `r` family → N4 general-purpose** (intel), N4D (amd) — *not* an M-series | `family_map.json:35-49` | CONST | r-family RAM ratio ≈ general N4; may under-provision RAM |
| **Storage `i`/`d`/`h` family → N4 general-purpose** — *not* Z3 | `family_map.json` | CONST | No storage-optimized target used |
| **Graviton/ARM → C4A at 1:1 vCPU** | `family_map.json`, `validate_fix.py:143` | CONST | ARM core ≈ C4A core parity |
| general→N4/N4D, compute→C4/C4D, burstable→E2 | `family_map.json:24-50` | CONST | One canonical GCP family per AWS family |
| **4th-gen preferred unconditionally** regardless of AWS source generation | `family_map.json`, `render_report.py:1698` | CONST | Ignores source-gen; `_GP_FAMILY_GENERATION` floor forbids older-gen substitution |
| Catch-all unknown family → **N4D** | `validate_fix.py:250` | DEFAULT | |
| Burstable t-family → E2 and **burst credits ignored** ("assumes steady-state CPU") | `apply_static_mappings.py:8001`, `service_specs` | CONST | E2 has no burst-credit model |
| CPU-credit charges → **ignore / $0** | `service_map.json`, `service_specs ec2` | NO-EQUIV | Hides a real recurring AWS line |
| Default specs when bill lacks them: **vcpus or 2, ram or 8.0** (GCE); **8 / 32** (GPU rows) | `apply_static_mappings.py:7918, 8223` | DEFAULT | |
| `arch`: suffix `g`→ARM, `a`→AMD, else Intel; non-Graviton processor ⇒ x86_64 | `family_map.json:17-22`, `refresh_ec2_specs.py:59` | ROUTING | No ARM-non-Graviton recognized |
| EFA/HPC network-tier forced for `{hpc*, c5n, m5n, m5dn, r5n, r5dn}` | `apply_static_mappings.py:7932` | CONST | |

---

## 3. GPU / accelerator sizing

| AWS → GCP | Location | Type | Note |
|---|---|---|---|
| G-family ≤gen4 → **T4**, ≥gen5 → **L4**; P-family ≤gen4 → **A100**, ≥gen5 → **H100** | `family_map.json:52-95` | CONST | Silicon-equivalence judgment calls |
| **GPU count inferred from vCPU**: `gpu_per_vcpu` 0.25 (G), 0.125 (P), vCPU breakpoints, or fixed 8 | `family_map.json:52-95` | FORMULA | CUR has **no GPU-count column**; these are estimates |
| `A10G → L4`, `T4G → T4` aliases | `apply_static_mappings.py:8119` | CONST | Note: "different generation/performance tier, verify" |
| **`{K80, GAUDI, HL-205, VU9P, V520, H200}` + all non-Nvidia → no GCP equivalent; accelerator cost dropped entirely**, priced as plain compute | `apply_static_mappings.py:8117-8122` | NO-EQUIV | Accelerator spend silently vanishes; conf 0.40 |
| GPU VRAM fallback `{T4:16, L4:24, P100:16, P4:8, V100:16, A100:40}` (A100=40 not 80) | `apply_static_mappings.py:1569` | DEFAULT | |
| GPU class rank `V100≈L4` (both 3) for equal-or-better substitution | `apply_static_mappings.py:1572` | CONST | |
| GPU model → fixed host family (T4→N1, L4→G2, A100→A2, H100→A3) | `apply_static_mappings.py:1565` | CONST | |
| ARM-hosted GPU → host silently becomes x86 ("GCP has no ARM GPU host") | `apply_static_mappings.py:8275` | NO-EQUIV | |
| `gcp-model-config.json` separately maps **Inferentia→G2/L4, Trainium→A2/A100, Trainium2→A3/H100** | `gcp-model-config.json` | CONST | **Conflicts** with TPU-v5e path (§10) |

---

## 4. Databases (RDS / Aurora / cache / NoSQL)

| Assumption | Location | Type | Note |
|---|---|---|---|
| **Aurora ACU = 2 GiB + 0.25 vCPU** | see §0-A | FORMULA | Headline assumption |
| **MariaDB priced as MySQL**; **unknown engine → MySQL** | `apply_static_mappings.py:5002, 8398` | NO-EQUIV / DEFAULT | Cloud SQL has no MariaDB |
| ARM/Graviton RDS → **forced x86 Cloud SQL** | `apply_static_mappings.py:8368, 8368` | NO-EQUIV | Cloud SQL has no ARM machine type |
| RDS MySQL 8.0 Extended Support → **$0 until 2027-01-01**; MySQL CE EOL surcharge → ignore/$0 | `apply_static_mappings.py:4830`, `service_map` | CONST/DATE | Date-fragile; may understate |
| gp3 provisioned IOPS → **ignored** ("bundled in pd-balanced") | `service_specs rds` | NO-EQUIV | **io2 IOPS may be wrongly ignored too** (pd-extreme DOES bill IOPS; "not yet split") |
| **ElastiCache node-hour → Memorystore by node RAM** (Redis) / vCPU+RAM (Memcached) | `apply_static_mappings.py:5169` | FORMULA | |
| Memorystore tier ceilings `M1=4…M5=300 GiB`; absent deployment ⇒ **assume Basic (non-HA)** | `apply_static_mappings.py:5109, 5253` | CONST/DEFAULT | HA would cost ~1.3–1.9×; conf capped 0.80 |
| MemoryDB / DAX → Memorystore for Redis (was misrouting to Cloud SQL) | `service_map.json` | CONST | |
| **DynamoDB target inconsistent**: Firestore vs Bigtable vs Spanner across files | `service_map` / `aws-to-gcp` / `service-classification` | — | See §10 |
| **1 RCU ≈ 1 doc read, 1 WCU ≈ 1 doc write**, conf ≤0.5 | `service_specs dynamodb`, `classify_mechanics.py:1372` | FORMULA | RCU/WCU are 4KB/1KB-sized, not in CUR |

---

## 5. Analytics / streaming

| Assumption | Location | Type | Note |
|---|---|---|---|
| **Redshift node → BQ slots** (`dc2.large:500 … ra3.16xlarge:6000`) | `apply_static_mappings.py:5367` | CONST | Self-declared "unsourced," overstates 15–60×, 2× internally inconsistent — **now forced passthrough**, numbers survive only in notes |
| **Redshift Serverless 1 RPU ≈ 128 BQ slots** | `apply_static_mappings.py:5483` | CONST | Disclaimed ~32× overstatement; now passthrough |
| RA3 managed storage ↔ BQ active storage 1:1 GB-mo | `apply_static_mappings.py:5454` | PASSTHROUGH | |
| **EMR surcharge → Dataproc $0.01/vCPU-hr** | `apply_static_mappings.py:6573` | FORMULA | |
| **1 Glue DPU = 4 vCPU + 16 GiB → Dataflow**, assumes **Batch not Streaming** | `apply_static_mappings.py:6512` | FORMULA | Streaming ~23% higher; CUR can't distinguish |
| **MSK broker-hour → self-hosted GCE** core+RAM table; **1 MCU = 1 vCPU + 4 GiB** | `apply_static_mappings.py:6647, 6671` | CONST/FORMULA | Replication/overhead not captured; conf cap 0.70 |
| Kinesis shard-hour ≈ 3.6 GB/hr throughput (documented, **not applied** → passthrough) | `apply_static_mappings.py:6166` | PASSTHROUGH | |
| Athena decimal-TB → BQ binary-TiB (×0.9095) | `apply_static_mappings.py:6087` | FORMULA | |

---

## 6. Storage / networking / egress

| Assumption | Location | Type | Note |
|---|---|---|---|
| EBS gp2→Balanced PD, gp3→Hyperdisk Balanced, io1/io2→Extreme PD, st1/sc1→Standard PD; default **Hyperdisk Balanced** | `apply_static_mappings.py:489` | CONST/DEFAULT | |
| Hyperdisk Balanced caps 160000 IOPS / 2400 MiBps; **$0.006/IOPS-mo parity** | `apply_static_mappings.py:3504` | CONST | |
| EFS Standard → Filestore Basic SSD; IA/Intelligent → Basic HDD | `apply_static_mappings.py:6199` | CONST | "closer performance profile" judgment |
| **Unmatched transfer row → assume internet egress** (most expensive class) | `apply_static_mappings.py:575` | DEFAULT | |
| Pinned GCP egress rates: interzone $0.01, inter-region $0.08, internet $0.12/GB + CDN tiers | `egress-rates.json`, `apply_rates.py:108` | CONST | Self-flagged "verify/update" |
| **1 LCU-hr ≈ 1 GB** for ALB/NLB data processing ($0.008/GiB) | `apply_rates.py:859` | FORMULA | Over-estimates connection-heavy workloads |
| **NAT gateway → 16 VMs** fan-out default (per-VM-hour billing) | `apply_static_mappings.py:2408` | DEFAULT | Overridden if bill-derivable |
| Public IPv4 → External IP "exact per-IP-hour parity"; EIP/VPN/EKS/DirectConnect/ELB hrs → flat_hourly | `service_specs` | CONST | |
| Client VPN explicitly **not** Cloud VPN → IAP/BeyondCorp review | `service_map.json` | NO-EQUIV | |
| Fargate vCPU-Hr → Autopilot mCPU (×1000) / GB-Hr → GiBy.h; cheaper of Autopilot vs Cloud Run auto-picked | `apply_static_mappings.py:2810` | FORMULA | Latent 1000× Arm-CPU unit bug excluded by hand |
| GCS class fallback chain Archive→Coldline→Nearline→Standard if SKU missing | `apply_static_mappings.py:2108` | DEFAULT | |

---

## 7. Monitoring / security / misc service fees

| Assumption | Location | Type | Note |
|---|---|---|---|
| CloudWatch metrics <1000 metric-months → **$0 ignore** (free tier absorbs); above → 0.33417 MiB/metric/mo at **assumed 1-min resolution** | `apply_static_mappings.py:5835` | GATE/FORMULA | |
| CloudWatch Dashboards → $0; Database Insights → $0 | `apply_static_mappings.py:5899` | CONST | |
| CloudWatch Logs ingest ×0.9313 (GCP ~7% cheaper, $0.50/GiB, 50 GiB free) | `apply_static_mappings.py:5945` | FORMULA | |
| CloudTrail events → Cloud Logging **passthrough** (event→GB "no reliable basis") | `service_map.json` | PASSTHROUGH | |
| Shield Advanced **$3,000/mo → Cloud Armor Enterprise** parity | `apply_static_mappings.py:4366` | CONST | |
| QuickSight Author → **Looker Studio Pro $9/user/mo**; CUR qty = seat-months | `apply_static_mappings.py:4626` | CONST | conf 0.80 |
| Cognito **MAU ≈ total_usage ÷ 20** (20 auth events/user/mo) | `classify_mechanics.py:1358` | FORMULA | CUR bills requests not MAU; conf 0.45 |
| Parity rate claims: EKS↔GKE $0.10/hr, KMS $1/key, Secrets $0.40→$0.06/version, Step Functions $0.025→$0.01/1k, EventBridge→Eventarc $0.60/M, ECR→Artifact Registry $0.10/GB, Amazon Q↔Gemini $19 | `service_specs`, `misc_service_context.json` | CONST | Published-price snapshots that drift |
| Honest **passthrough-at-AWS-cost** (no defensible conversion): MWAA, SSM Param, Inspector, GuardDuty, Marketplace, Neptune, DocumentDB, SES, Transfer Family, API Gateway, Bedrock→Vertex, Firehose→Dataflow, Detective/SecurityHub→SCC, DMS | `apply_static_mappings.py`, `service_map.json` | PASSTHROUGH | Treats AWS cost as 1:1 GCP placeholder |
| OpenSearch/Elasticsearch, MSK, Amazon MQ → **self-host on GCE** | `service_map.json`, `llm_guidance.py:29` | NO-EQUIV | Sizing assumes footprint = real cluster; conf 0.70–0.72 |
| RHEL license by vCPU band (≤8 / 9-127 / ≥128); Windows "License Included"=Standard 2019 proxy ($0.046/h); SQL Server by edition + 1-4/>4 vCPU band; BYOL never silently assumed | `apply_static_mappings.py:7555, 7564, 7870` | CONST | Only RHEL/Windows/SQL handled; other OS → no license entry |

---

## 8. Discounts, CUD & commitment netting (directional bias)

| Assumption | Location | Type | Note |
|---|---|---|---|
| **CUD multipliers**: CE 0.63/0.45, Mem-Opt 0.59/0.30, Cloud SQL 0.75/0.48, Spanner/Bigtable/AlloyDB 0.75/0.60, Memorystore 0.75/0.48, Cloud Run 0.83/0.67, **DEFAULT 0.75/0.60** | `cud_pct.json`, `apply_rates.py:14`, `validate_fix.py:123` | CONST/NETTING | Blanket default fabricates discounts for unlisted services; self-flagged "verify annually" |
| **Synthesized CUD rows = OnDemand × multiplier** when catalog lacks real Commit rows | `apply_rates.py:722`, `incremental_rerate.py:151` | FORMULA | Price fabrication |
| CUD eligibility restricted to **Compute Engine CPU/RAM/GPU + a fixed service whitelist** | `validate_fix.py:714, 1042` | NETTING | Hyperdisk/egress/IP/snapshots excluded |
| **EDP / negotiated AWS discounts dropped → GCP compared at list** | `service_map.json`, `render_report.py:1740` | NETTING | Systematic bias: makes GCP look relatively more expensive (deliberate "list vs gross-list" framing) |
| **Spot → OnDemand rate under commitment** (no CUD for Spot) | `projection_view.py:169` | NETTING | AWS Spot may be cheaper → GCP appears higher |
| Missing regional rate → **price at cheapest available region** ("never over-project") | `projection_view.py:62` | DEFAULT | Systematically **biases GCP downward** |
| CUD-column totals **fall back to OnDemand** when SKU has no CUD rate | `render_report.py:744`, `export_report.py:34` | DEFAULT | |

### ⚠️ The central netting conflict (two shipped, opposite rules)

| File | Rule | |
|---|---|---|
| `apply_commitment_ignores.py:6-9` | RI/SP/EDP fees → **ignore** (conf 1.0), "already in effective rates, mapping double-counts" | NETTING |
| `render_report.py:700-715` | commitment_discount **included** in grand total — "does NOT hold for PDF bills"; excluding it under-reported a $15,721 bill as $10,663 | NETTING |

Both coexist; behavior depends on ingest path. `ensure_full_coverage.py:54` and `classify_mechanics.py:178` side with "ignore" — but only when the bill has ≥1 DiscountedUsage row (documented $60K failure when that guard is wrong).

---

## 9. Validation gate thresholds (hardcoded cutoffs)

| Gate | Value | Location | Note |
|---|---|---|---|
| Outlier hard-clamp | ratio **>50×** or **>$10,000 on <$100** AWS → passthrough | `validate_fix.py`, `render_report.py:1702` | |
| Storage over-projection | GCP **>2×** AWS flagged (rows >$20) | `validate_fix.py:1004` | |
| Egress over-projection | GCP **>3×** AWS flagged | `validate_fix.py:1021` | "GCP egress genuinely ~2×" |
| Passthrough budget | **>10%** of workload spend fails | `validate_fix.py:420` | **Comment says 5% — doc/code mismatch** |
| Under-projection | AWS **>$10** & GCP==0 fails | `validate_fix.py:976` | $10 free-tier materiality floor |
| Phantom-zero autofix | AWS **≤$1** & GCP **>$10** → ignore | `validate_fix.py:792` | |
| Map-with-no-rate downgrade | AWS **>$5** & no GCP rate → passthrough | `validate_fix.py:836` | |
| Capacity grace | GCP vCPU/RAM must be ≥ AWS − **0.5 units** | `validate_fix.py:1106` | "GCP capacity ≥ AWS" hard requirement; ignores legit right-sizing |
| Reconciliation tol | max($1, **0.5%** of bill) | `validate_fix.py:1096` | |
| Per-N unit multiplier | clamped to **≤1.0** | `validate_fix.py:508` | Legit >1 multipliers silently clamped |
| misc-spend warn | **>15%** | `classify_mechanics.py:1654` | |
| max unit multiplier | vCPU 512, RAM 8192 GB | `review-config.json` | |
| Confidence caps | scattered 0.2/0.3/0.4/0.5/0.6/1.0 with no single source of truth | `llm_guidance.py`, `merge_mappings.py`, `service_classifier.py:155` | |

---

## 10. Cross-file inconsistencies (same thing, different answer)

1. **DynamoDB target**: Firestore (`service_map`) vs Bigtable (`aws-to-gcp-service-map`, `service-classification`) vs "Firestore/Bigtable/Spanner" (`service_specs`).
2. **Inferentia/Trainium**: priced as TPU v5e (`accelerator-chips`+`tpu-rates`+`apply_static_mappings`) vs listed `passthrough_services` (`service-classification`) vs mapped to G2/A2 GPU families (`gcp-model-config`). **Three treatments.**
3. **Trainium2 generation**: `gcp-model-config`→A3/H100 vs `tpu-rates` pins only v5e.
4. **Passthrough budget**: 10% in code vs 5% in comment (`validate_fix.py`).
5. **Commitment netting**: ignore (`apply_commitment_ignores`) vs include (`render_report`) — see §8.
6. **Lambda target string**: "Cloud Run Functions" vs "Cloud Run functions" — identity strings must match downstream lookups.
7. Preemptible fetched in `incremental_rerate.py:77` but not `apply_rates.py:596`.

---

## 11. Spec-generation assumptions (upstream of everything)

| Assumption | Location |
|---|---|
| EC2 specs scraped from **us-east-1 only** ("identical across regions") | `refresh_ec2_specs.py:22` |
| **First spec variant wins** ("identical across OS/tenancy") | `refresh_ec2_specs.py:125` |
| AWS region → one "closest" GCP region; gov/china → commercial fallback | `validate_fix.py:322` |
| Container-code → region lists per continent (drift-fragile as GCP adds regions) | `apply_rates.py:82` |
| `accelerator-chips.json` / `tpu-rates.json` / `fx_rates.json` all pinned, self-flagged "VERIFY" | data files |

---

## Highest-leverage items to revisit (by $ impact / fragility)

1. **ACU 0.25 vCPU** — un-sourced, drives all Aurora Serverless v2 sizing.
2. **Commitment netting conflict** — two opposite shipped rules; ingest-path-dependent; documented $60K and $5K mis-reports.
3. **EDP/discount dropped → GCP-at-list** — systematic directional bias on every enterprise bill.
4. **Memory/storage AWS families → N4 general-purpose** — wrong GCP family class for r/i/d/h.
5. **GPU count inferred from vCPU** — no CUR column; every GPU row is an estimate.
6. **Accelerator cost dropped for 6 chips + all non-Nvidia** — spend silently vanishes.
7. **Downward GCP bias stack** — cheapest-region + CUD→OD + Spot→OD fallbacks, none symmetric.
8. **io2 provisioned IOPS** — may be wrongly ignored like gp3 (pd-extreme does bill IOPS).
9. **Self-host sizing (OpenSearch/MSK/MQ)** — footprint assumed = real cluster, no replication.
10. **730h/mo + 1.074 GB/GiB** — pervasive; correct, but worth a single shared constant.
