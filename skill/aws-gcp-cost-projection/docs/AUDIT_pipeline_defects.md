# Pipeline audit — defects found by inspection, with evidence

Swept 2026-10-17 against the current tree and the 12-bill corpus built from
`C:\Users\arnav\Downloads\Facets` (4 PDF, 5 XLSX, 4 CSV incl. one raw CUR),
**1,457 workload rows**.

Every finding below names the file and line, and carries either a measured
number from the corpus or a reproducible demonstration. Nothing here is
inferred from reading alone without a check.

This is the *audit*. The curated backlog with fixes and priority is
`ISSUES_REGISTER.md`; items already tracked there are cross-referenced rather
than repeated.

---

## D-1 — `NOT LIKE` on a nullable column silently drops the row

**Severity: high. Affects review coverage and outlier detection.**

### Evidence — the SQL semantics

DuckDB three-valued logic, demonstrated directly:

```
v='t3.large'  v NOT LIKE 't%'  -> False
v='m5.large'  v NOT LIKE 't%'  -> True
v=NULL        v NOT LIKE 't%'  -> NULL     <-- not TRUE

SELECT v FROM (VALUES ('t3.large'),('m5.large'),(NULL)) WHERE v NOT LIKE 't%'
  -> ['m5.large']        the NULL row is DROPPED, not kept
```

A `WHERE ... NOT LIKE` on a nullable column is therefore an **implicit
`IS NOT NULL`**. The author almost always means "everything that isn't a
t-family instance", which should include rows with no instance type at all.

### Evidence — the columns really are null

Measured on the corpus:

| column | NULL rows | share | spend |
|---|---:|---:|---:|
| `aws_li_catalog.instance_type` | **571 / 1457** | 39% | **$97,803** |

### Call sites

| file:line | predicate | what it silently excludes |
|---|---|---|
| `scripts/auto_review.py:117` | `c.instance_type NOT LIKE 't%'` | all 571 rows above — they never reach this review check |
| `scripts/detect_outliers.py:210` | `m.gcp_sku_name NOT ILIKE '%preemptible%'` | every passthrough row (`gcp_sku_name` is NULL on passthrough) |
| `scripts/detect_outliers.py:211` | `m.gcp_sku_name NOT ILIKE '%spot%'` | same |
| `scripts/detect_outliers.py:131` | `p.product NOT ILIKE '%Data Transfer%'` | lower risk — `product` is rarely NULL |
| `scripts/apply_rates.py:244-245` | `c.usage_type NOT ILIKE '%ServerlessV2%'` / `'%:ACU%'` | PDF rows, which carry no `usage_type` at all |

`apply_rates.py:244-245` is the one to look at first: it is in the **pricing**
path, and PDF bills systematically have empty `usage_type`, so the exclusion
behaves differently on PDF than on CUR for the same logical row.

### Fix

Wrap every nullable column in a `NOT LIKE` / `!=` predicate with `COALESCE`:

```sql
COALESCE(c.instance_type, '') NOT LIKE 't%'
```

Then add a lint test — the same shape as
`tests/test_notes_are_output_only.py` — that fails on a bare `NOT LIKE` /
`NOT ILIKE` / `!=` against any column known to be nullable.

**This will change results**: rows currently excluded start being reviewed and
flagged. Each newly-flagged row needs an explanation, so run it against the
corpus and diff before and after.

---

## D-2 — Five different over-projection thresholds, in four files, two languages

**Severity: medium. No single source of truth for "how wrong is too wrong".**

| check | file:line | ratio | cost floor | configurable? |
|---|---|---:|---:|---|
| A1 over-projection, compute/SQL/Memorystore | `detect_outliers.py:50` | **1.3x** | $50 | yes — `review-config.json` |
| A1 over-projection, other services | `detect_outliers.py:51` | **1.5x** | $50 | yes |
| storage over-projection | `validate_fix.py:1304` | **2.0x** | $20 | **no — hardcoded** |
| transfer over-projection | `validate_fix.py:1308` | **3.0x** | $20 | **no — hardcoded** |
| Phase-5 blocking gate | `internal/jobs/orchestrate.go:524` | **3x** | $20 | **no — hardcoded, and in Go** |
| outlier_gate R1 clamp | `review-config.json` `outlier_gate_r1_ratio` | **50x** | $25 gcp | yes |
| outlier_gate R4 whole-bill | `review-config.json` | **2.0x** | — | yes |

Two of these read `data/review-config.json`; three are hardcoded, and one of
the hardcoded ones is **in Go**, so it cannot read the Python config file even
if someone wanted it to.

### What this produces

