# WORK ORDER — Credit and Savings-Plan-coverage lines counted as workload

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: one `CASE` expression in `scripts/ingest.py`.** You are not changing
any mapper, SKU, rate, or the CUR path. The **bill total must not move** — rows
move from the workload baseline into the non-workload (savings) side, and the
sum of the two is unchanged. The *workload* subtotal rises on affected bills.

---

## 1. The problem in plain English

The pipeline compares **AWS on-demand list cost** against **GCP on-demand
cost**. That is a decided policy, and the CUR path implements it carefully
(`ingest.py:1014-1100`): workload rows carry gross `publicOnDemandCost`, and
discounts are kept as separate `is_workload = FALSE` rows so the bill still
reconciles to the real invoice.

Some bills do not arrive as a CUR. One common export has the columns
`Service, Region, Custom Usage Type, Description, Usage Quantity, Cost ($)`.
In that format a Savings-Plan-covered instance appears as **two lines**:

```
On Demand Linux a1.2xlarge Instance Hour                      1,488.00    +349.98
a1.2xlarge Linux instance usage covered by Compute Savings..  1,488.00    -349.98
```

The positive line **is** the on-demand list cost — exactly what the policy
wants. The negative line is the Savings Plan offset.

**The bug: both lines are marked `is_workload = TRUE`.** They cancel, so that
instance contributes **$0** to the AWS baseline — while the GCP side still
projects a full 1,488 hours of compute. GCP is then compared against an AWS
figure with its own cost subtracted out.

### Measured on a real bill (corpus `job14`)

| | |
|---|---|
| negative lines counted as workload | **25** |
| of which Savings-Plan coverage | 24 lines, **-$9,142.05** |
| of which an account-level discount | 1 line, **-$1,457.11** |
| total netted out of the baseline | **-$10,599.16** |
| AWS workload as reported | $19,069 |
| AWS workload, corrected | **$29,668** |
| GCP projected | $25,986 |
| **ratio reported → corrected** | **1.36x → 0.88x** |

0.88x is exactly where every other bill in the corpus sits. The entire apparent
over-projection on this bill is this defect.

Also present on `job8` and `job9` (-$492 each). **Not** present on CUR bills —
that path excludes credits by construction.

---

## 2. Where it is

`scripts/ingest.py` has **three** ingest branches, and they classify
differently. This is the root of it:

| branch | line | `is_workload` logic | negative lines |
|---|---|---|---|
| raw CUR | ~1029 | allow-list: `line_item_type IN ('Usage','DiscountedUsage','SavingsPlanCoveredUsage') AND NOT marketplace` | excluded by construction ✅ |
| `Category`/`Meter` | ~1221 | `_nonworkload_sql(category, meter)` only | maps cost<0 to `line_item_type='Credit'` (~1215) but **`is_workload` never consults it** ⚠️ |
| `Service`/`Custom Usage Type` | **~1715** | `_nonworkload_sql(service, description)` only | **no negative handling at all** ❌ |

The third branch is the one that produced the measurement above. Its
`line_item_type` CASE (`ingest.py:1710-1713`) only distinguishes RI
(`DiscountedUsage`) from `Usage`; it has no notion of a credit or a Savings
Plan. Its `pricing_model` CASE (`1702-1709`) handles Spot and RI but not
Savings Plans.

Verified: on a clean re-ingest of that bill, all 25 negative rows come out as
`line_item_type='Usage'`, `is_workload=TRUE`, `billing_format='flat_csv'`.

---

## 3. Tasks

### T1 — A negative-cost line is never workload (the fix)

In the `Service`/`Custom Usage Type` branch, change the `is_workload` CASE at
`ingest.py:1714-1717` so a negative row is excluded:

```sql
CASE
    WHEN {_sql_num(cost_col)} < 0 THEN FALSE
    WHEN {_nonworkload_sql(c(['service']), c(['description']))} THEN FALSE
    ELSE TRUE
END AS is_workload,
```

**Why this rule and not a description pattern:** in this format the gross
on-demand cost is already on the *positive* line. A negative amount can only be
an offset against it — a Savings Plan credit, an RI credit, a discount, a
refund. None of those belongs in an on-demand baseline. Matching on
`'covered by%Savings Plan'` text would fix the 24 SP lines and miss the
discount line, and would break on the next export that words it differently.

