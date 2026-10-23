# WORK ORDER — One classifier for all three ingest branches

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/ingest.py`.** No mapper, rate or report changes. **Bill
totals must not move.** Workload subtotals *may* move on non-CUR bills, and any
row that changes classification must be visible and explained — see §4, which
is the most important section in this file.

---

## 1. The problem in plain English

Bills arrive in three shapes, and `ingest.py` has a branch for each:

| branch | input shape |
|---|---|
| raw CUR | `lineItem/UsageType`, `lineItem/LineItemType`, … |
| spreadsheet | `Category`, `Meter`, `Consumed`, `Cost` |
| invoice export | `Service`, `Custom Usage Type`, `Description`, `Cost ($)` |

Each branch does two jobs: **extract fields** from its own column names, and
**decide what those fields mean**. The second job is where they have drifted.

### What is already shared — do not re-do it

Both non-CUR branches now apply the same workload rule:

```sql
... NOT IN ('Usage','DiscountedUsage','SavingsPlanCoveredUsage') THEN FALSE
WHEN {_nonworkload_sql(...)} THEN FALSE
ELSE TRUE
```

That convergence was recent and is correct. **The remaining drift is one level
down: how each branch computes `line_item_type` in the first place.**

### The actual drift

Each branch derives `line_item_type` with its own inline SQL `CASE`, keyed on
its own columns:

| rule | spreadsheet branch | invoice branch |
|---|---|---|
| negative cost → `Credit` | yes | yes |
| `Savings Plans` category → `SavingsPlanRecurringFee` | **yes** | **NO** |
| reserved → `DiscountedUsage` | on `Meter` | on `Description`/`Service` |
| everything else | `Usage` | `Usage` |

**The invoice branch has no `SavingsPlanRecurringFee` rule at all.** A Savings
Plan hourly commitment therefore comes out as `line_item_type = 'Usage'`, slips
through the shared allow-list, and lands in the workload baseline.

That is not hypothetical — it is the bug we just fixed twice:

```
product   = Savings Plans for Compute usage
operation = 3yr No Upfront : CSP : $5.0 Hourly commitment
line_item_type = 'Usage'      <- wrong; CUR would say SavingsPlanRecurringFee
```

It first reached the workload baseline ($4,836), then reached the discount
divisor and was inflated to $10,075. Both were patched where they surfaced,
not where they originated.

### Why fix the structure rather than each instance

Three of the defects corrected in this codebase were this same shape, each
found and patched in one branch at a time:

- credits counted as workload (non-CUR branches only);
- `SavingsPlanRecurringFee` workload in one format, not another;
- SP commitment fees reaching the discount divisor.

Every new bill format adds a fourth place to get it wrong, and a fix in one
branch does not reach the others.

---

## 2. The shape to build

Separate **reading** from **meaning**. Branches keep their own column handling;
one function owns what the fields mean.

```python
def classify_charge(*, line_item_type_raw, product, usage_type,
                    operation, cost, category=None):
    """Return (charge_category, line_item_type, is_workload).

    charge_category follows FOCUS: 'Usage' | 'Purchase' | 'Credit' | 'Tax'.
    One definition, used by every ingest branch.
    """
```

FOCUS already supplies this vocabulary, so nothing is being invented:

| FOCUS `ChargeCategory` | what it covers here |
|---|---|
| `Usage` | consumption — the only thing comparable to GCP |
| `Purchase` | commitment purchases: RI fees, SP hourly commitments |
| `Credit` | refunds, discounts, SP coverage offsets |
| `Tax` | tax lines |

`is_workload` then becomes derived, not independently decided:

```python
is_workload = (charge_category == "Usage"
               and line_item_type in _WL_TYPES
               and not matches_nonworkload_patterns(...))
