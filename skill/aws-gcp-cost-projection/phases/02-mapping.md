# Phase 2 — Mapping

## Pre-Processing (deterministic — runs before this phase)

Before Phase 2 agents run, two deterministic scripts have already stamped each row:

1. classify_mechanics.py — adds mechanic_group to every aws_li_catalog row
2. classify_transfer.py — pre-fills DataTransfer rows in aws_li_to_gcp_li with correct direction

Phase 2 agents MUST check mechanic_group before mapping. The groups and their handling:

| mechanic_group | Who maps it | Approach |
|---|---|---|
| compute_breakdown | candidate_pool.py (script) | Family already resolved; agent confirms SKU ID |
| data_transfer | classify_transfer.py (script) | Pre-filled; agent only handles unclassified rows |
| block_storage | Phase 2 storage agent | volume_type→PD type lookup table (below) |
| managed_db | Phase 2 managed-db agent | Engine + tier judgment |
| flat_hourly | Phase 2 networking agent | Fixed SKU per service type |
| per_request | Phase 2 misc agent | Request-unit mapping |
| object_storage | Phase 2 storage-analytics agent | S3 class → GCS class lookup |
| commitment_discount | Auto-ignore | Set strategy='ignore', gcp_sku_id=NULL |
| misc | Phase 2 misc agent | Best-effort, document confidence |

### Block Storage Lookup Table (deterministic — use this, do not guess)

| AWS Volume Type | GCP PD Type | Notes |
|---|---|---|
| gp2 | pd-balanced | Standard balanced persistent disk |
| gp3 | pd-balanced | Same tier, gp3 is newer but same GCP equivalent |
| io1 | pd-ssd | Provisioned IOPS → SSD |
| io2 | pd-ssd | Same as io1 |
| st1 | pd-standard | Throughput-optimized HDD |
| sc1 | pd-standard | Cold HDD → standard |
| magnetic (standard) | pd-standard | Legacy |
| Aurora storage | pd-ssd | Aurora uses high-performance storage |
| EFS | Filestore (Basic HDD or Basic SSD) | Match to perf tier |

### Commitment/Discount rows — always ignore
Rows with mechanic_group='commitment_discount' MUST be set to strategy='ignore'.
These include: RIFee, SavingsPlanRecurringFee, EdpDiscount, BundledDiscount.
They represent amortized commitment costs already reflected in effective rates.
NEVER try to map these to GCP — it double-counts.

**Run by:** 4–5 sub-agents in parallel, partitioned by GCP target
family. Main agent dispatches all in one multi-tool message and waits.
**Reads:** the slice of `aws_li_catalog` rows owned by the partition,
the bundled catalog at `data/skus/`, the helper at
`scripts/find-sku.sh`.
**Writes:** rows in `aws_li_to_gcp_li` for the owned `aws_li_key`s; an
append-only `projection-audit/mapping-notes.md` with rationale entries.
**Returns to main:** `{slice_name, mapped_count, unmapped_keys[]}`.

## FIRST LINE OF THIS PHASE — write progress marker

Before any other work, write `progress.json` in the job working directory:

```python
import json
with open("progress.json", "w") as f:
    json.dump({"phase": 2, "phase_name": "Mapping", "last_activity": "Mapping AWS line items to GCP"}, f)
```

This is required — the UI reads it every 5 s and will show a blank screen for phases before the last if you skip it.

## Partition table

Standard partition (drop empty ones; combine if a partition has <5 rows):

| Partition | AWS sources | Primary GCP services |
|---|---|---|
| **compute** | EC2 (Box/Reserved/Spot/SoleTenancy), EBS, EIP, Snapshots, Dedicated Hosts | Compute Engine |
| **managed-db** | RDS, Aurora, ElastiCache, DynamoDB, MemoryDB, DocumentDB | Cloud SQL, AlloyDB, Cloud Memorystore, Cloud Bigtable, Cloud Firestore |
| **networking** | DataTransfer, NAT/Transit Gateway, ELB/ALB/NLB, Route 53, CloudFront, Shield/WAF, VPN, Direct Connect | Networking, Cloud DNS, Cloud Load Balancing, Cloud CDN, Cloud Armor |
| **storage-analytics** | S3, S3 Glacier, Athena, Glue, EMR, Redshift, Kinesis Firehose | Cloud Storage, BigQuery, Cloud Dataflow, Cloud Dataproc |
| **misc** | Lambda, SQS/SNS/Kinesis Streams, KMS, Secrets Manager, CloudWatch, EKS, ECS/Fargate, anything else | Cloud Run / Functions, Pub/Sub, KMS, Cloud Logging/Monitoring, GKE |

Keep concurrency at **4–5 max**. If a partition is huge (compute often
is, with distinct instance families × regions), split it once more by
region group rather than launching a 6th agent.

## Briefing each sub-agent

Each mapping sub-agent must read this whole file before starting. Brief
it with:
- Path to the schema reference: `reference/schemas.md`.
- Its slice of `aws_li_catalog` (export to a small JSON/CSV under
  `mapping/slices/<partition>.csv`, or pass via filter).
- Path to the catalog: `data/skus/`, `data/services.json`.
- Path to the helper: `scripts/find-sku.sh`.
- Path to the notes journal: `projection-audit/mapping-notes.md`
  (append-only).

## Per-row reasoning — `operation` is the authoritative detail field

