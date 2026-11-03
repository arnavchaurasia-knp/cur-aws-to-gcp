# Learning path — AWS→GCP cost migration expert

Ordered by what will actually get you asked hard questions, not by textbook
order. Each topic is tagged:

- **[AI]** — any decent AI or the public docs can teach you this. Free.
- **[ME]** — specific to *your* system, your corpus, or a judgement call
  nobody has published. Ask me.

Don't study linearly. Do Track 1 and Track 4 first — they're where you'd be
embarrassed in a review.

---

# TRACK 1 — Discounts and commitments (do this first)

**Why first:** this is the single biggest source of error in your product
today, and the first question any enterprise customer asks. It is also where
AWS and GCP differ *structurally*, not just in price.

| topic | tag |
|---|---|
| AWS Savings Plans: Compute vs EC2 Instance vs SageMaker; 1yr/3yr; no/partial/all upfront | [AI] |
| AWS Reserved Instances: standard vs convertible, regional vs zonal, how RIs are "applied" to usage lines | [AI] |
| AWS Spot: how pricing is set, interruption, Spot vs On-Demand price spread by family | [AI] |
| GCP CUDs: **resource-based** (vCPU/RAM, per-region, per-family) vs **spend-based** (per-service dollar commitment) | [AI] |
| GCP Sustained Use Discounts — automatic, no commitment, and how they stack with CUDs | [AI] |
| GCP Spot VMs (formerly Preemptible) — 60–91% off, 24h max, different from AWS Spot | [AI] |
| **Why you cannot compare a discounted AWS price to a list GCP price** | [ME] |
| Your OD-vs-OD decision: what it means, what it hides, how to defend it | [ME] |

**The thing to be able to say out loud:** *"AWS and GCP commitment models aren't
comparable line-for-line. AWS SP/RI discounts attach to usage; GCP CUDs are a
separate resource commitment with per-service rates, and SUD applies
automatically with no commitment at all. So we compare on-demand to on-demand
as the headline, and show CUD columns separately."*

---

# TRACK 2 — AWS services: the billing dimensions

You don't need AWS architecture depth. You need **what each service charges
for**, because that's what a bill line is. For each service below, learn: the
billable units, the free tier, and the one dimension people forget.

## Tier 1 — these dominate every bill you have

| service | what to know | tag |
|---|---|---|
| **EC2** | instance-hours by family/size/OS; what's bundled (vCPU, RAM, network, sometimes local disk); **OS licence premium is inside the hourly rate**; families: general (m), compute (c), memory (r/x/z), storage-dense (d/h/i), accelerated (p/g/inf/trn), burstable (t) | [AI] |
| **EBS** | gp2 vs gp3 (and why gp3 bills IOPS separately), io1/io2, st1/sc1, snapshots billed on changed blocks | [AI] |
| **S3** | storage classes and their **minimum durations** (Standard-IA 30d, Glacier Flexible 90d, Deep Archive 180d), request pricing per 1,000, retrieval fees, Intelligent-Tiering monitoring fee | [AI] |
| **RDS / Aurora** | instance-hours, Multi-AZ doubling, storage + IOPS, backup storage, Aurora's **I/O charges**, Aurora Serverless v2 **ACU** | [AI] |
| **Data transfer** | the one everyone gets wrong: internet egress tiers, inter-AZ **both directions**, inter-region, NAT gateway data processing — this is often the biggest migration surprise | [AI] |
| **Lambda** | GB-seconds (memory × time, and **CPU is proportional to memory** — 1 vCPU per 1,769 MB), requests, ephemeral storage, provisioned concurrency | [AI] |

## Tier 2 — present in your corpus, worth real study

ELB (ALB/NLB/CLB + LCU), ECS/Fargate (vCPU-hr + GB-hr), EKS (cluster fee),
ElastiCache, OpenSearch, MSK, Redshift (node-hours vs RA3 + managed storage),
Athena (TB scanned), Glue (DPU-hours), EMR (instance fee on top of EC2),
Kinesis (shard-hours vs on-demand), CloudWatch (ingest/storage/metrics/alarms/
dashboards), DynamoDB (RCU/WCU vs on-demand), SageMaker. **[AI]**

