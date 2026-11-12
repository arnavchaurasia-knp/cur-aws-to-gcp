# WORKORDER — Best-Fit GCP Target Selector (cheapest + fit + regional availability)

Status: **DRAFT — being filled one service at a time.** See the progress checklist in §4.
Owner-review required on anything marked ⚠️. Author of the worked sections: pricing-review pass (this session).

---

## 0. TL;DR for the implementer

Today the pipeline maps each AWS **managed** service to **one hard-coded GCP product** (`gcp_target`, a single string per charge-type in `data/service_specs.json`) and, at best, picks the cheapest *tier within that one product*. It does **not**:

1. consider **alternative GCP products** that could be cheaper or a better fit (e.g. ElastiCache → only Memorystore for Redis Basic; never Valkey / Redis Cluster / self-host), and
2. check whether the chosen managed target is **actually offered in the customer's region** (managed targets trust the pricing catalog, which the codebase itself documents as "a PRICING system, not a deployability system").

The **compute path already does this correctly** — `cheapest_in_scope()` sweeps a candidate family set with safety rules, and `verify_compute_availability.py` gates on live Compute Engine region availability. **This workorder generalizes that proven pattern to managed services**, one service at a time.

Do **not** invent a new framework. Reuse the compute pattern's three ideas: (a) a candidate set, (b) a safety rule that forbids silent downgrades, (c) an availability gate. Then pick the cheapest surviving candidate and **disclose the alternatives in the projection note** so every decision is auditable.

---

## 1. Why this exists (evidence)

Confirmed in the pricing-review pass that produced this workorder:

- **ElastiCache `cache.t4g.small` → Memorystore for Redis Basic M1 projects at 218% of AWS** ($0.032/hr AWS vs 1.42 GiB × $0.049/GiB-hr = $0.0696/hr GCP). The math is *correct*; the **target choice** is the problem — Redis Basic is the most expensive managed Redis option and has **no burstable tier** to match a `t4g` node.
- **Memorystore for Valkey** (`shared-core-nano` node) is Redis-wire-compatible at ~$0.023/GiB-hr — roughly half — landing near AWS parity. It is **not referenced anywhere in the codebase**; there is no mechanism to even consider it.
- The same shape recurs across services: RDS/Aurora → only Cloud SQL (never AlloyDB/Spanner); MemoryDB → only Redis (never Valkey); DocumentDB/DynamoDB → a single document/wide-column target without comparison.

Sources verified against official pages during the review: `cloud.google.com/memorystore/docs/redis/pricing`, `cloud.google.com/memorystore/docs/valkey/pricing`, `cloud.google.com/memorystore/docs/redis/cuds` (M1 < 5 GB is **CUD-ineligible** — see §3.4).

---

## 2. The existing compute pattern to generalize (read this before coding)

- `cheapest_in_scope(default_label, vcpu, ram_gib, region, archs, tiers, workloads, min_network_tier, min_generation)` — `scripts/apply_static_mappings.py:1504`. Prices **every** family in `_GP_FAMILIES` that is *safe to compare*, returns the cheapest, and tags the result `switched`/`reason` so a fallback is never mistaken for an optimization.
- **Performance-Tier Safety Rule** (CLAUDE.md §8): a sustained AWS source may NOT be silently swapped to a cheaper *burstable* GCP family; compute-optimized may not cross into general-purpose; a network/HPC tier may not be dropped; a hardware-generation floor (N4D/C4D/C4A) applies. Downgrades are only allowed when they are a pure win or explicitly flagged.
- `verify_compute_availability.py` + `_live_compute_availability()` (`apply_static_mappings.py:968`) — probes the **live** Compute Engine `machineTypes.aggregatedList` API per zone → region, writes `data/compute_family_availability.json`, consulted before a family is chosen. Exists precisely because the billing catalog gives false positives (N4D priced in Delhi but not creatable) and false negatives.

**The generalization:** managed services need the same three things — a candidate set, a safety rule, and an availability gate — plus two managed-specific concerns: **CUD eligibility per candidate** (§3.4) and **cheapest-tier within each candidate** (already have `_cheapest_memorystore_tier` as a model).

---

## 3. Design

### 3.1 Candidate set in `service_specs.json`

Replace the single `gcp_target` string on a charge-type with a `gcp_candidates` array (keep `gcp_target` as the *default/first* for backward compatibility). Schema per candidate:

```json
"gcp_candidates": [
  {
    "service": "Cloud Memorystore for Redis",
    "role": "default",                     // default | alternative | fallback
    "fit_class": "managed_like_for_like",  // see §3.2
    "safety": "safe",                      // safe | flag_downgrade | never_silent
    "cud_eligible": true,                  // may be overridden per-SKU, see §3.4
    "cud_exclude_sku_re": "Redis Capacity (Basic|Standard) M1\\b",
    "availability_key": "redis.googleapis.com",  // see §3.3
    "note": "per-GiB-hour dedicated capacity; no burstable tier"
  },
  { "service": "Memorystore for Valkey", "role": "alternative", "fit_class": "managed_like_for_like", "safety": "safe", "cud_eligible": true, "availability_key": "memorystore.googleapis.com", "note": "shared-core node ≈ burstable; ~½ per-GiB cost" },
  { "service": "Compute Engine (self-hosted)", "role": "fallback", "fit_class": "self_host", "safety": "flag_downgrade", "cud_eligible": true, "note": "loses managed HA/patching; only when flagged or user opts in" }
]
```