On AWS Cost Explorer Detail Reports (PDF or flat-CSV), `usage_type`
in `aws_li_catalog` carries the Cost-Explorer *rollup* string (e.g.
`"Amazon Elastic Compute Cloud running Linux/UNIX"`) — the same
value covers every Linux instance type in the region. The instance
type, disk class, data-transfer sub-type, and net unit price live
in `operation` (the verbatim AWS Description). Phase 1's dedup
tuple includes `operation`, so each distinct row in the bill becomes
its own catalog row — but you must read `operation` per-row to know
what you're actually mapping.

Concretely, **do not key Phase 2 mapping decisions on `usage_type`
alone.** When two catalog rows share `(product, usage_type, region)`
but differ in `operation`, they are different workloads and need
different mappings — for example `"On Demand Linux t3.micro Instance
Hour"` vs `"On Demand Linux m5.4xlarge Instance Hour"`. Treat
`operation` as the structural key for compute, EBS, data-transfer,
RDS, and S3 rows; `usage_type` is auxiliary metadata.

For raw CUR shape, `usage_type` already carries the granular
`BoxUsage:<instance-type>` form and the same discipline applies
trivially.

## Two principles that govern every choice

### 1. Bill is ground truth

The input bill is the only authoritative record of commercial
mechanisms on this workload, and the only authoritative record of
what the customer is buying. **Don't invent stories.** If you're
constructing a narrative to explain a discrepancy or justify a
deviation from defaults, treat that as strong evidence you're wrong.

| Visible — fair game | Invisible — don't invoke |
|---|---|
| Explicit discount lines (EDP, CK, Private Pricing, Volume) | Speculative reseller absorption ("CK probably absorbs this") |
| Credits and refunds | Speculative parent-account bundling (Shield Advanced, etc.) |
| SP-coverage offsets | "Probably free tier" without a cited published URL |
| RI-applied rows (`reserved instance applied` in description) | "Promo credit applied silently" |
| AWS description text (Multi-AZ, region, instance type) | Customer's "probable" Intel/ISA dependency, HA need, etc. |

**Two applications of the rule:**

- **Commercial-mechanism failures.** A row's cost is unexpectedly
  low/zero and no line item explains it → the cause is almost always
  a parsing or multiplier bug, not a hidden absorption. Fix the bug;
  don't write it into the report.
- **Technical-deviation failures.** You're picking a costlier GCP
  family / tier than the 60/40 default → the deviation needs evidence
  in the bill or customer docs, not a plausible-sounding story
  ("retain AVX-512 perf", "production-sized DB needs HA"). The bill
  tells you what the customer is paying for; match that.

### 2. Equivalence intent (price–performance, 60/40)

To prevent the mapping agent from making catastrophic "cheap-out" mistakes (e.g., mapping a memory-heavy database to a shared-core e2-micro), you must apply a **Two-Stage Filtering Pipeline**:

#### Stage 1: The Hard Boundary Filter (Zero Tolerance)
Before running any scoring or price comparison, eliminate incompatible candidates by applying these strict limits:
1. **The RAM/vCPU Ratio Floor:** Calculate the exact Gigabytes-per-vCPU ratio of the source AWS instance (e.g., `r5.xlarge` has 4 vCPUs and 32 GiB RAM, a ratio of 8:1). The target GCP instance **must have a ratio of ≥ source ratio** (e.g. `n2-highmem-4` or custom with ratio `≥8:1`). Disqualify any candidate family with a lower ratio.
2. **The Shared-Core / Burstable Block:** If the AWS `instance_type` does not start with `t` (meaning it is not a burstable instance type like `t3` or `t4g`), **you must explicitly exclude GCP's shared-core tiers (`e2-micro`, `e2-small`, `e2-medium`)** from the eligible candidate pool.

#### Stage 2: The 60/40 Optimization Within the Safe Pool
Only after filtering out the invalid, under-provisioned instances do you select the final SKU:
* Weight is **60% price / 40% performance** on the remaining safe, compatible candidates.
* The rule kicks in only when **both options are genuinely close** on the other axis:

- **Don't sacrifice cost for slight perf/availability gains.** Picking
  Cloud SQL Regional (HA) over Zonal because "it's more reliable"
  when the AWS row is Single-AZ is wrong — the customer isn't paying
  for HA today, don't project HA cost. Same for over-provisioning
  IOPS, upgrading to a faster disk tier, or picking a costlier compute
  family just because the benchmark is 5% better.
- **Don't sacrifice perf/availability for slight cost gains either.**
  Don't downgrade gp3 → Standard HDD, Cloud SQL Enterprise Plus →
  Enterprise, or Memorystore Standard → Basic just to save dollars
  when the AWS workload clearly needs the higher tier.
- **When both options are genuinely close**, pick the cheaper one.
  Example: AWS `m5.4xlarge` (Intel) → GCP `n2d-standard-16` (AMD) is
  fine even at ~5–10% lower per-thread perf — the families are peers,
  the cost win is real, the workload tolerates it.
- **A large performance regression on a perf-critical workload**
  (production databases, latency-bound services, high-IOPS storage)
  is never acceptable for cost reasons. Stay on the closer-perf
  family and note it.

Capture every non-obvious trade-off in `projection_note` and (more
importantly) in `mapping-notes.md` so the Review phase can challenge
it.

