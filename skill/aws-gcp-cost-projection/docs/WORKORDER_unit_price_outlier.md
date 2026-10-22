# WORK ORDER — Detect line items priced inconsistently with their own meter

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: one detection pass in `scripts/ingest.py`, emitting an existing
diagnostic.** You are not changing any cost, any mapping, any rate, or any
total. **Every dollar figure in every output must be byte-identical before and
after.** This work order adds a warning and nothing else.

---

## 1. The problem in plain English

A bill lists many lines against the same meter. They should share a unit price.
In one real bill they do not:

```
Meter: APS5-Lambda-GB-Second-ARM   (all five lines, same Category/Subcategory)

consumed=    63,930.16   cost=$    0.85   price=1.33334e-05
consumed=   377,694.12   cost=$    5.04   price=1.33334e-05
consumed=   953,703.71   cost=$   12.72   price=1.33334e-05
consumed=23,579,810.90   cost=$  314.40   price=1.33334e-05
consumed=   480,703.23   cost=$6,409.41   price=0.0133334      <-- 1000x
```

The fifth line is priced exactly **1000x** its siblings. At the correct rate it
would be **$6.41**, not $6,409.41. That one line is **62% of that bill's entire
AWS cost**.

The file is internally consistent — `cost = consumed x price` on every line —
so nothing downstream can notice. Ingest reconciles perfectly (verified: raw
total equals catalog total to the cent). **Ingest is not at fault.** The error
is in the source bill, and we pass it straight through.

### Why it matters commercially

The inflated line lands in the **AWS** side of the comparison. A too-high AWS
baseline makes GCP look cheaper than it is, so the tool **overstates the saving
to the customer** — the direction that does real damage. On that bill the
projection reads 0.34x (a 66% saving) where the true figure is near 0.89x.

### What this work order does, and does not, do

It **detects and reports**. It does **not** correct the number.

Silently rewriting a figure that came off a customer's invoice is worse than
reporting it: the bill would then no longer reconcile to what the customer can
see, and we would be asserting we know their costs better than their bill does.
The operator gets told; the number stays as billed.

---

## 2. Where the check must live

**In `scripts/ingest.py`, on the raw rows, before the dedup INSERT.**

Not afterwards. The dedup groups by product/usage-type/operation and **SUMs**,
so those five Lambda lines collapse into a single catalog row carrying
25,455,842 GB-seconds and $6,742.41. The per-line prices no longer exist.
Verified: `aws_li_catalog` holds exactly one `Lambda-GB-Second` row.

So the check runs against `aws_raw` while the individual lines are still there.

### Deriving the price

Only one of the three supported bill formats has a `Unit Price` column. Do not
depend on it. Derive:

```
price = cost / usage_quantity        (skip rows where usage_quantity is 0/NULL)
```

This works identically across all three formats and is exactly what caught the
case above. Column names per format:

| format | meter | cost | usage |
|---|---|---|---|
| raw CUR | `lineItem/UsageType` | `lineItem/UnblendedCost` | `lineItem/UsageAmount` |
| `Category`/`Meter` | `Meter` | `Cost` | `Consumed` |
| `Service`/`Custom Usage Type` | `Custom Usage Type` | `Cost ($)` | `Usage Quantity` |

Use the existing `c([...])` column resolver rather than hardcoding names.

### Capturing where the row came from

The operator must be able to open the bill and look at the offending line, so
the check needs a source-row locator. `aws_raw` does not currently carry one.

Add it where `aws_raw` is created, as the **first** column:

```sql
CREATE TABLE aws_raw AS
  SELECT row_number() OVER () AS _src_row, *
  FROM read_csv_auto('...', ALL_VARCHAR=TRUE, header=TRUE)
```

There is more than one creation site in `ingest.py` — a materialised `CREATE
TABLE` (~line 817) and a streaming `CREATE VIEW` (~line 836) used for large
files, plus the explicit-DDL paths (~449, ~489). **Add it to every path that
the formats in the table above can take**, or the locator will be missing
exactly on the big bills where it matters most.

Verified on a real bill: the anomalous Lambda line comes out as `_src_row =
452`, and file line **453** is that row (one header line). So:

```
file_line = _src_row + 1        # single header row
```

Two honest caveats to put in the diagnostic's wording, not to solve:

- `row_number() OVER ()` has no ORDER BY, so file order is relied upon rather
  than guaranteed. DuckDB preserves insertion order by default and it matched
  exactly here, but treat the number as a **locator, not an identity**.
- A quoted field containing a newline shifts the physical line number. The
  identifying fields below are what make the row unambiguous; the number just
  gets the operator close.