A row over-projecting by 10x is: **reported** by detect_outliers (>1.5x),
**fails** the Phase-5 gate (>3x), and **is not clamped** by outlier_gate (needs
>50x). The net behaviour is "gate fails → one LLM retry → if unfixed, logged as
a warning on a non-critical phase → report ships".

That may well be the intended policy. The problem is that **nobody can state it
from reading any one file**, and changing it means editing a JSON file, two
Python modules and a Go string.

### Fix

Move all of them into `data/review-config.json` with explicit names
(`over_projection_report_ratio`, `over_projection_block_ratio`,
`over_projection_clamp_ratio`). Have the Go gate read the same file at startup,
or — better — delete the Go `CheckSQL` and let the Python gate runner own it,
which also removes the duplicated SQL noted in
`WORKORDER_validation_path_repair.md`.

Then document the escalation ladder in one place: report at X, block at Y,
clamp at Z, and assert X ≤ Y ≤ Z in a test so the ladder cannot invert.

---

## D-3 — Materiality row omits currency and native cost

**Severity: high for non-USD customers. Tracked in `ISSUES_REGISTER.md` NEW-1.**

`scripts/ingest.py:2086-2100` re-adds dropped low-materiality rows as one
synthetic row. The INSERT names 14 columns and omits `currency` and
`aws_amortized_cost_native`.

Measured — **exactly one NULL row per bill, in all 12 bills**:

| bill | USD total | native total | gap |
|---|---:|---:|---:|
| aws_cur_style_report_enriched.csv | 419,119 | 414,923 | 1.0% ($4,196) |
| test-bill (1).csv | 19,359 | 19,069 | **1.5%** |
| May_filtered.csv | 21,023 | 20,816 | 1.0% |
| *(9 more, identical shape)* | | | ~1.0% |

The row's own comment says it exists "so the bill total always reconciles".
It reconciles in USD only.

---

## D-4 — The verifier/`spec_gap` contract is aggregate, so offsetting changes hide

**Severity: medium. Already fixed in `spec_parity.py`; recorded here as the pattern.**

Confirmed real during this work: folding the `ebs` canonical into `ec2` added
**$184** of EC2 `spec_gap` while kinesis dropped **$142** out, for a net **+$44**
that read as noise. The EC2 regression was only visible per-service.

`scripts/spec_parity.py` now prints the per-service table with that warning, and
`WORKORDER_spec_coverage_completion.md` §7 checks per service. **Any new
aggregate metric should be assumed to hide this and be broken down the same
way.**

---

## D-5 — A green result from a broken harness is indistinguishable from a green result

**Severity: process, not code. Worth recording because it nearly shipped.**

While testing the column verifier I wrote a loader whose dict comprehension
iterated `list(reader)` per column, exhausting the CSV iterator on the first
one. Every later column read as empty. The run printed **"4/4 caught"** — a
pass — and three of those four were caught for the wrong reason: *"column is
empty"*.

`scripts/schema_mapper.py` now refuses to report verdicts when every column
profiles as zero rows. **Every checker in this pipeline needs the equivalent
guard**: assert the input is non-trivial before trusting a clean result. A
validator that sees nothing reports success.

---

## D-6 — Known-but-unquantified: generic over-claim is smaller than assumed

**Severity: corrects a previous overstatement.**

Spend by mechanic from the live classifier, measured on the corpus:

```
compute_breakdown   383,498   71.5%
compute_arm          45,240    8.4%
block_storage        25,974    4.8%  generic
per_request          16,709    3.1%  generic
misc                  8,293    1.5%  generic (LLM-priced)
object_storage        6,222    1.2%  generic
flat_hourly           5,157    1.0%  generic
                     -------
GENERIC TOTAL        62,355   11.6%
```

Most "generic" routing is correct — EC2's $18.9k in `block_storage` **is** EBS,
S3's $6.2k in `object_storage` **is** S3. The genuine over-claim is two
services: `lambda → per_request` ($13,486) and `rds → misc` ($5,568).

**`misc` — the LLM-priced bucket — is 1.5% of spend.** Earlier characterisations
of a large LLM-dependency problem were wrong.

---

## D-7 — `compute_breakdown` is 71.5% of spend and has no dedicated accuracy check

**Severity: high by exposure. No defect proven — this is a gap in verification.**

Every gate in the pipeline is generic: ratio bands, null checks, passthrough
budget. None of them asks whether the *dominant* mechanic is right.

$383,498 of $536,629 flows through one mapper. A systematic error there — a
wrong family choice, a missing licence component, a vCPU off-by-one — would sit
inside every ratio band and trip nothing.