## Mapping checklist (apply to every catalog row)

For each row in your slice, INSERT 1+ rows into `aws_li_to_gcp_li`
following this checklist. Skim every row against every rule:

1. **`is_workload = FALSE` → `strategy = 'ignore'`**, all SKU/rate
   fields NULL. The AWS column still shows the cost; GCP cost = $0.
2. **Negative `aws_amortized_cost`** (credits, offsets that survived
   classification) → `strategy = 'ignore'`. Same shape as #1.
3. **Tax** — already dropped at the catalog stage; you should not see
   any tax row here.
4. **Region preserved per row.** Don't consolidate to a single GCP
   region; use the row's `gcp_region`. Only collapse if the user
   explicitly asks.
5. **One AWS LI → 1+ GCP rows.** `break_down` for compute (core+ram),
   databases (core+ram+storage+backup), accelerator instances
   (core+ram+accelerator), local-SSD families (core+ram+storage).
   Don't force a single-row `map` for compute.
6. **HA tier comes from deployment_option or description text, not from instance size.**
    For services with HA-tier pricing on GCP (Cloud SQL Regional vs
    Zonal, AlloyDB primary vs read pool, Memorystore Standard vs
    Basic):
    - Map to the HA / Regional tier **when deployment_option is 'Multi-AZ'** or when the AWS description contains "Multi-AZ", "Multi-Region", "HA", or equivalent.
    - Otherwise default to Zonal / Single-AZ, even for large instance
      sizes.
    - Never infer HA from "this is a production-sized DB". The bill
      tells you what the customer is actually paying for. A 48-vCPU
      `db.m6g.12xlarge` with `"reserved instance applied"` (and no
      Multi-AZ in the text) is Zonal.
7. **Licensing and BYOL routing rules.**
    Check the `license_model` column in `aws_li_catalog`:
    - If `license_model = 'Bring Your Own License'` or `Customer-provided`, map the workload to GCE/Cloud SQL BYOL instances to project compute-only charges and avoid double license fees.
    - If `license_model = 'License Included'`, map to GCP license-included SKUs.
8. **Workload Class Specific Routing Rules.**
    Read the `workload_class` column in `aws_li_catalog` and apply strict family gates:
    - **Burstable:** Map exclusively to GCP shared-core shapes (`e2-micro`, `e2-small`, `e2-medium`).
    - **ARM:** Map exclusively to Tau (`T2A`) or Axion (`C4A`) shapes. Disallow Intel/AMD GCE templates.
    - **Memory-Optimized:** Enforce RAM-to-vCPU ratio $\ge 8:1$ (e.g. `n2-highmem-4` or custom shapes with $\ge 8:1$ memory ratio).
    - **GPU:** Map to `g2` (L4) or `a2` (A100) shapes, matching physical GPU counts and RAM exactly.
    - **Outlier:** If `workload_class = 'Outlier'` (e.g., bare-metal `u-` or `hpc-` instances), mark the row with `strategy = 'outlier_triage'`, assign no default SKU, and write its details to `outlier_instances.md`. Do not apply standard 60/40 heuristics.
9. **Strict Region Descriptor Matching.**
    For standard network egress/data transfer and storage SKUs, the SKU description must match the target row's region display name (e.g. Mumbai -> `... from Mumbai`, Northern Virginia -> `... from Northern Virginia`). NEVER map to a Singapore SKU for egress originating in Mumbai or Virginia; the validation gates will instantly fail.
10. **Pick concrete `gcp_sku_id` after evaluating alternatives.** For
    each AWS LI, don't latch onto the first plausible SKU. Run a small
    evaluation:

   a. **Find the obvious candidate** by matching
      `category.serviceDisplayName` + `category.resourceGroup` +
      region + description in the bundled catalog
      (`data/skus/<service_id>.json.gz`). Component-affinity:
      `core` → vCPU/Core SKU, `ram` → RAM/Memory,
      `storage` → Storage/Disk, `accelerator` → GPU/TPU.

   b. **Surface ONE meaningful alternative** if any exists:
      - A **cheaper-but-equivalent** option (e.g. N2D-AMD vs N2-Intel
        for general-purpose compute; T2A-ARM if the AWS row is a
        Graviton family; gp3 → Hyperdisk Balanced when both fit).
      - A **higher-tier** option only if it's marginally pricier
        *and* the AWS row's description supports it (e.g. Cloud SQL
        Enterprise Plus vs Enterprise when the row already pays for
        HA via Multi-AZ).
      - Skip this step if the choice is unambiguous (S3 → Cloud
        Storage, KMS → Cloud KMS, Route 53 → Cloud DNS, etc.).

   c. **Score both via the 60/40 rule.** Cheaper option wins unless
      the costlier option's perf/availability advantage is **material
      AND matches what the AWS row is paying for** — see "Bill is
      ground truth" above. "Slight perf benefit" is not a reason to
      deviate; "AWS description literally says Multi-AZ / HA" is.

   d. **Append a note** to `projection-audit/mapping-notes.md`,
      keyed on `aws_li_key`. The file is **append-only** — each
      mapping sub-agent contributes its own entries; nobody rewrites
      others'. This is the working journal that gets reviewed in
      Phase 3 before outlier queries fire. Use entries to capture
      *anything you're not 100% sure about*: the alternative you
      considered, a unit-multiplier you derived from an ambiguous
      description, a back-check that came back degenerate, an AWS
      service you weren't sure mapped cleanly to GCP. `projection_note`
      in the mapping row itself stays terse — one sentence on why the
      picked SKU was picked.

      ```markdown
      ## <aws_li_key> — <short row description>
      - Picked: <gcp_sku_id> <sku-name>
      - Alt: <gcp_sku_id> <sku-name> (+X% cost, <perf delta>) —
        rejected per 60/40, <one-line reason>
      - Open: unit_multiplier=<X>, derived from Description
        "<phrase>"; back-check <pass | fail-by-Nx | degenerate>;
        <what to verify or how I'd want this challenged>
      ```

      `Alt` and `Open` lines are optional — only when there's
      something real to record. Obvious unambiguous rows (S3 → Cloud
      Storage at multiplier 1.0, back-check passes) don't need an
      entry at all.

   The point of this step isn't ceremony — it's to force one explicit
   comparison so the 60/40 rule actually fires instead of getting
   overridden by the first plausible-sounding rationale, *and* to
   surface uncertainties for review before outlier rationalization
   sets in.