**Check that adding the column breaks nothing.** The catalog INSERT selects
named columns, so it should be inert — but confirm `c([...])` resolution and
the faithfulness check still behave, and that no bill total moves (§5).

---

## 3. The rule — and why each guard is there

Every guard below exists because a looser version produced false positives on
the real corpus. Do not relax them.

```
For each meter group:
    candidates = rows with usage > 0 AND cost > 0        # positive-cost only
    require len(candidates) >= 3                          # need a reference
    reference  = median(price of candidates)
    require reference > 1e-9                              # reference must be real
    For each candidate:
        factor = max(price/reference, reference/price)
        if factor >= 100 and cost > 50:
            -> flag
```

- **positive-cost rows only.** Credits and zero-cost lines otherwise drag the
  median to ~0 and every row looks like an outlier. Without this guard one bill
  reported a factor of **1.6e16**.
- **reference > 1e-9.** Same failure, belt and braces.
- **>= 3 rows.** Two rows cannot establish which is the odd one.
- **factor >= 100, not 10.** At 10x the corpus produced 8 flags, mostly
  legitimate: a single meter name such as `APS5-Requests-Tier1` genuinely
  covers request classes priced 12.5x apart. 100x is far outside any real
  intra-meter spread.
- **cost > $50.** A mispriced rounding error is not worth an operator's
  attention.

### Calibration on the real corpus (11 bills)

At the specified thresholds, **exactly 3 lines flag**:

| bill | meter | factor | cost |
|---|---|---:|---:|
| job7 | `APS5-Lambda-GB-Second-ARM` | **1000x** | **$6,409.41** |
| job14 | `EBS` | 533x | $51.02 |
| job2 | `AP-SOUTHEAST-1-BoxUsage:NA` | 584x | $51.02 |

The first is the true defect. The other two are coarse catch-all meter buckets
(`EBS`, and the malformed `-Usage:NA` usage type tracked separately in
`WORKORDER_spec_coverage_completion.md`) that legitimately mix charge types —
worth an operator's glance, not noise. Three flags across eleven bills is the
intended signal rate.

**If your implementation flags materially more than this, it is wrong.** Re-read
the guards.

---

## 4. Tasks

### T1 — The detection pass

Add a function in `scripts/ingest.py`, called after `aws_raw` is loaded and
**before** the catalog INSERT. Implement the rule in §3 — in SQL against
`aws_raw`, or in Python over a fetched result; either is fine, but it must not
be slow on a 500k-row CUR, so prefer one grouped SQL query.

### T2 — Report through the existing channel

`scripts/diagnostics.py` already provides exactly what is needed. Ingest
already imports it (see its `instance_spec_unresolved` call).

```python
from diagnostics import diag, WARN

diag(WARN, "unit_price_outlier", "ingest.py",
     f"{n} line item(s) priced inconsistently with other lines on the same "
     f"meter. Largest: file line {top['file_line']}, meter "
     f"{top['meter']}, unit price {top['price']:.6g} vs {top['reference']:.6g} "
     f"on {top['peers']} other line(s) of that meter ({top['factor']:.0f}x), "
     f"cost ${top['cost']:,.2f}. Figures are reported as billed and have not "
     f"been adjusted.",
     rows=n, usd=usd,
     detail={"lines": [
         {"src_row": r, "file_line": r + 1, "meter": m, "resource": res,
          "account_id": acct, "usage": q, "cost": round(c, 2),
          "unit_price": p, "reference_price": ref, "factor": round(f, 1)}
         for r, m, res, acct, q, c, p, ref, f in flagged[:20]
     ]})
```

Emit **one** diagnostic for the bill, with a per-line breakdown in `detail` —
not one diagnostic per row. Cap the list (20 is ample).

**The message itself must name the worst line**, with its file line number,
meter, both prices and its cost. An operator reading the job log should not
have to open a JSON file to know which row to look at. `resource` and
`account_id` go in `detail` because they identify the row unambiguously even
if the line number is off by a quoted newline — include them whenever those
columns exist in the format, and omit them silently when they do not.

On the reference bill the message should read close to:

> 1 line item(s) priced inconsistently with other lines on the same meter.
> Largest: file line 453, meter APS5-Lambda-GB-Second-ARM, unit price 0.0133334
> vs 1.33334e-05 on 4 other line(s) of that meter (1000x), cost $6,409.41.
> Figures are reported as billed and have not been adjusted.

**Keep the wording factual and neutral.** State what was observed and that
nothing was altered. Do not characterise the customer's bill as broken, and do
not speculate about the cause.

### T2b — Surface it to whoever runs the report