### Fix

A targeted check rather than another generic one. For every
`compute_breakdown` row with a known instance type, assert the mapped GCP
vCPU/RAM is **≥** the AWS spec (the never-underprovision guarantee
`reconcile_capacity.py` already claims to enforce) and report the distribution
of GCP/AWS vCPU ratios. A mapper that silently halves a machine shows up as a
cluster at 0.5; today nothing would print that.

---

## D-8 — Concurrent jobs race on a shared cache file, breaking the pipeline's own isolation guarantee

**Severity: high. Silent data loss plus unnecessary model spend.**

### Evidence

`scripts/resolve_unknown_product_codes.py` runs as a **per-job Phase-1
post-script** (`internal/jobs/orchestrate.go:130`):

```go
PostLLMScripts: []string{
    "?scripts/aws_normalizer.py $DB",
    "?scripts/resolve_unknown_product_codes.py $DB",
},
```

It writes a cache that lives in the **shared skill directory**, not the job
directory (`scripts/resolve_unknown_product_codes.py:56, 93`):

```python
_CACHE_PATH = os.path.join(_DATA, "learned-product-codes.json")
...
with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=False)
```

Read-modify-write, no lock, no temp-file-and-rename. The watcher runs jobs
concurrently (`internal/jobs/watcher.go`, multiple `go func()` dispatch paths).

### What happens

Two jobs ingesting at the same time:

1. Both load the cache — say 100 entries each.
2. Job A learns 3 names, writes 103.
3. Job B learns 2 different names, writes 102 — **A's three are gone.**

Worse, `open(path, "w")` truncates *immediately*. A job reading while another
is mid-write gets truncated or empty JSON: the cache is treated as empty and
every name is sent to the model again. The script is wired `?`-soft, so a parse
crash is swallowed and nothing is reported.

### Why this one matters beyond the bug

The orchestrator asserts per-job isolation **three times** in its own prompts
(`orchestrate.go:88, 173, 299`):

> "this job's files live ONLY under <<JOB_DIR>>, isolated from every other job
> running on this machine at the same time"

That guarantee is false for this file. Any future per-job script that writes to
the skill directory will inherit the same bug, because nothing prevents it.

### Fix

Two parts, and the second matters more:

1. Make the write atomic and merge-safe — write to a temp file in the same
   directory, re-read and merge the on-disk version, then `os.replace()`
   (atomic on Windows and POSIX). A lock file is not enough on its own, because
   the failure is a lost update, not a torn read.
2. **Add a test that fails if any script under `scripts/` opens a path under
   `data/` for writing.** The cache is a legitimate exception and should be
   routed through one audited helper; everything else should be refused. That
   is what stops the next one.

Consider also whether this cache belongs in the job directory with a separate
promotion step, rather than being written live by a running job.

---

## D-9 — `billing_month` is blank on every non-CUR bill, so multi-month bills merge silently

**Severity: high if any customer uploads more than one month in a file.**

### Evidence

Measured across the corpus:

| bill | billing_month |
|---|---|
| aws_cur_style_report_enriched.csv | `2026-06` |
| **all 11 others** (4 PDF, 4 XLSX, 3 CSV) | `''` |

Only the raw-CUR path populates it. Every PDF, XLSX and flat-CSV bill has it
empty.

### Why that is not cosmetic

`billing_month` is part of the aggregation key (`scripts/ingest.py:1125`):

```sql
GROUP BY product, usage_type, operation, aws_region, pricing_model,
         line_item_type, is_workload, license_model, operating_system,
         database_engine, deployment_option, volume_type, pricing_unit,
         billing_month
```

and the code comments state the intent explicitly:

> "the md5 dedup key includes billing_month to keep months distinct"

On a CUR that works. On every other format the value is constant `''`, so **two
months of the same charge collapse into one row** with their costs summed. The
projection then prices one month's rate against two months' quantity, or shows
a single month at double the real spend — with no warning, because the bill
total still reconciles.

Nothing detects or rejects a multi-month upload: a grep for a distinct-month
count or a multi-month guard finds none.

### Fix

1. Populate `billing_month` in the PDF/XLSX/flat-CSV paths. Most bills carry a
   period in a header, a filename (`June-dev.xlsx`, `May.xlsx`) or a date
   column; where none exists, leave it NULL rather than `''` so "unknown" and
   "same month" stop being the same value.
2. Assert it at ingest: if more than one distinct `billing_month` is present,
   either handle it deliberately or reject the file with a clear message. A
   silent merge is the one outcome that must not happen.