## Tier 3 — know they exist and roughly how they bill

Route 53, CloudFront, WAF, Shield Advanced, GuardDuty, Secrets Manager, KMS,
SNS/SQS, Step Functions, API Gateway, Transfer Family, DMS, Backup. **[AI]**

> **[ME]** — which of these actually appear in your 11-bill corpus and at what
> spend. That tells you where to spend study time. I can produce that ranking
> from the corpus any time.

---

# TRACK 3 — GCP services: the counterpart and its billing shape

Same discipline: what does it charge for, and how is that shape *different*.

| GCP service | the shape, and where it differs from AWS | tag |
|---|---|---|
| **Compute Engine** | machine families (N4/N4D, C4/C4D, C4A Arm, E2, M-series); **custom machine types** — a thing AWS doesn't have; **vCPU and RAM billed separately**, which is why your mapper emits core+ram components; sole-tenancy; GPUs attached separately from the VM | [AI] |
| **Persistent Disk / Hyperdisk** | pd-standard/balanced/ssd/extreme, Hyperdisk Balanced/Throughput/Extreme; **Local SSD is flash-only — GCP has no dense-HDD instance tier** | [AI] |
| **Cloud Storage** | Standard/Nearline/Coldline/Archive, minimum durations (30/90/365d), **Class A vs Class B operations** priced per 10,000 — not per 1,000 like S3 | [AI] |
| **Cloud SQL** | vCPU + RAM + storage billed separately; Enterprise vs Enterprise Plus; HA doubling | [AI] |
| **Cloud Run / Cloud Run functions** | request-based vs instance-based billing; **memory GiB-s and vCPU-s on separate SKUs** — the structural difference from Lambda's single GB-second | [AI] |
| **GKE** | Autopilot (pod CPU/memory requests) vs Standard (cluster fee + GCE nodes) | [AI] |
| **Networking** | egress by tier (Premium/Standard) and destination; **Cloud NAT bills per-VM-hour, not per-gateway**; forwarding rules + data processing for LB | [AI] |
| **BigQuery** | on-demand $/TB scanned vs slot-based (editions); active vs long-term storage | [AI] |
| Memorystore, Dataproc, Dataflow, Pub/Sub, Cloud Armor, Vertex AI, Cloud Logging/Monitoring, Secret Manager, Cloud KMS, Cloud DNS, Cloud CDN, SCC | know the billing unit for each | [AI] |

> **[ME]** — your project's family defaults (N4D/C4D/C4A rather than legacy
> N2D/C2D) and *why* that choice was made. That's a decision in your repo, not
> in Google's docs.

---

# TRACK 4 — The mapping discipline (this is the actual expertise)

Everything above is lookup-able. **This is what makes you the expert**, and
it's where your manager's "be a cloud cost expert" really points.

## 4.1 The four kinds of mapping decision

Learn to name which one you're making — your product already encodes this:

1. **SKU** — a deterministic GCP SKU × a multiplier (EC2 → Compute Engine core+ram)
2. **FORMULA** — a derived conversion (Redshift node-hours → BQ slot-hours)
3. **PASSTHROUGH** — carry the AWS cost, label the GCP service, because the
   unit models don't convert (S3 requests per-1k vs GCS ops per-10k)
4. **IGNORE** — $0 with a stated reason (AWS charges for something GCP gives free)

**[ME]** — your `data/pricing_matrix.json` is exactly this, per service ×
charge type. Read it; it's the single densest artifact of this knowledge you
have.

## 4.2 Unit-model mismatches — the bug class that costs real money

| mismatch | real example | tag |
|---|---|---|
| different quantum | S3 per 1,000 requests vs GCS per 10,000 ops → 10x if unnoticed | [ME] |
| GB vs GiB | 1.074 difference, compounds on storage | [AI] |
| bundled vs unbundled | Lambda GB-second bundles CPU; Cloud Run bills CPU separately → ~6x under-projection if you only map memory | [ME] |
| per-resource vs per-consumer | NAT gateway-hour vs Cloud NAT per-VM-hour | [ME] |
| included vs charged | EC2 dense-storage instances bundle 224 TB of HDD; GCP has no equivalent tier | [ME] |