```

A Python helper already encodes part of this — `_is_workload_py` (~line 75).
Extend that rather than adding a parallel one.

---

## 3. Tasks

### T1 — Write the classifier, with the union of today's rules

Build `classify_charge()` from the rules currently spread across the three
branches, taking the **union**, not any one branch's subset. It must include:

- negative cost → `Credit`;
- Savings Plan **commitment/hourly-fee** wording → `Purchase` /
  `SavingsPlanRecurringFee` (the rule the invoice branch is missing);
- Savings Plan **coverage** wording on a negative line →
  `SavingsPlanCoveredUsage`;
- reserved-instance wording → `DiscountedUsage`;
- RI fee wording → `Purchase` / `RIFee`;
- otherwise `Usage`.

Accept both the raw CUR `line_item_type` (authoritative when present) and the
text fields the other branches must infer from. **When a raw
`line_item_type` is given, trust it** — do not let text inference override it.

### T2 — Call it from all three branches

Each branch maps its own columns to the classifier's parameters and uses the
result for `line_item_type` and `is_workload`. Delete the three inline `CASE`
expressions; do not leave one behind "for now".

The SQL branches will need the classifier's logic expressed once as SQL, or the
rows passed through Python. **Either is acceptable — one definition is the
requirement**, not the language. If you keep SQL, generate it from a single
Python source of truth so the two cannot diverge.

### T3 — A cross-format test

`tests/test_ingest_is_workload.py` exists for exactly this invariant. Add: the
same economic row, expressed in all three input shapes, classifies identically.

Cover at minimum:

1. an SP hourly commitment → `Purchase` / non-workload **in all three**;
2. an SP-covered usage credit → `Credit` / non-workload in all three;
3. an RI-applied usage row → `DiscountedUsage` / workload in all three;
4. ordinary on-demand usage → `Usage` / workload in all three;
5. a tax line → `Tax` / non-workload in all three.

Test 1 is the regression that this whole work order exists for.

---

## 4. How to do this without silently breaking a bill

This refactor can reclassify rows invisibly. That is the main risk, and it is
larger than the bug being fixed. **Do not skip this.**

Before changing anything, capture the current classification of every row
across the corpus:

```bash
cd skill/aws-gcp-cost-projection
python scripts/build_corpus.py --bills <bills-dir> --out before/
# dump aws_li_key, line_item_type, is_workload per bill
```

After the change, rebuild to `after/` and **diff per row**. Then:

- every row whose classification changed must be explainable in one sentence;
- the expected changes are the SP commitment rows on invoice-format bills;
- **anything else that moves is a regression until proven otherwise.**

List the diff in your summary. "Totals look fine" is not evidence — two
compensating reclassifications net to zero.

---

## 5. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/ingest.py
```

- Record the test count first; confirm it rises by exactly 5.
- **Bill totals unchanged on every bill** — this moves rows between workload
  and non-workload, never in or out of the catalog.
- Workload subtotals change only where §4's diff says they should.
- CUR-format bills must be **bit-identical**. That branch is already correct;
  if it moves, the classifier is overriding an authoritative
  `line_item_type` (T1).

---

## 6. Do NOT

- Do **not** take any single branch's rules as the baseline. Use the union
  (T1).
- Do **not** let inferred text rules override a raw CUR `line_item_type`.
- Do **not** leave one inline `CASE` in place alongside the classifier. Two
  definitions is the defect.
- Do **not** ship without the before/after row diff (§4).
- Do **not** change mappers, rates or the report. Classification only.

---

## 7. What this does not fix

Field **extraction** stays per-branch and still differs — a PDF puts the meter
in `product` and the rate in `operation`, where a CUR has clean columns. That
is genuinely format-specific work and is correct to keep separate.

This work order unifies only what those fields **mean** once extracted.

---

## 8. Done

- [ ] One `classify_charge()` returning charge category, line item type and
      workload flag.
- [ ] All three branches call it; no inline `CASE` survives.
- [ ] A raw CUR `line_item_type` is never overridden by text inference.
- [ ] Cross-format test: the same economic row classifies identically in all
      three shapes, including an SP hourly commitment.
- [ ] Before/after row-level diff produced, every change explained.
- [ ] Bill totals unchanged; CUR bills bit-identical.