### 3.2 Fit classes (what a candidate costs you in capability)

| fit_class | meaning | selection rule |
|---|---|---|
| `managed_like_for_like` | same managed capability (HA, patching, backups comparable) | freely comparable on price |
| `managed_reduced` | managed but fewer features (e.g. no Multi-AZ equivalent) | comparable only if source didn't use the missing feature |
| `self_host` | run it yourself on GCE | **never silent**; only when flagged or user opts in; always disclosed as a capability downgrade |
| `no_equivalent` | nothing on GCP | passthrough at AWS cost with reason |

### 3.3 Availability gate for managed services (the missing subsystem)

Compute has `verify_compute_availability.py`. Managed services have **nothing**. Build the analogue:

- **Source of truth:** per-service regional availability. Two acceptable implementations (pick per service, cheapest first):
  1. **Curated matrix** `data/managed_service_availability.json` — `{service_api: {region: true}}`, refreshed from each product's "locations" docs. Low-tech, no creds, good enough for most managed services (region lists change rarely).
  2. **Live probe** (where an API exists): `{service}.googleapis.com/v1/projects/{p}/locations` returns supported locations (the same pattern already noted for `redis.googleapis.com`/`memcache.googleapis.com` at `apply_static_mappings.py:1058`). Mirror `verify_compute_availability.py`'s token/fallback handling.
- **Gate rule:** a candidate whose `availability_key` is not available in the row's `gcp_region` is **dropped from the candidate set** before the cheapest pick. If *no* candidate is available in-region, fall back to the dominant-region price with a disclosed note (same philosophy as `od_pick`'s dominant-region fallback), never silently price a non-existent target.
- **Never-break rule:** missing/stale availability data must fall back to today's behavior (price the default target), exactly like `_live_compute_availability()` does. The probe running or not can never fail a job.

### 3.4 CUD eligibility per candidate (carry the M1 lesson forward)

`cud_pct.json` lists CUD-eligible services and 1yr/3yr multipliers, and `apply_rates.py:834-852` synthesizes `Commit1Yr/3Yr = OnDemand × multiplier` for every SKU of an eligible service. **Two bugs this selector must not reintroduce or inherit:**

- **Over-credit (confirmed bug):** Memorystore for Redis **M1 capacity tier (< 5 GB) is CUD-ineligible** (`cloud.google.com/memorystore/docs/redis/cuds`), yet synthesis applies the blanket 0.75/0.48 multipliers to M1 SKUs. Fix: add `cud_exclude_sku_re` per service to `cud_pct.json` and inject `AND NOT regexp_matches(gcp_sku_name, ?)` into the synthesis INSERT. **Scope the regex to `Redis Capacity … M1`** — do NOT catch Memorystore-for-Memcached's "Custom Core/RAM **M1**" (different meaning) or any eligible M2–M5 tier. Valkey has the same < 5 GB exclusion.
- **Under-credit (name-match drop-out):** synthesis keys on an **exact `gcp_service` name match** between the mapping table and the rate table. If a candidate's label in `aws_li_to_gcp_li` differs from its label in `gcp_sku_rates`, zero commit rows are synthesized and the 1yr/3yr columns silently fall back to OD (`projection_view.py:393/404` COALESCE). Every candidate's `service` string here MUST equal the `gcp_service` the rate loader writes. Add the validation gate in §3.6.

### 3.5 Selection algorithm (per row)

```
candidates = spec.gcp_candidates (or [gcp_target])
candidates = [c for c in candidates if available_in(c.availability_key, row.gcp_region)]   # §3.3
candidates = [c for c in candidates if safe_for(c, row)]                                    # §3.2 / safety
for c in candidates: c.cost = cheapest_tier_cost(c, row.size, row.region)                   # per-candidate tier sweep
pick = min(candidates, key=cost) among role in {default,alternative}; self_host only if flagged
note = "selected {pick.service} ({pick.cost}); alternatives considered: {others + costs}; {downgrade/availability disclosures}"
```

Mirror `cheapest_in_scope`'s `switched`/`reason` discipline: record WHY (cheaper vs only-available), never let a forced fallback read like an optimization.

### 3.6 Guard tests (add once, protect forever)

- `test_candidate_availability_gate.py` — a candidate dropped for region X never appears as a selected target for a row in region X.
- `test_candidate_service_names_match_rates.py` — every `gcp_candidates[].service` string exists as a `gcp_service` value the rate loader can produce (kills the §3.4 under-credit class).
- `test_cud_m1_exclusion.py` — no `Commit1Yr/3Yr` row is synthesized for a `Redis Capacity … M1` or Valkey < 5 GB SKU; Memcached "Custom … M1" is untouched.
- `test_no_silent_self_host.py` — a `self_host` candidate is never selected unless the flag/opt-in is set.

---

