# ANALYSIS — Spec-dispatch validation against real bills

> **Status update (same day).** The ambiguity and RDS findings below have since
> been FIXED: EC2 `instance_hour` is now a nested group with disjoint siblings,
> RDS `Multi-AZUsage` is classified, and WAF/RDS overlaps are gone. Re-measured
> on the same corpus: **genuine ambiguity NONE** (1,385 spec-covered rows),
> `spec_gap` $6,869 → $3,819, `agree` unchanged at 95.3%. The remaining work is
> tracked in `WORKORDER_spec_coverage_completion.md`. The numbers in §2–§4 below
> are the ORIGINAL measurements, kept as the record of why the change was made.

Run date: 2026-10-16. Reproduce with:

```bash
python scripts/spec_parity.py <jobs-root>
```

Corpus: the 15 files in `Downloads/Facets`. 12 ingested successfully
(4 PDF, 5 XLSX, 4 CSV — 1 of which is raw CUR); 3 correctly rejected
(RVTools vSphere inventory, an Azure plan, one unparsed CSV).
**1,457 workload rows, 54 service specs.**

---

## 1. Headline: the engine is sound, the specs are faithful-not-correct

```
bucket          rows           spend   % spend
agree           1295         511,448     95.3%
differ            58           3,201      0.6%
spec_gap          32           6,869      1.3%
no_spec           72          15,112      2.8%
```

`spec_dispatch` reproduces the legacy classifier on **95.3% of spend** across
every bill format, with no further work. Disagreements are 4 patterns totalling
$3,201.

**But parity is not correctness.** The specs were written to reproduce
`classify_mechanics` — their own fields say so (`"source_rule": "non_workload
(CPUCredits branch)"`, `"Faithful label = what classify_mechanics returns
today"`). 40 of 54 services route at least one charge type to a generic
mechanic:

```
fargate    fargate_vcpu      -> per_request
ecs        ecs_fargate_*     -> per_request
kms        kms_keys          -> misc
cloudtrail cloudtrail_events -> per_request
```

Those are the known-wrong routings from the per-SKU matrix. **Cutting over to
spec dispatch as-is changes accuracy by zero.** Its value is structural: one
place per service to fix, testable in isolation, no global ordering.

---

## 2. The dominant defect: sibling ambiguity (352 rows, ~$53k)

Rows where more than one charge type matched. First-match-wins silently picks
by list position.

| service | colliding charge types | rows | spend |
|---|---|---:|---:|
| ec2 | `instance_hour_graviton` + `instance_hour` | 189 | $44,679 |
| ec2 | `instance_hour_burstable` + `instance_hour` | 78 | $6,873 |
| ec2 | `instance_hour_burstable` + `instance_hour_graviton` + `instance_hour` | 60 | $1,489 |
| s3 | **`s3_storage` + `s3_requests`** | 14 | $6,179 |
| vpc | `vpc_tgw_hours` + `vpc_other` | 8 | $158 |
| ecs | `ecs_fargate_any` + `ecs_other` | 6 | $263 |
| ec2 | `instance_hour_windows` + `instance_hour` | 5 | $681 |
| s3 | **`s3_data_transfer` + `s3_requests`** | 4 | $1,346 |
| kms | `kms_requests` + `kms_other` | 4 | $123 |
| bedrock | `bedrock_tokens` + `bedrock_other` | 4 | $158 |
| waf | **`waf_fixed_hourly` + `waf_monthly`** | 3 | $15 |
| rds | **`backup` + `storage`** | 2 | $372 |
| opensearch | **`opensearch_storage` + `opensearch_compute`** | 2 | $182 |

They split into two kinds, and the distinction decides the architecture.

**Kind A — specific + general.** `instance_hour_graviton` + `instance_hour`,
`vpc_tgw_hours` + `vpc_other`, `kms_requests` + `kms_other`. The specific
variant and the catch-all both match. The outcome is *correct*, but only
because the specific one is listed first — correct by convention, not by
construction.

**Kind B — genuine ambiguity, ~$8,100.** A row cannot be both S3 storage and
an S3 request, or both an RDS backup and RDS storage. These are real defects
whose resolution is currently decided by list position, silently.

`instance_hour_burstable` + `instance_hour_graviton` (60 rows) is the
interesting case: **t4g is both burstable and Graviton.** The answer (burstable
wins, → E2) is right, but it is right by accident of ordering rather than by a
recorded decision.

---

## 3. Nested classification: validated by the collision data

The collision table *is* the argument for a two-level model. Every Kind A
collision is a parent/child relationship written as flat siblings.

```
ec2
├── instance_hour            parent: is this an instance-hour row?
│   ├── windows              OS = Windows
│   ├── burstable            family = t*
│   ├── graviton             arch = arm64
│   └── standard             default — makes the set exhaustive
├── storage
│   ├── ebs_volume
│   ├── snapshot
│   └── iops
└── data_transfer
```

