# WORK ORDER — Six small corrections, each with a known cause

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: four independent small fixes.** No design decisions are needed — each
cause is identified below. They are batched because each is a few lines, not
because they interact. **Do them in any order; none depends on another.**

Expected dollar movement: **only item 2**, and only on the affected rows
(~$2,963 of instance spend gains vCPU/RAM specs, which feeds sizing and the
capacity check). Items 1, 3 and 4 move nothing.

---

## 1. An all-zero bill fails with a message blaming the wrong thing

`scripts/ingest.py`

A bill whose rows are all $0 — a free-tier-only account, or a period with no
chargeable usage — fails with:

> `Empty bill: no line items were extracted into the catalog. The input parsed
> but produced zero priceable rows (all rows were tax/blank, or the layout was
> not recognized). Check the input file.`

The layout was recognised and the file parsed. The real cause is one line
earlier in the same log:

```
Materiality filter: dropped 1 zero-cost rows + 0 low-materiality rows
```

**The materiality filter deleted every row because each cost $0.** The user is
told their file is malformed when it is fine.

**Fix:** when the catalog is empty, distinguish the cases before writing
`failure.txt`. If rows were parsed and then removed by the materiality filter,
say so:

> `Bill parsed successfully but every line item was $0, so nothing remains to
> project. This usually means a free-tier-only account or a period with no
> chargeable usage.`

Keep the existing message for the genuine "nothing parsed" case. The
distinction is available: count rows before and after the filter.

**Do not** change the filter or make the run succeed. Failing is the right
behaviour; only the explanation is wrong.

---

## 2. Six instance types never resolve their specs — four distinct causes

`scripts/ingest.py`, the spec-enrichment loop (~line 2190)

Excluding the synthetic fixture `job2`, these rows carry an `instance_type` but
no `instance_vcpus`/`instance_ram_gb` — **17 rows, $2,963**:

| type | rows | AWS $ | cause |
|---|---:|---:|---|
| `kafka.storage.gp2` | 7 | 1,561 | **not an instance at all** |
| `db.m7g.xl` | 4 | 1,140 | truncated name |
| `inf1.xlarge` | 2 | 119 | lookup never attempted |
| `cache.r7g.large` | 2 | 112 | prefix not stripped |
| `c7i-flex.large/.xlarge` | 2 | 31 | lookup never attempted |

### 2a — `kafka.storage.gp2` is storage, not an instance

```
product=Amazon Managed Streaming...  usage_type=APS5-Kafka.Storage.GP2  operation=RunVolume
```

The instance-type extractor pulled `kafka.storage.gp2` out of a **storage**
usage type. There is no such instance; MSK broker storage is an EBS volume.

**Fix:** do not set `instance_type` when the usage type is a storage meter
(`...Storage...`, operation `RunVolume`). Leave it NULL so the row is treated
as storage, which it is.

### 2b — `db.m7g.xl` is a truncated `db.m7g.xlarge`

```
usage_type = APS5-Multi-AZUsage:db.m7g.xl
```

AWS abbreviates the size in some CUR/invoice renderings. Stripping `db.` gives
`m7g.xl`, which is not in the table; `m7g.xlarge` is.

**Fix:** when a stripped base is not found and it ends in a known abbreviated
size, expand it (`xl` → `xlarge`, and check whether `2xl`-style forms also
appear) and retry once. If still not found, leave unresolved — do not guess a
different size.

### 2c — `cache.`/`db.` prefixes are not stripped before the EC2 fallback

The code already strips `.search` and `kafka.`:

```python
if base_itype.endswith(".search"): base_itype = base_itype[:-len(".search")]
if base_itype.startswith("kafka."): base_itype = base_itype[len("kafka."):]
table = rds_table if base_itype.startswith(("db.", "cache.")) else ec2_table
spec  = table.get(base_itype) or ec2_table.get(base_itype)
```

For `cache.r7g.large`, `rds_table` misses it and the EC2 fallback is tried with
the **full prefixed name**, which EC2 also misses. The base `r7g.large` **is**
in the EC2 table.

**Fix:** strip `db.`/`cache.` for the EC2 fallback specifically, after the RDS
lookup fails. Keep the RDS lookup on the prefixed name — that table is keyed
that way.

### 2d — `inf1.xlarge` and `c7i-flex.*`: the lookup is never attempted

Both **are** in `data/ec2-instance-types.json` with full specs:

```
inf1.xlarge     -> {"vcpus": 4, "ram_gb": 8.0, ...}
c7i-flex.large  -> {"vcpus": 2, "ram_gb": 4.0, ...}
```

Every affected row is a **Spot** row whose `usage_type` is blank and whose type
appears only in the operation text (`inf1.xlarge Linux/UNIX Spot
Instance-hour`). `instance_type` **is** populated correctly on the row — so the
extraction worked and the spec lookup did not run for it.

**Investigate before fixing:** find why a row with a populated
`instance_type` reaches the end of the enrichment loop without a spec lookup.
The likely shape is that the lookup sits inside a branch keyed on usage-type or
operation patterns that the Spot wording does not match.

