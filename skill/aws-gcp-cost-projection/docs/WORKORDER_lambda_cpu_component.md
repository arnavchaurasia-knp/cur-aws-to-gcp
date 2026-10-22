# WORK ORDER — Lambda GB-seconds are priced for memory only; the CPU half is missing

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: a new static mapper for AWS Lambda in
`scripts/apply_static_mappings.py`.** You are not changing ingest, the CUR
path, or any other service. Lambda projections will **rise** — by roughly 6x on
Lambda compute. Nothing else may move.

---

## 1. The problem in plain English

AWS Lambda bills one metered unit for compute: the **GB-second**. That single
number pays for both the memory *and* the CPU your function was given — AWS
allocates one vCPU for every 1,769 MB of configured memory, and bundles the
cost.

Google Cloud Run functions bills the two **separately**: GiB-seconds of memory
on one SKU, vCPU-seconds on another.

**We only price the memory one.** The CPU side of the workload is projected at
zero.

### Measured on the corpus

One real Lambda line, 25,455,842 GB-seconds:

| | |
|---|---|
| projected today (memory SKU only) | **$89.10** |
| memory, correct | $89.10 |
| CPU, currently missing | **$483.50** |
| **correct total** | **$572.60** |

A **6.4x under-projection**, and it is in the dangerous direction: too little
GCP cost makes the migration look cheaper than it is.

Across the corpus Lambda is the single largest service-level gap:

```
service      jobs  rows      aws $      gcp $   ratio
lambda          3     5     13,514        204   0.02x      <-- largest gap
ec2            11   390     66,931     64,445   0.96x
rds            10    55     13,468     12,221   0.91x
s3              9    49      9,713      9,130   0.94x
```

> **Do not use that 0.02x as your success metric.** The AWS side of those two
> bills is itself inflated by a separate defect — one source line priced 1000x
> its siblings, tracked in `WORKORDER_unit_price_outlier.md`. Judge this work
> by the **GCP** figure moving from $89.10 to ~$572.60 on that row, not by the
> ratio.

### Why it happens

**There is no Lambda mapper.** `grep "def map_lambda" scripts/apply_static_mappings.py`
returns nothing. Lambda rows fall to the generic `per_request` mechanic
(`mechanic_group = per_request`), which resolves one SKU and applies
`unit_multiplier = 1.0`.

You can see it in the output: a **GB-second** row comes out tagged
`component = "requests"`, priced against the Cloud Run **Memory** SKU. One
generic mechanic, one SKU, one multiplier — and a unit it was never designed
for.

`data/service_specs.json` makes this likely by putting GB-seconds and requests
in **one** charge type (`lambda_metered`, `unit_in: ["lambda-gb-second",
"requests", "count"]`). Two different units in one bucket cannot be priced by
one rule.

`data/service_map.json`'s Lambda rule meanwhile claims the charge is "Priced
deterministically against real Cloud Run Functions rates". That is not true
today. Fix the sentence as part of this work (T5).

---

## 2. The arithmetic