Two invariants the nested form makes **testable**, which flat cannot:

1. **Siblings partition their parent.** For any row matching a parent, exactly
   one child matches. Kind B collisions become impossible to write without a
   test failing.
2. **Children are exhaustive.** The last child is a declared default, so
   fall-through is a decision with a name, not an accident.

Kind A collisions disappear structurally: a child cannot collide with its
parent because it is evaluated *within* it.

It also forces the t4g question into the open. In a tree you must declare the
discriminator order (OS → family class → arch), so "t4g is burstable, not
Graviton" becomes a recorded decision instead of a side effect of line order.

**Verdict: adopt the nested model.** It is not added complexity — it is the
structure the data already has, currently flattened.

---

## 4. Other defects found on real bills

| Finding | Evidence | Impact |
|---|---|---|
| **PDF bills are stamped `flat_csv`** | all 4 PDFs report `billing_format='flat_csv'` | confirms the B4 defect: the format that breaks field extraction has no identity |
| **RDS `Multi-AZUsage` matches no charge type** | `APS5-Multi-AZUsage:db.t4g.large`, `db.m7g.large`, `db.m7g.xl` | $5,567 here; Multi-AZ instance-hours are a major RDS charge type simply missing from the spec |
| **26 rows have no `canonical_service` at all** | bucket `?`, $1,019 | Layer-1 identity failures on real bills |
| **`ebs` resolves as its own canonical, no spec** | $4,790, 18 rows | EBS is being treated as a service separate from `ec2` |
| **`aurora` separate from `rds`, no spec** | $52, 2 rows | same class |
| **Non-standard region prefixes** | `AP-SOUTHEAST-1-Usage:NA` (elb, msk) | full region name instead of the `APS1` code — the usage-type normalizer assumes the short form |
| **Empty / numeric usage types** | `''`, `GLOBAL-Usage:671.49`, `GLOBAL-Usage:802.39` | PDF-derived rows carrying a number where a charge type belongs |

---

## 5. Refuted: generating specs from the AWS Price List index

Measured against the live index (272 services):

| service | published charge types | present in bills |
|---|---:|---:|
| S3 | 169 | 5 |
| CloudWatch | 110 | 11 |
| Lambda | 39 | 1 |
| GuardDuty | 119 | — |

The published taxonomy is **10–30× larger** than what bills contain. Generating
specs from it produces files that are ~95% dead weight and *more* human
grouping work than writing 5–10 entries by hand.

Clustering quality is also unreliable: Lambda collapsed 423 raw usage types to
39, but CloudWatch went 84 → 110 (the `(usagetype, operation, unit)` key splits
where it should merge). Two normalizer bugs surfaced in 20 minutes of trying:
cross-region usage types carry two region tokens (`APS3-APN1-S3RTC-Out-Bytes`),
and `EBS:VolumeUsage.gp3` was mangled by the instance-type regex.

Practicality: EC2's single-region offer file is **314 MB**; every other service
measured is under 20 MB, most under 2 MB.

**Keep the index as a periodic audit** — "which published charge types does no
spec cover?" — not as a generator.

---

## 6. Recommended architecture

```
ingest          facts
aws_normalizer  identity    product_code -> canonical            done
spec_dispatch   routing     canonical -> group -> charge_type    NESTED (change)
mappers         pricing     mechanic -> SKU
row_state       decisions   declared fields                      done
render_notes    prose       generated                            done
```

Engine changes, each justified by a measurement above:

1. **Nested charge types**, siblings partitioning their parent. *§3 — 352 ambiguous rows.*
2. **Partition test per service** — no row matches two siblings; every row matching a parent matches exactly one child. *Makes §2 Kind B impossible.*
3. **Declared default child** at each level. *Fall-through becomes named.*
4. **`spec_gap` ≠ `no_spec`** — both return `None` today. *§4 — this is how RDS Multi-AZ was found.*
5. **Predicate lint** — service identity must not appear in a predicate inside its own spec file.

Cutover stays **per service**: `dispatch()` already returns `None` for
undeclared services, so a verified service owns its rows while the rest remain
on the legacy chain.

---

## 7. Work queue, in measured order

1. Fix the 5 Kind B ambiguities — S3 ($7,525), RDS, OpenSearch, WAF. *Real misclassification today.*
2. Convert `ec2` and `s3` specs to nested; prove the partition test catches the collisions.
3. Add the missing RDS `Multi-AZUsage` charge type ($5,567).
4. Resolve the 26 `?`-canonical rows and the `ebs` / `aurora` canonical split.
5. Give PDF its own `billing_format`.
6. Then — and only then — revisit the 40 services routing to generic mechanics.
   That is the accuracy work; everything above is the groundwork that makes it
   safe.