8. **`unit_multiplier` is the conversion factor** so that
   `total_usage × unit_multiplier × rate_usd` yields GCP-billable
   cost. Common pairings:
   - `Hrs → h` at vCPU count (core) / RAM-GB (ram)
   - `GB-Mo → GiBy.mo` at 1.0
   - `Requests-1000 → Count-10000` at 0.1
   - `Requests → TiBy` at `avg_msg_bytes / 1024^4` (SQS → Pub/Sub)

   **For `break_down` compute and database rows, read vCPU and RAM from
   the catalog — do NOT infer from the operation string or model weights.**
   Phase 1 Step 7 wrote `instance_vcpus` and `instance_ram_gb` into
   `aws_li_catalog` exactly for this purpose. Use them:

   ```sql
   SELECT c.aws_li_key, c.operation, c.total_usage,
          c.instance_vcpus, c.instance_ram_gb, c.instance_arch
   FROM   aws_li_catalog c
   WHERE  c.aws_li_key = '<key>';
   -- Set unit_multiplier = c.instance_vcpus  for the 'core' row
   -- Set unit_multiplier = c.instance_ram_gb for the 'ram'  row
   ```

   If `instance_vcpus` is NULL (lookup miss flagged in Phase 1 anomalies),
   surface a `projection_note` that the multiplier was inferred, and flag
   it in `mapping-notes.md` under `Open:` so Phase 3 can challenge it.

   **Don't normalize EC2/RDS hours to 730 hr/mo.** `total_usage` from
   the bill is already the actual hours billed (744 for a 31-day
   month, 720 for 30-day, 672/696 for Feb). Re-normalizing
   systematically skews the projection.
9. **Back-check is mechanical, not judgment.** This is the predicate
   that catches the rationalization trap. Rule #9 is the ONLY rule
   that's allowed to be a hard predicate — when it fires, the
   conclusion is fixed; you may not talk around it. Apply both
   branches:

   **Branch A — `aws_amortized_cost > $1`: ratio test.**
   `gcp_projected_cost / aws_amortized_cost` must be within 3×
   (i.e. between 0.33 and 3.0). Outside → `unit_multiplier` is wrong,
   full stop. Not "AWS rate is RI / SP / EDP discounted" (that's a
   *visible* line item; outlier triage handles it separately). Here
   it's about the multiplier. Multiplier first, every time.

   **Branch B — `aws_amortized_cost ≤ $1` (the phantom-zero case):
   absolute test, NOT a ratio.**
   `gcp_projected_cost` must also be `≤ $10`. Larger →
   **`unit_multiplier` is wrong, full stop.** No exception allowed
   for:
   - "Free tier we don't see" — only valid if a free-tier line item
     is visible in the bill.
   - "Vendor / reseller absorbs this" — only valid if a discount
     line item explicitly absorbs it.
   - "AWS rounded to zero" — when AWS itself stays near zero, GCP
     should too.
   - "Promotional credit applied silently" — credits show as line
     items.
   - Any "the bill is unusual" narrative.

   No visible mechanism = no exception = flip the multiplier.

   **Default for `per-N` descriptions.** When the AWS Description
   contains `"per 1,000"`, `"per 10,000"`, `"million"`, `"thousand"`,
   `"<N>-hour"`, `"<N>-month"`, or any rate-denomination phrase, the
   default `unit_multiplier = 1.0`. `Usage Quantity` is raw count;
   the description names the *rate denomination* AWS uses to publish
   the price, not the unit of the quantity column. This applies
   uniformly to S3 ops, DynamoDB requests, WAF requests, NAT data
   processed, CloudFront requests, KMS API calls, SNS publishes,
   SQS messages, Lambda invocations, etc.

   **Forced two-interpretation compare.** Whenever you're tempted to
   set `unit_multiplier ≠ 1`, compute the projection under BOTH
   `m=1` and `m=N` explicitly. Pick the one that satisfies Branch A
   or B above. Don't pick on description-text alone.

   **What to do when Branch A or B fails:** Try the other multiplier
   interpretation per the forced two-interpretation compare. If
   neither satisfies the predicate, the unit genuinely doesn't
   reconcile (rare — gp3 MiBps-month overages, OpenSearch throughput
   tiers can do this) → switch to `passthrough` rather than guess.
   Never accept a Branch A / B failure as "the bill is unusual".