## 4. MASTER PROGRESS CHECKLIST (all 55 services) — **the tracker**

Disposition: **CAND** = needs candidate-set + availability work · **SINGLE** = one sensible target, keep as-is (verify availability only) · **PASS** = passthrough / no GCP equivalent · **COMPUTE** = already routed through the compute optimizer (`cheapest_in_scope`) so it inherits candidate+availability for free.

Status: ☐ not started · ◧ drafted · ☑ done+verified.

| # | service | current target (mech) | disposition | §ref | status |
|---|---|---|---|---|---|
| 1 | elasticache | Memorystore Redis Basic (`elasticache`) | **CAND** | §5.1 | ☑ |
| 2 | memorydb | Memorystore Redis (`elasticache`) | **CAND** | §5.2 | ◧ |
| 3 | rds | Cloud SQL (`managed_db`) | **CAND** | §5.3 | ◧ |
| 4 | documentdb | (document) (`managed_db`) | **CAND** | §5.4 | ◧ |
| 5 | dynamodb | Firestore/Bigtable/Spanner (`dynamodb_*`) | **CAND** | §5.5 | ◧ |
| 6 | redshift | BigQuery (`redshift`) | **CAND** | §5.6 | ◧ |
| 7 | opensearch | self-host GCE (`opensearch`) | **CAND** | §5.7 | ◧ |
| 8 | msk | self-host GCE (`msk`) | **CAND** | §5.8 | ◧ |
| 9 | kinesis | Pub/Sub (`kinesis`) | **CAND** | §5.9 | ◧ |
| 10 | mq | self-host / Cloud Tasks (`misc`) | **CAND** | §5.10 | ◧ |
| 11 | emr | Dataproc (`emr`) | SINGLE(tiers) | §6 | ◧ |
| 12 | sagemaker | Vertex AI (`misc`) | SINGLE(tiers) | §6 | ◧ |
| 13 | mwaa | Composer (`mwaa`) | SINGLE | §6 | ◧ |
| 14 | glue | Dataflow/Dataproc (`glue`) | **CAND** | §5.11 | ◧ |
| 15 | athena | BigQuery (`athena`) | SINGLE | §6 | ◧ |
| 16 | quicksight | Looker (`quicksight`) | SINGLE | §6 | ◧ |
| 17 | lambda | Cloud Run (`per_request`) | SINGLE(formula) | §6 | ◧ |
| 18 | fargate | GKE Autopilot (`per_request`) | SINGLE | §6 | ◧ |
| 19 | eks | GKE (`flat_hourly`) | SINGLE | §6 | ◧ |
| 20 | ecs | GKE/Cloud Run (`misc`) | **CAND** | §5.12 | ◧ |
| 21 | ecr | Artifact Registry (`misc`) | SINGLE | §6 | ◧ |
| 22 | s3 | Cloud Storage (`object_storage`) | SINGLE(tiers) | §6 | ◧ |
| 23 | ebs | Persistent/Hyperdisk (`block_storage`) | **CAND** | §5.13 | ◧ |
| 24 | efs | Filestore (`efs`) | SINGLE(tiers) | §6 | ◧ |
| 25 | fsx | Filestore (`fsx`) | **CAND** | §5.14 | ◧ |
| 26 | kms | Cloud KMS (`misc`/`per_request`) | SINGLE | §6 | ◧ |
| 27 | secretsmanager | Secret Manager (`misc`) | SINGLE | §6 | ◧ |
| 28 | cloudwatch | Cloud Monitoring/Logging (`cloudwatch`) | SINGLE | §6 | ◧ |
| 29 | cloudtrail | Cloud Audit Logs (`misc`) | SINGLE | §6 | ◧ |
| 30 | xray | Cloud Trace (`xray`) | SINGLE | §6 | ◧ |
| 31 | guardduty | SCC (`guardduty`) | PASS | §7 | ☐ |
| 32 | securityhub | SCC (`guardduty`) | PASS | §7 | ☐ |
| 33 | inspector | SCC (`inspector`) | PASS | §7 | ☐ |
| 34 | waf | Cloud Armor (`flat_hourly`) | SINGLE(formula) | §6 | ◧ |
| 35 | shield | Cloud Armor (`shield`) | PASS | §7 | ☐ |
| 36 | route53 | Cloud DNS (`per_request`) | SINGLE | §6 | ◧ |
| 37 | sns | Pub/Sub (`per_request`) | SINGLE | §6 | ◧ |
| 38 | sqs | Pub/Sub / Cloud Tasks (`per_request`) | **CAND** | §5.15 | ◧ |
| 39 | ses | (no equiv) (`misc`) | PASS | §7 | ☐ |
| 40 | cognito | Identity Platform (`misc`) | SINGLE(formula) | §6 | ◧ |
| 41 | apigateway | API Gateway/Endpoints (`per_request`) | **CAND** | §5.16 | ◧ |
| 42 | appsync | (no clean equiv) (`misc`) | PASS | §7 | ☐ |
| 43 | states | Cloud Workflows (`misc`) | SINGLE | §6 | ◧ |
| 44 | events | Eventarc (`misc`) | SINGLE | §6 | ◧ |
| 45 | glue (data cat) | Dataplex (`per_request`) | SINGLE | §6 | ◧ |
| 46 | dms | Database Migration Service (`misc`) | SINGLE | §6 | ◧ |
| 47 | bedrock | Vertex AI Model Garden (`misc`) | SINGLE | §6 | ◧ |
| 48 | ssm | Secret Manager/(no equiv) (`ssm_*`) | SINGLE | §6 | ◧ |
| 49 | acm | CAS/Certificate Manager (`misc`) | SINGLE | §6 | ◧ |
| 50 | directconnect | Dedicated Interconnect (`data_transfer`) | SINGLE | §6 | ◧ |
| 51 | globalaccelerator | (networking) (`data_transfer`) | SINGLE | §6 | ◧ |
| 52 | vpc | VPC/PSC (`flat_hourly`) | SINGLE | §6 | ◧ |
| 53 | elb | Cloud Load Balancing (`flat_hourly`) | SINGLE | §6 | ◧ |
| 54 | backup | GCS/PD snapshot (`block_storage`) | **CAND** | §5.17 | ◧ |
| 55 | datatransfer | network egress SKUs (`data_transfer`) | SINGLE | §6 | ◧ |