### T2 — Label them, so the savings side can show them

In the same branch's `line_item_type` CASE (`ingest.py:1710-1713`), classify
negative lines rather than calling them `Usage`:

```sql
CASE
    WHEN {_sql_num(cost_col)} < 0
         AND {c(['description'])} ILIKE '%covered by%Savings Plan%'
         THEN 'SavingsPlanCoveredUsage'
    WHEN {_sql_num(cost_col)} < 0 THEN 'Credit'
    WHEN ... existing RI conditions ... THEN 'DiscountedUsage'
    ELSE 'Usage'
END AS line_item_type,
```

T1 is what moves the money; T2 is what makes the report able to say *why*.
`line_item_type` is part of the md5 dedup key, so this also stops a credit line
merging with a usage line that shares its `Custom Usage Type` — which is how
these became invisible in the first place.

### T3 — Do the same for the `Category`/`Meter` branch

That branch already computes `'Credit'` for negative rows (~1215) and then
ignores it. Add the same `WHEN cost < 0 THEN FALSE` guard to its `is_workload`
CASE at ~1221-1224. Same reasoning; smaller exposure.

**Note on SQL:** the `line_item_type` alias cannot be relied on inside the same
`SELECT` list. Repeat the `{_sql_num(cost_col)} < 0` predicate as shown rather
than referencing the alias.

### T4 — Tests

Add to the ingest test file (find it under `tests/` — the one covering
`billing_format` detection):

1. `test_negative_line_is_not_workload_service_format` — a two-row fixture in
   `Service/Custom Usage Type` form (one `+349.98` on-demand line, one
   `-349.98` "covered by Compute Savings Plans" line) yields workload total
   **349.98**, not 0.
2. `test_negative_line_is_not_workload_meter_format` — same for the
   `Category`/`Meter` branch.
3. `test_savings_plan_credit_is_labelled` — the SP line gets
   `line_item_type='SavingsPlanCoveredUsage'`; a plain discount gets `'Credit'`.
4. `test_bill_total_unchanged_when_credits_reclassified` — catalog total over
   **all** rows is identical before and after; only the `is_workload` split
   moves. This is the regression that matters most.

---

## 4. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/ingest.py
```

- Record the test count **before** you start; confirm it rises by exactly 4.
- **The bill total must not move.** Ingest's own faithfulness check sums all
  catalog rows, workload and non-workload alike, so a correct fix leaves it
  untouched. If the total moves, you deleted rows instead of reclassifying
  them.
- On a bill in this format, the workload subtotal **rises** by the absolute
  value of its negative lines. On the reference bill that is
  $19,069 → $29,668, and the GCP/AWS ratio goes 1.36x → 0.88x.
- CUR-format bills must be **bit-identical**. You did not touch that branch.

---

## 5. Do NOT

- Do **not** touch the raw-CUR branch (~1029). It is correct.
- Do **not** drop or filter out negative rows. They must stay in the catalog as
  `is_workload = FALSE` so the bill still reconciles to the invoice and the
  savings can be reported. Deleting them breaks the faithfulness check.
- Do **not** match on description text to decide *workload-ness* (T1). Text is
  for labelling (T2); the sign is for the decision.
- Do **not** add `publicOnDemandCost`-style gross/net logic to these branches.
  The column does not exist in these formats — the positive line already *is*
  the gross figure. That is the whole reason this fix is a one-line sign test.
- Do **not** "fix" the ratio anywhere downstream. The projection is correct;
  the baseline it is divided by is wrong.

---

## 6. Out of scope

**Unifying the three branches' classification.** They have drifted — the CUR
path allow-lists, the `Category`/`Meter` path half-handles credits, the
`Service` path not at all. One shared classifier would be the right structure
and is worth doing, but it is a refactor with its own risk and belongs in its
own work order. Note it in your summary; do not start it here.

---

## 7. Done

- [ ] Negative lines are `is_workload = FALSE` in both non-CUR branches.
- [ ] Negative lines are labelled `Credit` / `SavingsPlanCoveredUsage`.
- [ ] 4 new tests pass; full suite passes at your recorded baseline + 4.
- [ ] Bill totals unchanged; workload subtotal rises on affected bills.
- [ ] CUR-format output unchanged.
- [ ] Summary reports the workload-subtotal change per affected test bill, and
      anything noticed but not fixed.
