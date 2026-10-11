# WORK ORDER — Unify `is_workload` across all bill formats (NEW-3)

**For an implementing AI / engineer with no prior context. Read fully. This is an
INGEST-side fix (scripts/ingest.py), NOT classify_mechanics.**

## The problem
`is_workload` (a boolean column on `aws_li_catalog`: TRUE = real resource consumption
that would recur on GCP, FALSE = fees/taxes/credits/marketplace/support that vanish on
GCP) is derived FOUR different ways in `scripts/ingest.py`, and they DIVERGE:

- **raw CUR** (~line 966, `_workload_expr`): `line_item_type IN ('Usage','DiscountedUsage',
  'SavingsPlanCoveredUsage') AND NOT marketplace`. This is the GOLD STANDARD — it uses
  AWS's authoritative `lineItem/LineItemType` enum, so Tax/RIFee/Credit/Refund/EDP/etc.
  are all non-workload automatically.
- **spreadsheet** (~line 1163): `Category ILIKE support/marketplace/AWSMP/tax/Savings Plans/
  Registrar → FALSE`. MISSES Credit, Refund, RIFee, EDP, private/bundled discounts.
- **flat CSV** (~line 1660): `service/description ILIKE marketplace/support → FALSE`.
  MISSES tax, credits, savings-plans, RIFee ENTIRELY.
- **PDF** (~line 1575): long description/product keyword list — most complete of the three
  non-CUR paths, but still ad-hoc and separate.

**Concrete bug:** a **Credit** row is non-workload in raw-CUR and PDF, but
`is_workload=TRUE` in a **flat-CSV** bill → counted as real spend → inflates the AWS
total and the GCP projection. The three non-CUR paths must compute the SAME logical
predicate the CUR path does.

## The logical basis (canonical definition — the single source of truth)
A row is **workload** iff:
- its `LineItemType` ∈ {`Usage`, `DiscountedUsage`, `SavingsPlanCoveredUsage`}  (when LineItemType is available), **AND**
- it is NOT a marketplace/support product.

Everything else is **non-workload**: `Tax`, any `*Fee` (incl `RIFee`,
`SavingsPlanUpfrontFee`, `SavingsPlanRecurringFee`, `SavingsPlanNegation`), `Credit`,
`Refund`, `EdpDiscount`, `PrivateRateDiscount`, `BundledDiscount`, `DistributorDiscount`,
`SolutionProviderDiscount`, Marketplace, Support, Registrar, late fees.

For formats WITHOUT a `LineItemType` column, reconstruct the same verdict from the
available text (category / description / product): a row is non-workload if it matches
any non-workload pattern; otherwise workload.

## Steps

### 1. Create the single source: `data/workload_classification.json`
```json
{
  "_meta": {"purpose": "Single source of truth for is_workload. ingest.py's per-format derivations all read THIS. Edit here only."},
  "workload_line_item_types": ["Usage", "DiscountedUsage", "SavingsPlanCoveredUsage"],
  "nonworkload_patterns": [
    "marketplace", "awsmp", "aws marketplace",
    "support", "aws support", "enterprise support", "business support", "developer support",
    "tax",
    "savings plan", "savingsplan",
    "reserved instance", "rifee", "ri fee",
    "credit", "refund",
    "edp discount", "enterprise discount program", "private rate", "private pricing",
    "bundled discount", "distributor discount", "solution provider",
    "registrar",
    "late fee", "ocb late fee"
  ]
}
```
(Patterns are case-insensitive substrings. Keep them lowercase.)

### 2. Add a loader + two helpers in ingest.py (near the top, after config_loader import)
```python
_WL_CFG = _cfg("workload_classification")  # _cfg = load_data_config
_WL_TYPES = set(_WL_CFG["workload_line_item_types"])
_NONWL_PATTERNS = _WL_CFG["nonworkload_patterns"]

def _is_workload_py(line_item_type, *text_fields):
    """Python-path verdict (PDF / usage-units parsers)."""
    blob = " ".join((t or "") for t in text_fields).lower()
    if any(p in blob for p in _NONWL_PATTERNS):
        return False
    if line_item_type and line_item_type not in _WL_TYPES:
        return False          # authoritative enum says non-consumption
    return True

def _nonworkload_sql(*col_exprs):
    """Build a SQL boolean that is TRUE when the row is NON-workload, from the
    pattern list — so the spreadsheet/flat-CSV SQL CASEs stay in sync with the JSON."""
    ors = []
    for col in col_exprs:
        for p in _NONWL_PATTERNS:
            ors.append(f"{col} ILIKE '%{p}%'")
    return "(" + " OR ".join(ors) + ")"
```

### 3. Rewire each parser to use the single source
- **raw CUR (`_workload_expr`, ~966):** already correct — leave the LineItemType enum
  logic, but build its marketplace/non-workload exclusion from `_nonworkload_sql(...)` so
  it uses the same pattern list (it already uses the enum; just confirm the marketplace
  side references the shared list, don't regress it).
- **spreadsheet (~1163):** replace the hand-written `Category ILIKE support/.../Registrar`
  CASE with: `CASE WHEN {_nonworkload_sql(cat_col, meter_col)} THEN FALSE ELSE TRUE END`.
- **flat CSV (~1660):** replace the marketplace/support-only CASE with
  `CASE WHEN {_nonworkload_sql(c(['service']), c(['description']))} THEN FALSE ELSE TRUE END`.
- **PDF (~1575):** replace the inline keyword list with
  `is_workload = _is_workload_py(None, desc, product)` (PDF has no LineItemType, so pass
  None; the pattern list covers the keywords it had).
- **usage-units (~line 1290 area, if it hardcodes is_wl=True):** compute via
  `_is_workload_py(None, service, usage_str)` instead of a blanket True.

### 4. Tests — `tests/test_ingest_is_workload.py` (new)
Assert the SAME verdict across formats for the same economic row:
- A **Credit** row → `is_workload=False` in raw-CUR, spreadsheet, flat-CSV, and PDF inputs
  (build a tiny input of each shape; the flat-CSV case is the one that regresses today).
- A **Tax** / **RIFee** / **SavingsPlanRecurringFee** row → False in every format.
- A normal **EC2 Usage** row → True in every format.
- A **Marketplace** product with LineItemType=Usage → False (marketplace override).
Also a unit test of `_is_workload_py` and `_nonworkload_sql` directly.

### 5. (Follow-on, SEPARATE) classify_mechanics trusts the flag
Only AFTER the above lands and is green: in `classify_mechanics.py`, have the
`non_workload` rule also honor `row["is_workload"] is False` (route to non_workload),
so classify stops re-deriving workload-ness from its own patterns. That is a
routing-change in classify — do it as its own step with test updates + shadow-diff. (Do
NOT bundle it into this ingest work order.)

## Acceptance
- New test passes; existing ingest tests still pass.
- The four parsers produce identical `is_workload` for Credit/Tax/RIFee/Usage/Marketplace.
- `git diff` touches ingest.py (the four CASEs + helpers) and the new data + test files.
- Report: which parser verdicts changed (the flat-CSV credit/tax case especially).