3. Where the period genuinely cannot be determined, record that as a
   diagnostic — the pipeline is projecting a monthly figure from a file whose
   period it does not know.

---

## D-10 — `billing_days` falls back to 30 silently, and it divides into instance counts

**Severity: medium. Compounds with D-9.**

### Evidence

`scripts/ingest.py`:

```python
def infer_billing_days(rows):
    candidates = [r[1] for r in rows if r[1] and 670 <= r[1] <= 745]
    if candidates:
        return round(max(candidates) / 24)
    return 30              # <- silent default, no record that it was guessed
```

The inference only fires when some row has 670–745 usage hours, i.e. a resource
that ran the whole month. A bill of short-lived or partial-month resources has
no such row and silently gets 30.

That value then feeds sizing (`scripts/ingest.py`, enrichment):

```python
instance_count = round(total_usage / (b_days * 24), 4) if b_days else None
```

So a wrong `billing_days` produces a wrong `instance_count` for every compute
row in the bill, and `instance_count` feeds the mappers' sizing decisions.

Corpus check also shows `billing_days` is `NULL` for a subset of rows in every
bill — the rows that exited enrichment early (the `continue` paths in D-1's
family), so they have no count at all.

A smaller signal worth a look: `June.xlsx` infers **31** days. June has 30.
Either a row reports more hours than the month contains, or the inference is
picking up something else.

### Fix

- Prefer an explicit period when one is available — a `billing_month`, a date
  column, or a stated billing period — and use the inference only as a
  fallback.
- When the fallback is used, record it (`billing_days_source = 'inferred' |
  'default_30'`) and emit a diagnostic with the affected spend. A guessed
  denominator under every compute sizing decision should not be invisible.
- Add a sanity assertion: inferred days must match the days in
  `billing_month` when that is known. That alone would flag the June/31 case.

---

## D-11 — The dimensional-mismatch guard is blind to compound units

**Severity: high. It sits in the pricing path and its whole purpose is to stop
unit errors.**

### What the guard is for

`scripts/resolve_sku.py:10` states the intent:

> "classes from unit_equivalence.py; rejects dimensional mismatches as
> passthrough (G-1b)"

and `resolve_sku.py:338-347`:

```python
res_cls = unit_class(resolved_unit)
exp_cls = unit_class(exp_unit)
if res_cls is not None and exp_cls is not None:
    if res_cls != exp_cls:
        # Dimensional mismatch: REJECT
```

This is the check that exists to stop a per-hour SKU being used to price a
per-GB row — the class of error that produced the historical 50x, 120x and
1300x bugs.

### Failure 1 — compound units collapse to their time component

`unit_class()` (`scripts/unit_equivalence.py:70-78`) ends in substring
heuristics. Anything containing `sec`, `hour`, `hrs` or `hr` returns `time`.
Tested against real cloud units:

| unit | `unit_class()` | what it actually is |
|---|---|---|
| `Lambda-GB-Second` | **time** | GB × seconds |
| `GB-Second` | **time** | GB × seconds |
| `vCPU-Hours` | **time** | vCPU × hours |
| `ACU-Hrs` | **time** | ACU × hours |
| `GiB-Hours` | **time** | GiB × hours |
| `Gibibyte hour` | **time** | GiB × hours (a real GCP unit) |
| `Shard-Hours` | **time** | shards × hours |
| `Hrs` | time | time ✓ |

**Six of fifteen common units tested are compound, and every one collapses to
pure `time`.** So a `Lambda-GB-Second` row matched to a plain per-hour VM SKU
passes the guard — both sides are "time". The check is blindest exactly where
the unit errors happen, because compound units are where they happen.

Measured on the corpus: `Lambda-GB-Second` carries **$13,485** and is
classified `time` today.

### Failure 2 — unknown units skip the check entirely

`if res_cls is not None and exp_cls is not None` means an unrecognised unit
disables the guard rather than failing it. Measured:

| | rows | spend |
|---|---:|---:|
| unit classified | 1,209 | $529,037 |
| **unit UNCLASSIFIED → guard skipped** | **248** | **$7,592** (1.4%) |

`IOPS-Mo` and `vCPU-Months` are both real AWS units that return `None`.

### Fix

The class set is the problem: five classes (`time`, `storage-month`, `data`,
`count`, `seat`) cannot describe a domain whose units are mostly products of
two dimensions.

Model a unit as a **dimension vector**, not a label — e.g.
`GB-Second = {data: 1, time: 1}`, `vCPU-Hours = {compute: 1, time: 1}`,
`Hrs = {time: 1}`, `GB-Mo = {data: 1, time: 1}` *(note this is genuinely the
same shape as GB-Second — they differ by scale, not dimension, which is a
second thing the current model cannot express)*. Two units are compatible iff
their vectors match; the scale difference becomes an explicit conversion
factor rather than something `unit_multiplier` absorbs silently.