## 4.3 Equivalence is a judgement, and it's scoped

The thing nobody can look up. Learn to reason like this:

> *"Glacier Flexible and Coldline both have a 90-day minimum, so for
> **storage-time** they're a fair match. Their **retrieval** pricing is not
> comparable, so that's a separate decision."*

Equivalence is **scoped to a charge type**, and "not equivalent" is a valid,
useful answer. Standard vocabulary for this exists — healthcare calls it a
**crosswalk**, with degrees (equal / equivalent / wider / narrower / inexact /
unmatched / disjoint). Using that vocabulary signals expertise. **[ME]** — the
design doc for this is already in your repo.

## 4.4 Where there is genuinely no equivalent

Be able to say this confidently, because saying "it maps to X" when it doesn't
is how you lose credibility:

Inferentia/Trainium (TPU is an analogue, not a drop-in) · managed SFTP ·
DynamoDB's exact model · Kinesis reservation model · SES · dense-HDD storage
instances. **[AI]** for the facts, **[ME]** for what your product currently
decides.

---

# TRACK 5 — Billing data (lighter than you'd think, but non-zero)

You're right that this matters less than service knowledge. But these specific
things will come up:

| topic | tag |
|---|---|
| CUR columns that matter: `lineItem/LineItemType`, `UnblendedCost`, **`pricing/publicOnDemandCost`**, `UsageType`, `Operation`, `product/*` | [AI] |
| Line item types: `Usage`, `DiscountedUsage`, `SavingsPlanCoveredUsage`, `SavingsPlanRecurringFee`, `RIFee`, `Credit`, `Tax` — and which count as workload | [ME] |
| Why `publicOnDemandCost` is the key to OD-vs-OD, and what you do when the bill format doesn't have it | [ME] |
| **FOCUS** (FinOps Foundation open billing spec) — `BillingPeriodStart` mandatory, `ChargeCategory`, `ChargeFrequency`. The industry vocabulary. | [AI] |
| GCP billing export schema (BigQuery) — the other side, if you ever validate against a real GCP bill | [AI] |

---

# TRACK 6 — FinOps framing (how to sound like you belong)

| topic | tag |
|---|---|
| FinOps Foundation framework: Inform / Optimize / Operate | [AI] |
| Unit economics — cost per customer, per transaction | [AI] |
| Migration cost drivers people forget: egress during migration, dual-running, licence re-purchase, re-architecture vs lift-and-shift | [AI] |
| Why a projection is a **range**, not a number, and how to present assumptions as a sensitivity | [ME] |
| TCO vs run-rate — what you're actually producing | [AI] |

---

# What to ask me specifically

These have no public answer — they're in your code, your corpus, or your
decisions:

1. **Rank the services in your corpus by spend** so you study the right ones.
2. **Walk through `pricing_matrix.json`** — each row is a decision you should
   be able to defend.
3. **The discounted-row problem** — why Spot rows currently project at 10.8x,
   and the three ways to fix it.
4. **Your accuracy numbers** — what the corpus says today, and how to phrase
   that honestly to a customer.
5. **Where your product is currently wrong** — OS licensing, NAT per-VM, the
   LLM-routed 5%. Knowing your own gaps is the strongest signal of expertise.
6. **Any specific mapping** — "why does X map to Y here?" I can trace it
   through the code and tell you the real reason, not a plausible one.

---

# Suggested order

**Week 1** — Track 1 end to end (discounts). Then Tier 1 of Track 2 (EC2, EBS,
S3, RDS, data transfer, Lambda) and their Track 3 counterparts side by side.
Don't learn AWS and GCP separately; learn them **in pairs**.

**Week 2** — Track 4 entirely. Read `pricing_matrix.json` row by row and ask
me about every one you can't justify.

**Week 3** — Track 2 Tier 2, Track 5, Track 6. By now you'll know which
services actually matter in your bills.

**Ongoing** — every time the pipeline produces a number you can't explain,
that's a lesson. Chase it. That's how most of what I know about your system
got found.