AWS allocates **1 vCPU per 1,769 MB** of Lambda memory
(https://docs.aws.amazon.com/lambda/latest/dg/configuration-memory.html). So
for a function billed `G` GB-seconds:

```
memory component  = G                     GiB-seconds
cpu component     = G / 1.769             vCPU-seconds
```

Worked on the real row, in `asia-south2`:

```
memory : 25,455,842        x $3.5e-06  = $ 89.10
cpu    : 25,455,842/1.769  x $3.36e-05 = $483.50
                                   total $572.60
```

`1769` is an assumption and must be editable, not hardcoded. Add it to
`data/assumption-defaults.json` and the Go seed in `internal/db/db.go`
alongside the existing ones:

```
lambda_mb_per_vcpu   default 1769.0   min 512   max 10240   unit "MB"
```

Read it the way the existing constants do
(`_ASSUMPTIONS.get("lambda_mb_per_vcpu", 1769.0)`). There is a ratchet test
asserting every declared assumption is referenced by a script, so declaring it
without using it fails the build.

---

## 3. The SKU trap — read before resolving anything

The catalog contains **four** families whose descriptions all contain
"Cloud Run functions" and "CPU (Request-based billing)":

```
Cloud Run functions CPU (Request-based billing)                     s   2.4e-05  <-- want (tier 1)
Cloud Run functions CPU (Request-based billing)                     s   3.36e-05 <-- want (tier 2)
Cloud Run functions Min-Instance CPU (Request-based billing)        s   2.5e-06  <-- NOT this
Cloud Run functions (1st Gen) CPU (Request-based billing)           s   1e-05    <-- NOT this
Cloud Run functions (1st Gen) CPU Tier 2 (Request-based billing)    s   1.4e-05  <-- NOT this
```

Two traps:

1. **`Min-Instance` and `(1st Gen)` must not match.** A loose word-overlap
   resolve will take one — this is the same failure class as the Glacier
   120x and the `_strict_resolve_sku` work that followed it. Pin the
   descriptions with anchored patterns that exclude `Min-Instance`,
   `Min Instance` and `1st Gen`.
2. **The same description exists at two rate tiers, by region** — memory
   $2.5e-06 / $3.5e-06, CPU $2.4e-05 / $3.36e-05. **Resolve the CPU and memory
   SKUs in the same region**, so a tier-1 CPU rate is never paired with a
   tier-2 memory rate. Use the row's resolved `gcp_region` for both, exactly
   once, and pass it to both lookups.

Use `_strict_resolve_sku` (as the compute path does), not the lenient resolver.

---

## 4. Tasks

### T1 — `map_lambda()`

Add a mapper beside the other service mappers in
`scripts/apply_static_mappings.py`. Model its structure on an existing
multi-component mapper — `map_gce_row`'s core/ram split is the closest shape.

For a row whose `pricing_unit` is `Lambda-GB-Second` (match the unit, not the
usage-type text — the usage type carries region prefixes and an `-ARM` suffix):

```python
components = [
    ("memory", mem_sku, 1.0,                      "Cloud Run functions Memory (Request-based billing)"),
    ("cpu",    cpu_sku, 1.0 / _LAMBDA_MB_PER_VCPU_GB, "Cloud Run functions CPU (Request-based billing)"),
]
```

where the CPU multiplier is `1 / (lambda_mb_per_vcpu / 1024)` = `1/1.7275` for
the default 1769 MB. **Compute it from the assumption; do not write 0.5653.**

Set `strategy = "map"` when the SKU resolves and `"passthrough"` when it does
not, following the convention of the surrounding mappers. Carry a note naming
the assumption, so the row explains itself:

> `Lambda GB-seconds -> Cloud Run functions memory (GiB-s) + CPU (vCPU-s). AWS bundles CPU into the GB-second at 1 vCPU per 1769 MB; GCP bills them separately, so one AWS row becomes two GCP components.`

### T2 — Requests rows

A Lambda row whose unit is `requests`/`count` is an invocation count, not
compute. Map it to the **`Cloud Run Functions (1st Gen) Invocations`** SKU
family — confirm the exact current description in `data/catalog.duckdb` before
using it, and if no clean non-`Min-Instance` invocations SKU exists for the
region, make it a `passthrough` with a stated reason rather than forcing a
match.

### T3 — Do not touch ephemeral storage here

Lambda `GB-Mo` rows (ephemeral storage) currently fall through to the generic
mechanic too, and one lands at $0.22 against $1.17 of AWS. **Leave them.**
Deciding whether Cloud Run's ephemeral storage is chargeable at all is its own
question; `service_specs.json` already documents that these fall to misc
deliberately. Note it in your summary.

### T4 — Claim the rows before the generic mechanic

`map_lambda` only runs if Lambda rows reach it. Today they are claimed by
`per_request`. Follow the pattern the other static mappers use to take their
rows (see how `map_shield` / `map_flat_hourly` rows are selected and how
`apply_static_mappings` partitions the manifest), and confirm with a run that
the Lambda row's `component` is now `memory`/`cpu` and not `requests`.

### T5 — Correct the claim in `data/service_map.json`

The Lambda rule's `reason` says it is "Priced deterministically against real
Cloud Run Functions rates where the bill format permits". After T1 that becomes
true for GB-seconds; make sure the sentence reflects what the code does, and
says that the CPU component is derived from the memory:vCPU ratio.

### T6 — Tests

In `tests/test_static_mappers.py`:

1. `test_lambda_gb_seconds_emits_memory_and_cpu` — one GB-second row yields
   **two** components, `memory` and `cpu`, not one.
2. `test_lambda_cpu_multiplier_from_assumption` — the cpu multiplier equals
   `1/(lambda_mb_per_vcpu/1024)`; changing the assumption changes it.
3. `test_lambda_skus_exclude_min_instance_and_1st_gen` — the resolved
   descriptions contain neither `Min-Instance`/`Min Instance` nor `1st Gen`.
   **This is the regression that matters most** (§3).
4. `test_lambda_cpu_and_memory_same_region` — both components resolve in the
   same region, so their rate tiers match.
5. `test_lambda_requests_not_priced_as_compute` — a `requests` row does not
   produce `memory`/`cpu` components.

---

## 5. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_static_mappings.py
```

- Record the test count first; confirm it rises by exactly 5.
- On a bill with a `Lambda-GB-Second` row, that row's GCP total rises ~6.4x
  (the reference row: **$89.10 -> ~$572.60**), and it now has `memory` and
  `cpu` components.
- **No non-Lambda service's total moves at all.** Rebuild the corpus and
  compare the per-service table; `ec2`, `rds`, `s3` and the rest must be
  unchanged to the dollar. A static mapper that claims rows it should not is
  the main risk here.
- Check the resolved SKU descriptions in the output — literally read them —
  and confirm neither is a `Min-Instance` or `1st Gen` variant.

---

## 6. Do NOT

- Do **not** hardcode `1769`, `1.769` or `0.5653`. It is an assumption with an
  admin surface (§2).
- Do **not** resolve the CPU and memory SKUs independently of region, or pair
  rates from different tiers (§3).
- Do **not** use the lenient SKU resolver. `Min-Instance` and `1st Gen`
  variants are waiting for it.
- Do **not** touch Lambda ephemeral-storage `GB-Mo` rows (T3).
- Do **not** chase the corpus `0.02x` ratio to 1.0. The AWS side of those bills
  is separately inflated; the GCP figure is your target (§1).
- Do **not** extend this to other services that the generic mechanics
  over-claim. That is a known, larger problem and is out of scope here.

---

## 7. Out of scope

**The generic-mechanic over-claim in general.** Lambda is one instance of
`per_request` (and friends) claiming rows whose units they cannot price. The
same shape affects other services and is tracked separately. Fix Lambda here —
it is the largest single gap — and do not generalise the mechanism in this
work order.

**Cloud Run instance-based billing.** Lambda is request-billed, so the
request-based SKUs are the right family. Instance-based billing is a different
product model and a separate question.

---

## 8. Done

- [ ] `map_lambda()` emits `memory` and `cpu` components for GB-second rows.
- [ ] CPU multiplier derived from a new `lambda_mb_per_vcpu` assumption, which
      is declared in both the JSON defaults and the Go seed and read by code.
- [ ] Both SKUs resolved strictly, in the same region, excluding
      `Min-Instance` and `1st Gen`.
- [ ] Lambda rows no longer carry `component = "requests"` for compute.
- [ ] `service_map.json`'s Lambda reason matches what the code now does.
- [ ] 5 new tests pass; full suite passes at your recorded baseline + 5.
- [ ] Reference row goes $89.10 -> ~$572.60; **no other service moves**.
