# Phase 2 — Mapping

**Run by:** 4–5 sub-agents in parallel, partitioned by GCP target
family. Main agent dispatches all in one multi-tool message and waits.
**Reads:** the slice of `aws_li_catalog` rows owned by the partition,
the bundled catalog at `data/skus/`, the helper at
`scripts/find-sku.sh`.
**Writes:** rows in `aws_li_to_gcp_li` for the owned `aws_li_key`s; an
append-only `projection-audit/mapping-notes.md` with rationale entries.
**Returns to main:** `{slice_name, mapped_count, unmapped_keys[]}`.

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

Pick the GCP SKU that is the **best price–performance equivalent**,
not the closest spec match. Weight is **60% price / 40% performance**
— but this is a fine-line judgment call, not a license to cost-cut.

The rule kicks in only when **both options are genuinely close** on
the other axis:

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
6. **HA tier comes from the description text, not from instance size.**
   For services with HA-tier pricing on GCP (Cloud SQL Regional vs
   Zonal, AlloyDB primary vs read pool, Memorystore Standard vs
   Basic):
   - Map to the HA / Regional tier **only when the AWS description
     literally contains** `"Multi-AZ"`, `"Multi-Region"`, `"HA"`, or
     equivalent.
   - Otherwise default to Zonal / Single-AZ, even for large instance
     sizes.
   - Never infer HA from "this is a production-sized DB". The bill
     tells you what the customer is actually paying for. A 48-vCPU
     `db.m6g.12xlarge` with `"reserved instance applied"` (and no
     Multi-AZ in the text) is Zonal.
7. **Pick concrete `gcp_sku_id` after evaluating alternatives.** For
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
11. **`projection_note`**: one sentence per row. Capture the
    rationale ("baseline gp3 → Hyperdisk Balanced", "Multi-AZ → Cloud
    SQL Regional", "RI-applied: AWS rate is post-RI, compare GCP
    CUD", etc.). The reviewer reads these.

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