10. **`passthrough` is the LAST RESORT, not a comfortable fallback.**
    The whole point of this skill is to project AWS spend to a GCP
    equivalent. A passthrough row tells the customer nothing — its
    Diff is $0 by definition, neither a win nor a loss, just noise
    in the report. Every passthrough is a row where we couldn't do
    our job.

    **Services that may NEVER be passthrough — they always have a GCP
    target.** If a row's `product` matches any entry below, passthrough is
    **FORBIDDEN** — you MUST map it (run `find-sku.sh` to get the SKU).
    Passing these through is the single most common way this skill fails: on a
    real bill it can leave the *majority* of spend unprojected, which makes the
    whole report worthless.

    | AWS product (substring of `product`) | Mandatory GCP target — never passthrough |
    |---|---|
    | EC2 / Elastic Compute Cloud | Compute Engine (`break_down` core+ram) |
    | EBS / Elastic Block Store | Hyperdisk Balanced / Persistent Disk |
    | RDS / Aurora | Cloud SQL or AlloyDB |
    | ElastiCache | Cloud Memorystore (Redis/Memcached) |
    | S3 / Simple Storage Service | Cloud Storage |
    | Data Transfer / Bandwidth / egress | Compute Engine / Networking egress |
    | ELB / ALB / NLB / Load Balancing | Cloud Load Balancing |
    | Lambda | Cloud Run / Cloud Run Functions |
    | Route 53 | Cloud DNS |
    | KMS | Cloud KMS |
    | CloudWatch Logs / Metrics | Cloud Logging / Cloud Monitoring |

    For any row in that table, **none** of these are valid reasons to
    passthrough: "I'm not sure of the exact SKU", "the price ratio looks
    weird", "the AWS rate is RI/SP/PRC-discounted", "it's a small row". They
    are mapping problems to **solve**, not reasons to give up. If you genuinely
    cannot find the exact SKU, map to the closest one and set
    `confidence: low` — but **do not passthrough**.

    **Valid reasons to use `passthrough`:**

    - **No GCP equivalent exists at all.** AWS Support / Enterprise
      Support; AWS Config configuration-item recording (Cloud Asset
      Inventory is free, so just zero-out — that's not passthrough,
      it's `ignore`); AWS Marketplace third-party SaaS; niche
      AWS-only sub-features like S3 Intelligent-Tiering per-object
      monitoring fee (GCS Autoclass bundles tiering into the storage
      rate with no separate line item).
    - **Implied-rate back-check fails on Branch A/B/m=1/m=N** —
      genuinely irreconcilable unit. Rare.

    **Invalid reasons — DO NOT use passthrough for any of these:**

    - **"Price ratio is unfavorable / weird"** — wrong reason.
      Example seen in the wild: S3 Glacier Transition → GCS Class A
      ops, ratio ~0.33. That ratio is *information* — GCS is
      cheaper for this operation. Map it. Don't drop it because
      the math looks lopsided.
    - **"AWS rate looks anomalously low (PRC / RI / SP)"** — wrong.
      The AWS rate is post-discount and we use it as the comparison
      baseline; GCP at list might also be discountable by a
      negotiated GCP rate card we can't see. Show the apples-to-
      oranges gap as the diff; note it in `projection_note`.
    - **"I'm not 100% sure of the right GCP SKU"** — that's what
      `confidence: medium / low` is for, not passthrough.
    - **"It's a small line item, I'll save thinking time"** —
      cumulative passthroughs erode the report's value. Map it or
      mark it `confidence: provisional` for Phase 3 to scrutinize.

    If you find yourself reaching for passthrough, pause and ask:
    "Is there really NO GCP service that does roughly the same
    work?" If you can name a GCP service that does — Cloud Storage,
    Compute Engine, Cloud SQL, etc. — there's almost certainly a
    SKU. Use `find-sku.sh`. Map it.

    `projection_note` for passthroughs must explicitly state which
    of the two valid reasons applies. "No equivalent" or "unit
    irreconcilable" — anything else is suspect and Phase 3 will
    overturn it.

    **Passthrough budget — a hard self-check before you return.** A
    correctly-mapped bill has at most a handful of passthrough rows and
    **well under 5% of total AWS spend** in passthrough. Before signaling
    done, sum `aws_amortized_cost` of your passthrough rows and divide by your
    slice's total. If it exceeds 5% — or if *any* row from the
    never-passthrough table above is passthrough — **you are not finished**:
    the excess is mappable-service rows you gave up on. Go back and map them.
    (A run that passes through the majority of spend has failed, even if every
    individual passthrough "felt" justified.)
11. **`projection_note`**: one sentence per row. Capture the
    rationale ("baseline gp3 → Hyperdisk Balanced", "Multi-AZ → Cloud
    SQL Regional", "RI-applied: AWS rate is post-RI, compare GCP
    CUD", etc.). The reviewer reads these.

## Service-specific mapping guides

These services have non-obvious billing model differences that a
one-line checklist entry can't capture. Read the relevant section
before mapping any row in that category.

### DynamoDB → Cloud Bigtable / Cloud Firestore

DynamoDB bills on **provisioned or on-demand capacity units** (WCU, RCU)
plus storage GB-month, not on instance hours. GCP has no direct
WCU/RCU analogue. Map based on the workload pattern visible in the
bill:

| DynamoDB charge | GCP target | Mapping logic |
|---|---|---|
| Provisioned WCU / RCU (consistent traffic) | **Cloud Bigtable** node-hours | Map one Bigtable SSD node ≈ 10K QPS sustained; WCU and RCU are not interchangeable with node count — use `passthrough` and note "sizing requires workload analysis" unless the bill shows a clear capacity tier |
| On-Demand reads (DynamoDB `ReadRequestUnits`) | **Cloud Firestore** read ops | `unit_multiplier = 1.0`; Firestore bills per read op; back-check ratio |
| On-Demand writes (`WriteRequestUnits`) | **Cloud Firestore** write ops | same; separate SKU per write vs read |
| DynamoDB Streams | Cloud Dataflow / Pub/Sub | `passthrough` unless stream volume is visible; note in `projection_note` |
| DynamoDB storage (GB-month) | Cloud Bigtable or Firestore storage | `unit_multiplier = 1.0` (GB-month → GiBy.mo, adjust 1 GB = 0.931 GiBy if precise) |
| DynamoDB global table replicas | Bigtable multi-cluster replication surcharge | No direct SKU; use `passthrough` and note |
| DynamoDB backup / PITR | Bigtable backup GB-month | `unit_multiplier = 1.0` |

**Key rule:** do NOT blindly map DynamoDB WCU to Firestore write ops at
unit_multiplier=1. The billing denominations are different (one DynamoDB
WCU ≠ one Firestore write op in capacity). If you cannot find a
reconcilable unit, use `passthrough` and write a specific note explaining
which capacity concept has no equivalent — this is one of the few valid
`passthrough` uses.

---

### Lambda → Cloud Run / Cloud Run Functions

Lambda bills on **invocations + GB-seconds** (duration × memory). GCP
bills Cloud Run on **vCPU-seconds + GiB-seconds** separately.

| Lambda charge | GCP target | unit_multiplier |
|---|---|---|
| `Request` (invocation count) | Cloud Run Functions — invocations | `1.0` (1 invocation = 1 invocation) |
| `Duration` (GB-seconds) | Cloud Run Functions — compute (GiB-s) | `1.0` — Lambda GB-seconds and Cloud Run GiB-seconds are the same denomination; back-check passes |
| Lambda@Edge requests | Cloud Run at network edge or Cloud CDN | `passthrough` — no direct peer; note |
| Lambda@Edge duration | same | `passthrough` |
| Provisioned Concurrency | Cloud Run min-instances idle charge | Map to Cloud Run idle vCPU-seconds × memory ratio; flag as `confidence: low` |

**ARM (Graviton) Lambda:** if `operation` contains `"arm64"`, map to
Cloud Run on ARM or Cloud Run Functions 2nd gen (which bills the same
SKU). No multiplier adjustment needed; rate is the same as x86 for
Cloud Run Functions.

**Memory-to-vCPU conversion for Cloud Run (if mapping to standard Cloud Run,
not Functions):**
Cloud Run bills vCPU and memory separately. Lambda GB-seconds conflate
them. Split as:
- vCPU-seconds = GB-seconds × (lambda_vcpu_fraction) — Lambda allocates
  vCPUs proportionally to memory; at 1 GB → 0.5 vCPU (approx), 2 GB → 1 vCPU.
  For the projection, default to 0.5 vCPU per 1 GB memory unless the
  bill shows a specific function configuration.
- memory GiB-seconds = GB-seconds × 0.931 (GB → GiB)

If this split produces an A-branch back-check failure, fall back to
mapping Lambda GB-seconds → Cloud Run GiB-seconds at `unit_multiplier=1.0`
(treating GiB ≈ GB) and note the approximation.

---

### ARM (Graviton) routing decision tree

When `aws_li_catalog.instance_arch = 'arm64'` (set by Phase 1 from the
lookup table), follow this decision tree for the GCP family:

```
Is the target region available?
  ├─ C4A (Axion, ARM) available in region?
  │    └─ YES → map to C4A (best ARM-native match, GCP Axion)
  │    └─ NO  ↓
  ├─ T2A (Tau ARM) available in region?
  │    └─ YES → map to T2A (general-purpose ARM)
  │    └─ NO  ↓
  └─ Fall back to N2D (AMD x86) — note architecture change in projection_note
```

C4A regions (as of 2025): `us-central1`, `us-east4`, `europe-west4`,
`asia-southeast1`. T2A adds: `us-west1`, `europe-west1`, `asia-east1`,
`asia-northeast1`, `southamerica-east1`.

Write the architecture choice into `projection_note` on every Graviton
row. Never silently fall back to N2 (Intel x86) from an ARM instance
without a note.

---

### Data Transfer / Egress matrix

Data-transfer rows (`DataTransfer` product or `Bandwidth` usage_type) need
the destination dimension — it determines which GCP egress tier applies.

| AWS transfer sub-type (from `operation`) | GCP SKU family | Notes |
|---|---|---|
| `"regional data transfer"` / inter-AZ | Compute Engine inter-zone egress (`InterzoneEgress`) | ~$0.01/GB; same-region cross-zone |
| `"inter-region"` within same continent | Compute Engine inter-region egress (`InterregionEgress`) | Rate varies by region pair |
| `"internet"` outbound to internet | Compute Engine internet egress (`InternetEgress`) | Tiered; first 1 TB/month cheaper |
| CloudFront → internet | Cloud CDN egress | Map if CDN row is separate; else treat as internet egress |
| `"Direct Connect"` / `"VPN"` | Cloud Interconnect / Cloud VPN | Separate SKUs; `passthrough` if Interconnect port cost isn't in bill |
| Cross-region `S3` replication | Cloud Storage multi-region replication fee | No direct SKU; `passthrough` with note |
| `"EKS NAT Gateway"` / NAT Gateway processed bytes | Cloud NAT processed bytes | `unit_multiplier = 1.0` (GB → GB) |
| Transfer within same AZ (free on AWS) | No GCP charge for same-zone | `strategy='ignore'` |

For internet egress: AWS and GCP both apply tiered pricing. Phase 4's
blended-rate computation handles this — just map to the correct SKU
and let Phase 4 compute the right rate from the actual GB volume.

## Finding SKUs — use the helper

84K SKUs is too many to grep through interactively. Use
`scripts/find-sku.sh`. It emits TSV rows of
`service<TAB>skuId<TAB>resource_group<TAB>usage_type<TAB>regions<TAB>description<TAB>rate_usd<TAB>unit`:

```bash
# Storage class for an EBS gp3 mapping in Singapore:
scripts/find-sku.sh --service "Compute Engine" --region asia-southeast1 \
                    --keyword "Hyperdisk Balanced Capacity" --usage-type OnDemand

# CUD-1yr core rate for N2D AMD in Singapore:
scripts/find-sku.sh --service "Compute Engine" --region asia-southeast1 \
                    --resource-group CPU --keyword "N2D AMD" \
                    --usage-type Commit1Yr

# Find anything matching a keyword across all 110 services:
scripts/find-sku.sh --keyword "external IP"
```

All flags optional. Without `--service` it scans every service
(~1 second). `--keyword` is a regex matched case-insensitively against
`description`. The price column is the first non-zero `tieredRate` —
the value to put in `gcp_sku_rates.rate_usd` later (Phase 4).

### Common AWS → GCP service-file landings

Saves you the "which file do I open" question:

| AWS feature | GCP service file (display name) |
|---|---|
| EC2 instance core/RAM, Spot, sole-tenancy | Compute Engine |
| EBS gp3 / io2 / sc1 / st1 | Compute Engine (Hyperdisk Balanced/Throughput/Extreme; Persistent Disk for legacy) |
| Snapshots | Compute Engine (resource_group `Storage`, "Snapshot") |
| Public IPv4 / EIP | Compute Engine (resource_group `IpAddress`) |
| NAT Gateway / Transit Gateway | Networking |
| Inter-AZ / inter-Region egress | Networking (look for `InterregionEgress`, `InterzoneEgress`) |
| Internet egress | Networking (look for `InternetEgress` + destination region in description) |
| ELB/ALB/NLB | Networking (Cloud Load Balancing entries) |
| Route 53 zones + queries | Cloud DNS |
| Shield/WAF | Networking (Cloud Armor entries — same parent as Networking) |
| S3 storage + ops | Cloud Storage |
| RDS / Aurora compute, RAM, storage | Cloud SQL or AlloyDB (resource_group `SQLGen2InstancesCPU` / `SQLGen2InstancesRAM` / `SQLGen2InstancesPD-SSD`) |
| ElastiCache Memcached | Cloud Memorystore for Memcached (NOT Redis Basic) |
| ElastiCache Redis / Valkey | Cloud Memorystore for Redis (cluster mode → Redis Cluster) |
| DynamoDB | Cloud Bigtable or Cloud Firestore |
| Kinesis / SNS / SQS | Cloud Pub/Sub |
| Lambda | Cloud Run Functions or Cloud Run |
| EKS | Kubernetes Engine |
| CloudWatch Logs / Metrics | Cloud Logging / Cloud Monitoring |
| KMS | Cloud Key Management Service (KMS) |
| Secrets Manager / SSM Parameter Store | Secret Manager |

When in doubt, run `find-sku.sh --keyword "<aws-feature-word>"`
unscoped first to see which service file owns it.

## Common pitfalls (re-read before mapping)

| Pitfall | Reality | What to do |
|---|---|---|
| In-use Public IPv4 is "free on GCP" | Charged at $0.005/h since 2020 (sku `C054-7F72-A02E`) | Map to Compute Engine `IpAddress` SKU; don't zero it out |
| ElastiCache Memcached → Redis | Memcached and Redis are different engines with different SKUs in Memorystore | Memcached → Cloud Memorystore for Memcached; Redis → Cloud Memorystore for Redis |
| AWS `Usage Quantity` for "per 1,000" lines | Raw count; the description names the rate denomination but the column is unit count | `unit_multiplier = 1.0`; back-check rule #9 |
| "Production-sized DB → Multi-AZ" | The bill says what the customer pays for; large size ≠ HA | Rule #6 — only HA when description literally says so |
| EC2 hours = 730/mo | Bill carries actual hours (720/744/672) | Rule #8 — don't normalize |
| `"reserved instance applied"` row | Real workload at RI-amortized rate, not a discount line | Phase 1 already classified this `is_workload=TRUE`, `pricing_model='Committed'`; compare GCP CUD, not OD in outliers |
| Windows OS premium | Customer may BYOL or pay GCP marketplace; depends on posture | Default to including Windows DC SKU; flag in `projection_note` so customer can request BYOL adjustment |
| AWS ARM (Graviton) → x86 | T2A is ARM, matches `c6g`/`m6g`/`r6g` shape | Map ARM-to-ARM, don't fall back to x86 N2 |

### ECS / Fargate → Cloud Run

Fargate bills on **vCPU-hours + GB-hours of memory**, separately from
ECS task-definition count. The `Fargate` product shows two charge types
in the bill: `vCPU` (per vCPU-second) and `Memory` (per GB-second).

| Fargate charge | GCP target | unit_multiplier |
|---|---|---|
| `AWS Fargate vCPU Hours:perCPU` | Cloud Run — vCPU-seconds | `3600.0` (hrs → seconds); Cloud Run bills per vCPU-second |
| `AWS Fargate GB Hours:perGB` | Cloud Run — memory GiB-seconds | `3600 × 0.931` (hrs → GiBy-seconds; 1 GB = 0.931 GiBy) |
| `AWS Fargate Windows vCPU/GB` | Cloud Run (no Windows); use standard Linux rate | flag in `projection_note` |

**ECS cluster fee:** ECS itself has no control-plane charge (unlike EKS).
If you see a `Amazon Elastic Container Service` line with a small flat charge,
check whether it's Fargate container insight or CloudWatch — map to the
appropriate monitoring SKU or `passthrough` if no GCP equivalent.

---

### EKS → GKE

EKS bills a **per-cluster, per-hour** control-plane fee (currently
$0.10/hr in most regions). GKE's control plane also has a per-cluster fee.

| EKS charge | GCP target | Notes |
|---|---|---|
| `Amazon Elastic Kubernetes Service:AmazonEKS` control-plane hours | GKE cluster management fee | `unit_multiplier = 1.0` (hr → hr); rate from GKE `ClusterManagement` SKU |
| EC2 worker nodes under EKS | Compute Engine (standard instance mapping) | Map normally via compute partition; EKS doesn't add a per-node surcharge |
| EKS Anywhere / Outposts | `passthrough` | No GCP equivalent for on-prem |

---

### MSK (Managed Streaming for Kafka) → Cloud Pub/Sub or Dataflow

MSK bills on **broker instance-hours + storage + data-in**. There is no
direct Kafka-as-a-service on GCP; the closest is Pub/Sub (if the workload
is purely pub/sub patterns) or Cloud Dataflow (if ETL pipelines).

| MSK charge | GCP target | Notes |
|---|---|---|
| Broker instance-hours (e.g. `kafka.m5.4xlarge`) | `passthrough` | Sizing for Pub/Sub or Dataflow requires workload analysis; no direct broker SKU on GCP |
| MSK storage GB-month | `passthrough` alongside broker | Bundle with broker passthrough note |
| MSK data-in GB | Cloud Pub/Sub message delivery | `unit_multiplier = 1.0`; Pub/Sub bills per-GB ingested; back-check ratio |

Always note in `projection_note`: *"MSK broker hours: GCP equivalent requires
workload sizing for Pub/Sub throughput tiers or Dataflow — passthrough."*

---

### OpenSearch / Elasticsearch → GCP (no direct equivalent)

AWS OpenSearch Service has no first-party GCP equivalent. The closest is:
- **BigQuery + Data Profiler** for analytics search patterns
- **Vertex AI Search** for document search
- **Elasticsearch on GCE** (self-hosted) — not a managed service

For projection purposes: `passthrough` on all OpenSearch charges (instance
hours, storage, UltraWarm) with a specific note:
*"OpenSearch Service — no direct managed equivalent on GCP; carrying AWS
cost forward. Customer would run self-hosted Elasticsearch on GCE or
adopt Vertex AI Search depending on use case."*

---

### Fargate-on-EKS vs plain Fargate

When the bill shows `Amazon Elastic Kubernetes Service` + `AWS Fargate`
charges together, the customer runs Fargate pods in EKS. Map:
- Fargate charges per the Fargate guide above.
- EKS control-plane hours per the EKS guide above.
These are separate line items; don't conflate them.

---

### AWS Glue → Cloud Dataflow / Cloud Data Fusion

Glue bills on **DPU-hours** (Data Processing Units, each = 4 vCPU + 16 GB).

| Glue charge | GCP target | unit_multiplier |
|---|---|---|
| Glue ETL job DPU-hours | Cloud Dataflow — vCPU-hours | `4.0` per DPU (1 DPU = 4 vCPUs); memory component → Dataflow memory GB-hours at `16.0` |
| Glue crawler DPU-hours | Cloud Data Catalog + Dataflow | `passthrough` with note — crawlers don't have a direct analog |
| Glue Data Catalog storage / requests | Cloud Data Catalog | No per-storage charge; `passthrough` or `ignore` depending on cost size |

---

## Coverage check before returning to main

Before signaling done, verify in your slice:

```sql
-- Every aws_li_key in the slice is covered exactly once:
SELECT c.aws_li_key
FROM   aws_li_catalog c
LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
WHERE  c.aws_li_key IN (<your slice keys>)
GROUP BY c.aws_li_key
HAVING COUNT(m.aws_li_key) = 0;
```

Any rows returned → `unmapped_keys` in your return payload. Main agent
will flag and either re-dispatch or escalate.