Then **invert the guard's default**: an unknown unit must fail, not pass. A
unit the system cannot parse is precisely when it should not be pricing.
Pair that with a diagnostic listing unparsed units so the vocabulary can grow.

---

## D-12 — Rate vintage is neither gated nor disclosed

**Severity: medium-high. Affects every number the product produces.**

### Evidence

`data/CATALOG_META.json`:

```json
{"fetched_at": "2026-07-27T06:50:51Z", "service_count": 110, "sku_count": 85952}
```

Today is 2026-10-17. **The GCP price catalog in use was fetched 82 days ago.**

`internal/jobs/catalog_refresher.go` defines `catalogStaleDays = 7` and runs in
the background, so the intent is weekly. Whatever the reason it has not fired,
the per-bill pipeline is unaffected by it:

```
grep -rn "check_catalog_age" internal/jobs/orchestrate.go scripts/*.py
  -> no in-pipeline caller
```

No phase checks catalog age. No gate fails on it. And the customer-facing
report does not state the rate vintage, so a projection built on 82-day-old
prices is indistinguishable from one built this morning.

### Why it matters

GCP changes prices, adds SKUs and retires machine families. An 82-day-old
catalog produces three distinct errors that all look like correct output:
a stale rate, a missing new SKU (which becomes `no_catalog` → passthrough),
and a retired SKU priced as though it still exists.

### Fix

- Stamp the catalog's `fetched_at` onto the job at rate-fill time, so every
  projection records which catalog vintage produced it.
- Emit a diagnostic — not a failure — when the catalog is older than the
  configured staleness window, with the age and the affected spend.
- Disclose the vintage in the report. "Priced against the GCP catalog as of
  2026-07-27" is a one-line change and it is the honest statement.
- Separately, find out why the weekly refresher has not run for 82 days. That
  is an operational question this audit cannot answer from the code.

---

## What I checked and did NOT find a defect in

Recording these so the next sweep does not redo them:

- **Division by zero** — the numeric paths use `NULLIF(x,0)` or guard with
  `if total_usage`. One unguarded case in `explain_row.py:109`, which is a
  human-facing diagnostic tool, not the pipeline.
- **Currency detection** — `ingest.py:195` `_detect_foreign_currency()` does
  look for a bare `currency` column, so the vendor exports' `Currency` column
  is found. My earlier concern that it was unmapped was wrong; the alias list I
  tested (`c()`) is a different mechanism. The only currency defect is D-3.
- **`except Exception: pass`** — the remaining instances are in
  `validate_fix.py` (6) and `verify_compute_availability.py` (1), all around
  optional enrichment rather than load-bearing logic. Lower priority than when
  first flagged, because the diagnostics channel now covers the paths that
  mattered.

---

## Priority

| | finding | why |
|---|---|---|
| 1 | **D-3** | correctness bug on a customer-facing total; small fix |
| 2 | **D-1** | 571 rows / $97,803 silently skipping review today |
| 3 | **D-7** | no coverage over 71.5% of spend |
| 4 | **D-9** | a two-month upload is silently summed into one month today |
| 5 | **D-8** | breaks a guarantee the pipeline states three times; corrupts a committed file |
| 6 | **D-2** | not wrong, but unknowable; blocks reasoning about every other gate |
| 7 | **D-11** | the guard against unit errors is blind to compound units, where unit errors live |
| 8 | **D-12** | every number is priced against an 82-day-old catalog, undisclosed |
| 9 | **D-10** | guessed denominator under every compute sizing decision |
| 10 | **D-6** | re-scope the generic work to the two services that matter |
| — | D-4, D-5 | already fixed; keep as patterns to apply to new checks |

### A pattern across D-1, D-3, D-8, D-9, D-10, D-11 and D-12

All five are the same failure mode, in five places: **a value is absent or
guessed, and the absence is represented by something that looks like a normal
value** — NULL that a `NOT LIKE` drops, an omitted column, a lost cache entry,
`''` standing for "unknown month", `30` standing for "no idea".

Add D-11 and D-12 and the pattern widens: an unknown unit *disables* a safety
check instead of tripping it, and a rate's age is not part of the rate.

**The pipeline has no concept of "I do not know", and no concept of "how old is
this".** Until it has both, each of these gets fixed individually and the next
one appears somewhere else.

The architecture below is the structural answer. It is one mechanism, not
twelve fixes.