The job log alone is not enough: the person running the report needs to see it
without tailing logs. `data_gaps.json` is the existing operator-facing channel
and already feeds a section of the HTML report (see `render_report.py`
~1689-1760, `data_gap_html`). Its shape is:

```json
{"count": 0, "total_cost": 0.0, "items": []}
```

Append one item per flagged line, merging with any existing file rather than
overwriting it — `classify_mechanics.py` and `ensure_full_coverage.py` also
write to it, so **read-modify-write, and never clobber their items**. Each item
carries the same locator fields as the diagnostic detail, so the report can
print "file line 453 — meter X — priced 1000x the other lines of that meter".

Keep that text neutral too: it describes the input bill, not the pipeline.

### T3 — Tests

Add to the ingest test file (the one covering `billing_format` detection and
`is_workload`):

1. `test_unit_price_outlier_detected` — five rows on one meter, four at price
   `p` and one at `1000p` with cost > $50, produces one
   `unit_price_outlier` diagnostic naming that meter.
2. `test_unit_price_outlier_not_flagged_below_threshold` — the same fixture
   with the odd row at `12.5p` produces **no** diagnostic.
3. `test_unit_price_outlier_ignores_zero_cost_rows` — a meter whose rows are
   mostly zero-cost does not flag (the 1.6e16 regression).
4. `test_unit_price_outlier_changes_no_totals` — catalog total and workload
   total are identical with and without the flagged row's anomaly present.
   **This is the test that matters most.**
5. `test_unit_price_outlier_reports_source_line` — the diagnostic's message
   contains the file line number of the offending row, and `detail["lines"][0]`
   carries `file_line`, `meter`, `unit_price` and `reference_price`.
6. `test_unit_price_outlier_appends_to_data_gaps` — an existing
   `data_gaps.json` containing an unrelated item still contains that item
   afterwards, plus the new one (the read-modify-write requirement in T2b).

Read `scripts/diagnostics.py` for how to capture emitted records in a test
(`read_all(job_dir)`).

---

## 5. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/ingest.py
```

- Record the test count before you start; confirm it rises by exactly 6.
- **Totals must not move.** Re-ingest any bill and confirm the catalog total,
  workload total, and every projected figure are unchanged. This work order
  cannot legitimately change a single number.
- On a bill containing a 1000x line, the job log contains one
  `[diag] WARN unit_price_outlier` line naming the meter **and the file line
  number** (453 on the reference bill), and `data_gaps.json` gains a matching
  item.

---

## 6. Do NOT

- Do **not** correct, clamp, drop, or re-price the anomalous line. Report only.
  See §1.
- Do **not** fail the run or raise. This is a customer-facing tool; a data
  oddity in one line must not stop a projection. `diag()` never raises — keep
  it that way.
- Do **not** run the check after the dedup INSERT. The evidence is gone by
  then (§2).
- Do **not** depend on a `Unit Price` column. Only one of the three formats
  has one.
- Do **not** lower the thresholds "to catch more". §3 records what each one
  costs in false positives.
- Do **not** invent a new report section or a banner for this. T2b uses the
  existing `data_gaps.json` channel and the data-gap section the report
  already renders — that is the whole surface. Full per-line detail
  (resource, account, both prices) stays in the job log and
  `diagnostics.jsonl`; the report entry is the neutral one-liner.
- Do **not** word it as a pipeline failure. It describes the input bill. "This
  line is priced differently from the other lines on its meter; reported as
  billed" — not "ingest could not handle this row".

---

## 7. Out of scope

**Acting on the finding.** Deciding what the pipeline should *do* about a bill
known to contain a mispriced line — exclude it from the headline, show a range,
ask the operator — is a product decision and is not yours to make here. Detect
and report; the decision comes later, informed by how often this actually fires.

**The coarse-bucket meters** (`EBS`, `-Usage:NA`). Those flags are a symptom of
usage-type extraction, tracked in `WORKORDER_spec_coverage_completion.md`.

---

## 8. Done

- [ ] Detection runs on raw rows before dedup, price derived as cost/usage.
- [ ] All five guards from §3 implemented.
- [ ] `aws_raw` carries `_src_row` on every creation path; no total moved.
- [ ] One `unit_price_outlier` WARN per bill via `diag()`, whose **message
      names the worst line by file number**, with the per-line breakdown in
      `detail`, neutral wording.
- [ ] Flagged lines appended to `data_gaps.json` without clobbering items
      written by other scripts, so the report runner sees them.
- [ ] On the reference corpus it flags 3 lines, not more.
- [ ] 6 new tests pass; full suite passes at your recorded baseline + 6.
- [ ] **No dollar figure anywhere changed.**