**The fix to aim for:** once `instance_type` is populated, always attempt the
spec lookup, regardless of which parsing path produced it. Report in your
summary what the actual gate was.

### 2e — Tests

1. `test_kafka_storage_not_an_instance_type` — a `Kafka.Storage.GP2` row has
   `instance_type IS NULL`.
2. `test_abbreviated_size_resolves` — `db.m7g.xl` resolves to the
   `m7g.xlarge` spec.
3. `test_cache_prefix_falls_back_to_ec2` — `cache.r7g.large` resolves via
   `r7g.large`.
4. `test_spot_row_resolves_specs` — a Spot row whose type comes from the
   operation text gets vCPU/RAM.
5. `test_unknown_size_not_guessed` — an abbreviation with no valid expansion
   stays unresolved rather than matching a different size (2b).

---

## 3. A Lambda test cannot fail

`tests/test_static_mappers.py`, `test_lambda_skus_exclude_min_instance_and_1st_gen`

```python
sku_name = r.get("gcp_sku_name") or ""
assert "min-instance" not in sku_name.lower()
```

`map_lambda` writes `gcp_sku_name` as a **hardcoded literal** —
`"Cloud Run functions CPU (Request-based billing)"` — regardless of what
`_strict_resolve_sku` returned. The assertion therefore tests a constant and
can never fail, even if the resolver started returning a `Min-Instance` SKU.

The behaviour is correct today (I checked the patterns against the catalog: 41
SKUs matched per pattern, one family each, zero leakage). The **guard** is
hollow.

**Fix:** look up the resolved `gcp_sku_id` in `data/catalog.duckdb` and assert
on that row's real `description`. Skip cleanly if the catalog is unavailable in
the test environment — but then the test must **report that it skipped**, not
silently pass.

---

## 4. Projection coverage is computed and never shown

`scripts/render_report.py` (~line 743)

`mapping_coverage.json` is written every run and read by nothing. A reader
cannot tell whether a projection covers 99% of the bill or 45%.

**Fix:** surface it as one line in the existing data-gap section — do **not**
add a new section or card:

> `Priced 98.0% of workload spend; the remainder is listed below.`

Use the same spend-weighted basis the file already uses. If coverage is 100%,
say so rather than omitting the line — its absence should never be ambiguous.

---

## 5. A report built from zero workload rows renders anyway

`scripts/render_report.py`

If classification marks **every** row non-workload, the pipeline still produces
a full report: an empty GCP side, an AWS figure built from nothing, and a
savings number derived from both.

The realistic path to this is **not** an odd input — it is an unrecognised bill
format. Real usage, real spend, but the `is_workload` rules match nothing, so
the customer gets a confident-looking page built from zero rows with no
indication anything went wrong.

**Fix:** one check after classification, which does not care *why* the count is
zero:

```python
if workload_row_count == 0:
    banner("No rows were classified as workload — nothing to project. "
           "This usually means an unrecognised bill format.")
```

Render the banner in place of the comparison table. Do **not** fail the run —
the catalog and bill total are still valid and worth showing.

This is insurance against a classification failure, not a feature for unusual
bills. Keep it to the banner; do not build handling for degenerate inputs
beyond it.

---

## 6. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/ingest.py scripts/render_report.py
```

- Record the test count first; confirm it rises by exactly 5 (item 2e), plus
  any you add for items 1, 3 and 4.
- **Items 1, 3 and 4 move no dollars.**
- **Item 2 does:** rows gaining specs now contribute real vCPU/RAM to sizing
  and to the capacity check. Re-run a bill and confirm the unresolved count
  falls from 17 rows / $2,963 toward zero, excluding `job2` (a synthetic
  fixture full of instance types that do not exist — `c1.12xlarge`,
  `p4d.large`, `x8aedz.*`; **its unresolved rows are correct and must stay
  unresolved**).
- No GCP projected total should change from item 2 alone; specs feed sizing and
  capacity, not price, on these rows. If a GCP total moves, say so and explain
  why before assuming it is fine.

---

## 7. Do NOT

- Do **not** make an all-zero bill succeed (item 1). Fix the message only.
- Do **not** guess an instance size when an abbreviation does not expand
  cleanly (2b). A wrong size silently mis-sizes the GCP shape.
- Do **not** "fix" `job2`'s unresolved types. They are fabricated.
- Do **not** weaken the Lambda test to make it pass; strengthen it so it can
  fail (item 3).
- Do **not** add a new report section for coverage (item 4).
- Do **not** fail the run on zero workload rows (item 5). Banner only.

---

## 8. Done

- [ ] All-zero bills explain the real cause; genuine parse failures keep their
      message.
- [ ] `kafka.storage.gp2` is no longer treated as an instance.
- [ ] `db.m7g.xl`, `cache.r7g.large`, `inf1.xlarge`, `c7i-flex.*` resolve
      specs; the Spot-path gate is identified and reported.
- [ ] The Lambda SKU test asserts on the catalog description and can fail.
- [ ] Coverage appears as one line in the existing data-gap section.
- [ ] A zero-workload bill shows the banner instead of an empty comparison.
- [ ] Full suite passes at your recorded baseline + new tests.