> Update the `status` column as each §5 section is completed. Do the **CAND** rows first (that's where the money and the fit risk are); SINGLE rows only need an availability-gate wiring + a one-line confirmation; PASS rows need no selector.

---

## 5. Per-service candidate sections (filled one at a time)

### 5.1 ElastiCache — `elasticache` ☑ GOLD TEMPLATE

**AWS source:** `cache.*` node-hours (Redis & Memcached engines); `deployment_option=Multi-AZ` ⇒ HA. Mapper: `map_elasticache` (`apply_static_mappings.py:5484`).

**Current behavior:** Redis → `Cloud Memorystore for Redis`, cheapest tier via `_cheapest_memorystore_tier`; Memcached → `Cloud Memorystore for Memcached`. Single product; no Valkey; no availability check.

**Candidate set (Redis engine):**

| candidate | fit_class | safety | ~rate | notes | availability_key |
|---|---|---|---|---|---|
| Memorystore for Redis (Basic/Standard) | managed_like_for_like | safe | $0.049/GiB-hr (M1) | current default; no burstable tier; **M1 < 5 GB CUD-ineligible** | `redis.googleapis.com` |
| **Memorystore for Valkey** | managed_like_for_like | safe | ~$0.023/GiB-hr; `shared-core-nano` ≈ burstable | ~½ cost; best fit for `t4g`; same < 5 GB CUD exclusion | `memorystore.googleapis.com` |
| Memorystore for Redis **Cluster** | managed_like_for_like | safe | node/shard-based | only when source is clustered/large | `redis.googleapis.com` |
| Self-hosted Redis/Valkey on GCE | self_host | flag_downgrade | e2-small ≈ $0.0168/hr | can undercut AWS; loses managed HA/patching | (compute availability) |

**Safety:** Redis Multi-AZ source ⇒ require HA-capable candidate (Standard tier / Valkey HA); single-AZ source ⇒ all comparable. `t4g`/`t3` burstable source ⇒ Valkey `shared-core` is the fit-match; mapping it to Redis Basic dedicated is the current over-projection. Self-host only when the flag is set.

**CUD:** apply §3.4 — exclude `Redis Capacity … M1` and Valkey < 5 GB from synthesis; keep Memcached "Custom … M1" eligible.

**Expected impact:** `cache.t4g.small` OD drops from 218% → ~100–115% by selecting Valkey `shared-core`; committed columns for M1 stop showing fabricated 25%/52% discounts.

**Implementer steps:**
1. Add `gcp_candidates` (above) to the `elasticache` charge-type in `service_specs.json`.
2. Add Valkey SKU discovery (mirror `_discover_memorystore_tiers`) + `GCP_MEMORYSTORE_VALKEY` constant; resolve `shared-core`/node SKUs.
3. Extend `map_elasticache` to build candidates, run the §3.5 algorithm, and emit the winner with the disclosure note.
4. Wire the §3.3 availability gate (`memorystore.googleapis.com` locations).
5. Apply §3.4 CUD exclusion.
6. ⚠️ Verify Valkey node rates and the `< 5 GB` CUD exclusion against `cloud.google.com/memorystore/docs/valkey/pricing` before pinning; cite the URL in a comment per AGENTS.md.

**Verification:** re-run the bill with the `cache.t4g.small` row; confirm selected target = Valkey shared-core, OD ≈ parity, committed = OD for the M1-class SKU, and the note lists Redis Basic as a considered-but-pricier alternative.

---

### 5.2 MemoryDB — `memorydb` ◧

**AWS source:** MemoryDB node-hours — a **durable** Redis (multi-AZ transaction log, data persisted), so it is NOT the same as ElastiCache (in-memory cache). Mapper: shares `map_elasticache` via the `is_memdb` branch.

**Candidate set:**

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Memorystore for Redis **Standard** + AOF/RDB persistence | managed_like_for_like | safe | durability match for MemoryDB; HA |
| **Memorystore for Valkey** (durable config) | managed_like_for_like | safe | ~½ per-GiB cost; preferred where available |
| Self-hosted Redis/Valkey on GCE w/ persistent disk | self_host | flag_downgrade | cheapest; loses managed durability guarantees |

**Safety:** MemoryDB is durable by definition ⇒ candidate MUST be a durable/HA config (never a Basic/non-persistent tier — that would understate the guarantee the customer pays for). **CUD:** same < 5 GB exclusion (§3.4). **Availability:** `memorystore.googleapis.com`. **Impact:** same over-projection class as ElastiCache for small nodes; Valkey narrows it. **Verify:** Valkey durability + persistence rates at `cloud.google.com/memorystore/docs/valkey/pricing`.

---

### 5.3 RDS / Aurora — `rds` ◧

**AWS source:** RDS/Aurora instance-hours + storage; engine in product/usage (Postgres, MySQL, MariaDB, SQL Server, Oracle, Aurora-MySQL/PG). Mapper: `managed_db` → Cloud SQL (LLM-sized).

**VERIFIED nuance (keeps this honest):** AlloyDB is **~39% more expensive per vCPU than Cloud SQL** (≈$54.5 vs $39.2/vCPU-mo, us-east1), better *price-performance* for demanding workloads but **not cheaper on sticker**. So for ordinary RDS, **Cloud SQL correctly stays the cheapest default** — the selector must NOT auto-switch to AlloyDB on price (it would lose). The real RDS wins are (a) **Cloud SQL edition** selection and (b) availability gating, not a product swap.

**Candidate set:**

| candidate | fit_class | safety | selection rule |
|---|---|---|---|
| Cloud SQL **Enterprise** | managed_like_for_like | safe | default; cheapest for standard OLTP |
| Cloud SQL **Enterprise Plus** | managed_like_for_like | safe | only if source shows HA/perf tier justifying it |
| **AlloyDB** (Postgres/Aurora-PG only) | managed_like_for_like | flag (never auto on price) | surface as a price-performance *alternative* in the note; select only if flagged or source is high-throughput PG |
| Spanner | managed_reduced | never_silent | only for global/horizontally-scaled sources |

**Engine gate:** AlloyDB/Cloud-SQL-PG only for Postgres & Aurora-PG; SQL Server/Oracle → Cloud SQL (SQL Server) / no-equiv (Oracle) respectively. **Multi-AZ** ⇒ HA-capable edition. **CUD:** Cloud SQL eligible; ⚠️ AlloyDB CUD disputed — verify at `cloud.google.com/alloydb/pricing`. **Impact:** mostly correctness (edition + availability), modest $ vs today. **Verify:** Cloud SQL Enterprise vs Enterprise Plus rates; AlloyDB only as disclosed alternative.

---

### 5.4 DocumentDB — `documentdb` ◧

**AWS source:** DocumentDB (MongoDB-compatible) instance-hours + storage. Mapper: `managed_db`.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Firestore (Native/Datastore) | managed_reduced | flag_downgrade | document model but not Mongo-wire; API-level port, not lift-shift |
| **MongoDB Atlas** (GCP Marketplace) | managed_like_for_like | safe (marketplace-priced) | true Mongo compatibility; passthrough at Atlas price |
| Self-hosted MongoDB on GCE | self_host | flag_downgrade | cheapest; loses managed |

**Safety:** DocumentDB → Firestore is a capability/API change (not drop-in) ⇒ `managed_reduced`, must be disclosed. Atlas is the like-for-like but priced outside the GCP catalog (marketplace) ⇒ passthrough with reason. **Confidence ≤ 0.6.** **Impact:** fixes today's single-target guess; honest disclosure of port cost.

---

### 5.5 DynamoDB — `dynamodb` ◧

**AWS source:** RCU/WCU (provisioned), on-demand request units, storage. Mapper: `dynamodb_capacity` / `dynamodb_storage` (+ existing Firestore/Bigtable/Spanner note).

| candidate | fit_class | safety | pick-by access pattern |
|---|---|---|---|
| Firestore | managed_reduced | flag | key-value + small/document, serverless request-priced |
| Bigtable | managed_like_for_like | safe | wide-column, high-throughput, sustained |
| Spanner | managed_reduced | never_silent | only global-transactional |

**Safety:** workload-dependent, so keep **confidence ≤ 0.5** (existing design). The selector surfaces all three with per-candidate cost + the access-pattern rationale rather than silently committing to one. **CUD:** Bigtable/Spanner eligible; Firestore is request-priced (no CUD). **Impact:** replaces a single guess with a disclosed candidate comparison.

---

### 5.6 Redshift — `redshift` ◧

**AWS source:** node-hours (RA3/DC2) + RA3 managed storage + Spectrum/Concurrency. Mapper: `redshift` → BigQuery via `_REDSHIFT_SLOT_MAP`.

| candidate | fit_class | safety | selection rule |
|---|---|---|---|
| BigQuery **on-demand** ($/TB scanned) | managed_like_for_like | safe | bursty/low-utilization clusters |
| BigQuery **Editions** (Standard/Enterprise slot commitments) | managed_like_for_like | safe | steady high-utilization ⇒ committed slots cheaper |

**Safety:** choose by utilization — a steadily-busy Redshift cluster is cheaper on committed slots; a mostly-idle one on on-demand. The current slot-map assumes one model; the selector should price BOTH and pick the cheaper, disclosing the crossover. **CUD:** BigQuery Editions ARE the commitment model (don't double-apply cud_pct). **Impact:** can swing either way; removes a modeling assumption.

---

### 5.7 OpenSearch — `opensearch` ◧

**AWS source:** data-node/master-node hours + EBS. Current: self-managed GCE (via compute breakdown — already gets `cheapest_in_scope` + availability).

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Self-managed OpenSearch/Elasticsearch on GCE | self_host | current default | already optimizer-routed; keep |
| **Elastic Cloud on GCP** (Marketplace) | managed_like_for_like | safe (marketplace) | true managed like-for-like; passthrough at Elastic price |

**Note:** OpenSearch already benefits from the compute optimizer. The only *addition* is surfacing Elastic Cloud as a managed alternative (disclosed, marketplace-priced) so the comparison isn't self-host-only. Low priority.

---

### 5.8 MSK — `msk` ◧

**VERIFIED:** **GCP Managed Service for Apache Kafka is GA** (`cloud.google.com/managed-service-for-apache-kafka/pricing`). Today MSK maps to **self-hosted GCE only** — a managed like-for-like was missed.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| **Managed Service for Apache Kafka** | managed_like_for_like | safe | NEW default; ~like running Kafka on GCE + managed premium (~20%); CUD 20%/40% (not on Connect) |
| Self-managed Kafka on GCE | self_host | flag_downgrade | current behavior; cheaper infra, shifts ops burden |
| Pub/Sub | managed_reduced | never_silent | only if source is pub-sub-shaped, not a Kafka lift-shift |

**Safety:** prefer the managed Kafka as the honest like-for-like; keep self-host as a disclosed cheaper fallback. Pub/Sub is a re-architecture, never silent. **Cost components to model:** compute + local/long-term storage + inter-zone ($0.01/GiB) + PSC endpoints ($0.01/hr, ≥4/cluster), replication factor 3. **CUD:** eligible (compute portion). **Impact:** adds a managed option that didn't exist in the pipeline. **Verify:** rates at the official pricing page.

---

### 5.9 Kinesis — `kinesis` ◧

**AWS source:** shard-hours (Data Streams), Firehose, data volume. Current: Pub/Sub.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Pub/Sub | managed_like_for_like | safe | default for streaming ingest; throughput-priced |
| **Managed Service for Apache Kafka** | managed_like_for_like | safe | if source is Kafka-shaped / ordered-partition semantics |
| Dataflow (Firehose→) | managed_reduced | flag | only for the Firehose transform/load portion |

**Safety:** shard/partition-ordering workloads map better to Managed Kafka than Pub/Sub; the selector should disclose both. **Impact:** fit improvement for ordered-stream workloads.

---

### 5.10 MQ — `mq` ◧

**AWS source:** Amazon MQ (ActiveMQ/RabbitMQ) broker-hours + storage. Current: self-host / Cloud Tasks (misc).

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Self-hosted RabbitMQ/ActiveMQ on GCE | self_host | current default | no managed GCP broker equivalent |
| Cloud Tasks / Pub/Sub | managed_reduced | never_silent | only for task/queue-shaped use, not JMS/AMQP broker semantics |

**Note:** no true managed GCP equivalent ⇒ self-host is the honest default (optimizer-routed); Cloud Tasks only as a disclosed re-architecture. Mostly correctness.

---

### 5.11 Glue — `glue` ◧

**AWS source:** DPU-hours (ETL), crawler, Data Catalog. Current: Dataflow/Dataproc.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Dataproc (Spark/Hadoop) | managed_like_for_like | safe | Glue Spark ETL → Dataproc (per-vCPU premium model) |
| Dataflow | managed_like_for_like | safe | for streaming/Beam-style pipelines |
| Dataplex | managed_reduced | flag | Data Catalog portion only |

**Safety:** pick by job type (Spark batch ⇒ Dataproc; Beam/stream ⇒ Dataflow). DPU count often absent in CUR ⇒ keep passthrough fallback + confidence ≤ 0.6 when DPU unknown.

---

### 5.12 ECS — `ecs` ◧

**AWS source:** ECS (control plane free; tasks on EC2 or Fargate). Current: misc/GKE/Cloud Run.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| GKE (Standard/Autopilot) | managed_like_for_like | safe | ECS-on-EC2 → GKE; control plane fee parity |
| Cloud Run | managed_reduced | flag | only for request-driven/stateless task shapes |

**Safety:** ECS control plane is free (don't project the GKE mgmt fee onto it — that's an over-projection class flagged in AGENTS.md); only the task compute maps. Pick GKE for long-running, Cloud Run for request-driven.

---

### 5.13 EBS — `ebs` ◧

**AWS source:** gp2/gp3/io1/io2 capacity + provisioned IOPS/throughput + snapshots. Current: `block_storage` → PD/Hyperdisk.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Hyperdisk Balanced / pd-balanced | managed_like_for_like | safe | gp3/gp2 default (0.91x verified) |
| Hyperdisk Extreme / pd-extreme | managed_like_for_like | safe | io1/io2 + provisioned IOPS (GCP DOES bill IOPS on extreme) |
| Hyperdisk Throughput | managed_like_for_like | safe | throughput-optimized (st1-class) |

**Safety:** pick the PD/Hyperdisk type matching the EBS volume type; **do NOT ignore io2 provisioned IOPS** (unlike gp3 where IOPS is included) — verify io2 IOPS rows aren't wrongly zeroed. Mostly correctness; the "candidate" here is the disk *type*, not a product swap.

---

### 5.14 FSx — `fsx` ◧

**AWS source:** FSx Lustre / Windows / ONTAP / OpenZFS GB-months. Current: Filestore.

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Filestore High Scale / Enterprise | managed_like_for_like | safe | FSx Lustre (HPC) → Filestore High Scale |
| Filestore (Basic/Zonal) | managed_reduced | flag | FSx Windows/general |
| NetApp Volumes (GCP) | managed_like_for_like | safe | **FSx ONTAP → Google Cloud NetApp Volumes** (true like-for-like, currently missed) |

**Safety:** FSx-ONTAP specifically has a dedicated GCP partner product (NetApp Volumes) that's a better fit than generic Filestore — surface it. Pick by FSx flavor.

---

### 5.15 SQS — `sqs` ◧

**AWS source:** request-count. Current: Pub/Sub (per_request).

| candidate | fit_class | safety | notes |
|---|---|---|---|
| **Cloud Tasks** | managed_like_for_like | safe | SQS is a task/work queue ⇒ Cloud Tasks is the closer fit than Pub/Sub |
| Pub/Sub | managed_reduced | flag | fan-out/pub-sub shapes |

**Safety:** SQS point-to-point queues map more naturally to Cloud Tasks; Pub/Sub is fan-out. Disclose both; unit models differ (per-request vs throughput) so keep as passthrough-with-note where units don't convert cleanly. Low $.

---

### 5.16 API Gateway — `apigateway` ◧

**AWS source:** API calls (REST/HTTP/WebSocket). Current: Cloud Endpoints / API Gateway (Apigee rejected — 5.7x over).

| candidate | fit_class | safety | notes |
|---|---|---|---|
| Cloud Endpoints / API Gateway | managed_like_for_like | safe | default; keep (Apigee correctly excluded) |
| Apigee | managed_reduced | never_silent | enterprise API mgmt only — NOT a default (5.7x over-projection if auto-picked) |

**Note:** mostly already correct; the candidate framework just codifies "never auto-pick Apigee." Low priority.

---

### 5.17 Backup — `backup` ◧

**AWS source:** AWS Backup vault storage GB-months (source-type varies: EBS snap, RDS snap, EFS, DynamoDB, etc.). Current: block_storage/misc (falls to LLM).

| candidate | fit_class | safety | notes |
|---|---|---|---|
| PD Snapshot storage | managed_like_for_like | safe | for EBS/compute-sourced backups |
| GCS (Nearline/Coldline/Archive) | managed_like_for_like | safe | for object/file backups; tier by retention |
| Backup & DR Service | managed_reduced | flag | application-consistent backups |

**Safety:** needs a **source-type split** (the backup's underlying resource decides the target) — this is the main work; don't price all backup GB at one rate. Lowest priority (needs source-type plumbing first).

---

## 6. SINGLE-target services (no candidate set; wire availability gate + confirm)

For each: keep the one target, but (a) route it through the §3.3 availability gate so it can't be priced in a region it isn't offered, and (b) confirm the target is still the cheapest sensible fit. No candidate sweep. `avail_key` = the availability source for the §3.3 gate (`*.googleapis.com` = live `/locations` probe available; `matrix` = curated `managed_service_availability.json` row). ✅ = confirmed cheapest/only sensible fit; ⚠️ = caveat to check.

| # | service | keep target | avail_key | confirm / caveat |
|---|---|---|---|---|
| 11 | emr | Dataproc | dataproc.googleapis.com | ✅ only managed Spark/Hadoop; already tier-priced ($0.01×vCPU premium) |
| 12 | sagemaker | Vertex AI | aiplatform.googleapis.com | ✅ only ML-platform equivalent; ⚠️ training vs prediction vs notebook are different Vertex SKUs — keep sub-type split |
| 13 | mwaa | Cloud Composer | composer.googleapis.com | ✅ only managed Airflow; ⚠️ Composer has an environment base fee — don't under-project idle environments |
| 15 | athena | BigQuery ($/TB) | bigquery (global) | ✅ only serverless-SQL-on-storage equivalent |
| 16 | quicksight | Looker / Looker Studio Pro | matrix | ⚠️ Looker is pricier per-user than QuickSight; confirm user-count basis before trusting ratio |
| 17 | lambda | Cloud Run (functions) | run.googleapis.com | ✅ fit; ⚠️ GB-sec + requests formula (0.5× ratio) — make static per roadmap T8; Cloud Run requests $0.40/M vs Lambda $0.20/M |
| 18 | fargate | GKE Autopilot | container.googleapis.com | ✅ fit; mCPU×1000 + Pod memory; ARM variants exist |
| 19 | eks | GKE mgmt fee | container.googleapis.com | ✅ $0.10/hr 1:1 parity; keep as SKU not passthrough |
| 21 | ecr | Artifact Registry | artifactregistry.googleapis.com | ✅ only container-registry equivalent |
| 22 | s3 | Cloud Storage | storage (multi-region) | ✅ fit; ⚠️ storage-CLASS selection (Standard/Nearline/Coldline/Archive) is the real "candidate" axis — keep the pinned class map (Glacier→Coldline, Deep Archive→Archive); requests stay passthrough (incompatible unit model) |
| 24 | efs | Filestore | file.googleapis.com | ✅ fit; Standard→Basic SSD, IA→HDD; tier-priced |
| 26 | kms | Cloud KMS | cloudkms.googleapis.com | ✅ parity; software $0.06/version, HSM $1–2.50, ops $0.03/10k — branch already correct |
| 27 | secretsmanager | Secret Manager | secretmanager.googleapis.com | ✅ $0.06/version + $0.03/10k; verified correct |
| 28 | cloudwatch | Cloud Monitoring + Logging | monitoring/logging (global) | ✅ fit; ⚠️ alarms & dashboards are FREE on GCP → IGNORE those rows (don't passthrough); logs→Log Storage SKU |
| 29 | cloudtrail | Cloud Audit Logs | logging (global) | ⚠️ first copy of management events FREE on GCP → IGNORE free-tier rows; only paid data-events map |
| 30 | xray | Cloud Trace | cloudtrace.googleapis.com | ✅ ingestion ×1.0 (~25× cheaper) |
| 34 | waf | Cloud Armor | matrix (global) | ✅ fit; ⚠️ FORMULA not passthrough: policy $5/mo + rule $1/mo + $0.60/M requests — components map 1:1 |
| 36 | route53 | Cloud DNS | dns (global) | ✅ zones $0.20 vs $0.50; queries $0.40/M parity — order after resolver rule |
| 37 | sns | Pub/Sub | pubsub.googleapis.com | ✅ fit; throughput-priced vs per-request (keep passthrough where units don't convert) |
| 40 | cognito | Identity Platform | identitytoolkit.googleapis.com | ⚠️ MAU ≈ requests÷20 formula (LLM-guided today); confirm MAU basis |
| 43 | states | Cloud Workflows | workflows.googleapis.com | ✅ fit; $0.01/K vs $0.025/K steps — SKU upgrade trivial (same unit model) |
| 44 | events | Eventarc | eventarc.googleapis.com | ✅ only event-bus equivalent |
| 45 | dms | Database Migration Service | datamigration.googleapis.com | ✅ fit; ⚠️ homogeneous migrations FREE on GCP — don't map DMS instance-hours to GCE compute (over-projects) |
| 46 | bedrock | Vertex AI Model Garden | aiplatform.googleapis.com | ⚠️ per-model token pricing; model-by-model map, not a flat ratio |
| 47 | ssm | Secret Manager / (no equiv) | secretmanager / matrix | ⚠️ Parameter Store (advanced) → Secret Manager; Session Manager has no charge equivalent — passthrough |
| 48 | acm | CAS / Certificate Manager | privateca / certificatemanager | ⚠️ public certs free both sides; Private CA $400/mo → CAS (review, not $0) |
| 49 | directconnect | Dedicated Interconnect | matrix | ✅ port-hours map; fixed |
| 50 | globalaccelerator | (networking) | matrix | ⚠️ no clean 1:1; passthrough with reason |
| 51 | vpc | VPC / PSC | compute (global) | ✅ flat-hourly + PSC; NAT/endpoints handled in their own rules |
| 52 | elb | Cloud Load Balancing | compute (global) | ✅ forwarding rules + data processing |
| 53 | datatransfer | network egress SKUs | pinned (egress_rates.py) | ✅ interzone/interregion/internet pinned; ⚠️ see bug WO (inter-region mislabel) |

**Note:** several ⚠️ rows (cloudwatch alarms, cloudtrail free-tier, dms free migrations, waf formula) are *IGNORE/FORMULA* corrections already tracked in other work-orders — this table just confirms the single target is right and flags where the *mechanic* (not the target) still needs work.

## 7. PASS services (no GCP equivalent; passthrough with reason — no selector)

guardduty, securityhub, inspector (→ SCC labels), shield (→ Cloud Armor Enterprise parity), ses (no equivalent), appsync (no clean equivalent). Leave as passthrough; just ensure the reason text is present.

---

## 8. Implementation order

1. **Framework first** (§3): `gcp_candidates` schema + selector function + §3.3 availability subsystem + §3.4 CUD fix + §3.6 guard tests. Land with ElastiCache (§5.1) as the first consumer.
2. Then the **CAND** rows in §4 order (money + fit risk).
3. Then wire the availability gate through the **SINGLE** rows (mechanical).
4. **PASS** rows: no code.

Every rate pinned from a page must cite the official URL in a comment (AGENTS.md pricing-truth policy). Never fabricate a CUD or a $0 on a billed charge.
