# WORK ORDER — Mappers read four columns the query never fetches (one silently disables shipped work)

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/apply_static_mappings.py` plus one new test.** No rate, SKU
or pricing logic changes. The only dollar movement expected is on multi-month
bills carrying a flat monthly fee, where a fee currently priced **once per
bill** becomes priced **once per month** — which is the behaviour an earlier
work order already shipped and that this defect silently prevents.

---

## 1. The problem in plain English

Every static mapper receives rows as plain dicts, built here (~line 8764):

```python
rows = con.execute("SELECT aws_li_key, mechanic_group, product, usage_type,
                           pricing_unit AS unit, ... (20 columns) ...").fetchall()
cols = ["aws_li_key", "mechanic_group", ... 20 names ...]
r = dict(zip(cols, raw))
```

A mapper that asks for a key outside those 20 gets `None`. **Python does not
complain, the mapper does not complain, and the pipeline keeps running.** Any
condition built on that key silently evaluates as if the field were empty.

A full sweep of all 33 registered mappers — checking both `r.get("x")` and
`r["x"]` against the 20 fetched columns — finds **exactly four** such keys:

| key | mappers | effect |
|---|---|---|
| `billing_month` | `map_flat_hourly`, `map_shield` | **HIGH — silently disables the per-month fee dedup** |
| `line_item_type` | `map_commitment_discount` | HIGH — already covered by `WORKORDER_commitment_rows_unmapped.md` |
| `aws_cost` | `map_commitment_discount` | MEDIUM — a fallback that can never fire |
| `pricing_unit` | `map_managed_db` | **none — harmless redundancy**, clean up only |

### The serious one: `billing_month`

A previous work order made flat monthly fees (Cloud Armor Enterprise
enrollment, Shield Advanced subscription) price **once per billing month**
rather than once per bill, because pricing a $200/mo or $3,000/mo fee once
across a 12-month bill understates it by 11 months.

That code is correct. It reads:

```python
billing_month = r.get("billing_month") or "default"
...
if billing_month not in enterprise_enrollment_priced_months:
```

and

```python
months = {m for m in (r.get("billing_month") for r in rows) if m}
if not months:
    return 1, False
```

**`billing_month` is not one of the 20 fetched columns.** So in production:

- every row's `billing_month` is `None` → the dedup key is always `"default"`
- every month collapses into one bucket → **the fee is still priced once per
  bill**, exactly the bug that was fixed
- `months_covered()` always returns `(1, False)` → every bill is treated as
  "month unknown", so the explanatory note fires even on bills that state their
  months perfectly well

The unit tests pass because they construct row dicts directly with
`billing_month` set. They test the mapper; nothing tests that the mapper
*receives* the field. **The feature has never worked outside its tests.**

### `aws_cost` — a fallback that cannot fire

`map_commitment_discount` has:

```python
cost_unblended = float(r.get("aws_cost") or 0.0)
is_commitment = ... or cost < 0 or cost_unblended < 0
```

`aws_cost` is not fetched — and no such column exists in `aws_li_catalog` at
all (it carries `aws_amortized_cost` and `aws_amortized_cost_native`). So
`cost_unblended` is always `0.0` and the third test is dead code.

### `pricing_unit` — not a defect

`map_managed_db` reads `r.get("pricing_unit")`, which is `None` because the
SELECT aliases it to `unit`. But the same condition already tests
`unit_str in ("acu-hr", ...)` against the fetched `unit`, so behaviour is
correct today. It is only misleading to read. Treat it as cleanup, **not** as
a bug to "fix" by changing behaviour.

---

## 2. Tasks

### T1 — Fetch `billing_month` (mind the positional zip)

Row dicts are built by `dict(zip(cols, raw))`. **The SQL column order and the
`cols` list must correspond exactly.** Adding a column to the SELECT without
adding it to `cols` at the same index silently shifts every later field —
`region` would hold `operation` — and the pipeline would keep running on
nonsense.

**Append to the end of both**, which cannot disturb any existing index:

```sql
..., {_os_expr}, {_lic_expr}, billing_month
```
```python
..., "operating_system", "license_model", "billing_month"]
```

> If you are also applying `WORKORDER_commitment_rows_unmapped.md`, which
> appends `line_item_type` the same way, **append both and keep SQL and `cols`
> in the same order**. Do them in one edit and re-check the alignment once,
> rather than twice.

### T2 — Delete the dead `aws_cost` fallback

In `map_commitment_discount`, remove `cost_unblended` and its clause. Do
**not** add an `aws_cost` column to the query — no such column exists; adding
an alias for `aws_amortized_cost` would make the clause duplicate the `cost < 0`
test that is already there.

After `line_item_type` is fetched (the other work order), the token match
identifies these rows correctly and the negative-cost fallbacks are belt and
braces. Removing a clause that has always evaluated to `False` cannot change
behaviour — assert that in your summary.

### T3 — Clean up `pricing_unit`

In `map_managed_db`, drop the `pricing_unit_str` variable and its clause, which
duplicates the adjacent `unit_str` test against a key that is always `None`.
**Behaviour must not change** — `unit_str` already covers those values.

### T4 — The durable fix: make this class impossible to reintroduce

This is the main deliverable. Everything above is four symptoms of one gap:
**nothing checks that what mappers read is a subset of what the query fetches.**

Add a test that derives both sets from the source and compares them:

1. Parse the `cols` list from `apply_static_mappings.py` (or import it if you
   refactor it to a module-level constant — **preferred**, as parsing source in
   a test is brittle).
2. For every mapper registered in the `handlers` dict, collect every key read as
   `r.get("key")` or `r["key"]`.
3. Assert that set is a subset of `cols`, and fail with the offending
   key/mapper pairs named.

```
AssertionError: mappers read keys the row query does not fetch:
  billing_month -> map_flat_hourly, map_shield
```

Making `cols` an importable module-level constant used by both the query
builder and the test is the cleanest form — then the test compares live
objects, not regexes over source.

**Expected result on the current code: the test fails until T1-T3 are done,
then passes.** Write it first and watch it fail; a test of this kind that
passes immediately is usually not looking at what you think it is.

### T5 — Tests for the behaviour T1 restores

1. `test_billing_month_reaches_mappers` — a row dict built through the real
   query path for a flat_hourly row contains `billing_month`.
2. `test_monthly_fee_priced_per_month_end_to_end` — a bill with three distinct
   billing months and one enrollment-fee row per month yields **three** priced
   fees. This must exercise the real query path, not a hand-built dict —
   hand-built dicts are exactly what hid this.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_static_mappings.py
```

- Record the test count first; confirm it rises by exactly 3.
- Print one built row dict and confirm `region` holds a region,
  `instance_type` holds an instance type, and `billing_month` is present.
  **Do this before trusting any other result** — a misaligned zip produces
  plausible-looking nonsense.
- On a single-month bill, **every dollar is unchanged.**
- On a multi-month bill with a monthly fee, the fee is priced once per month.
  No corpus bill currently has non-empty `billing_month`, so this needs a
  synthetic fixture — say so in your summary rather than claiming corpus
  evidence.
- The "month unknown" note must stop appearing on bills that do state their
  months.

---

## 4. Do NOT

- Do **not** add a column to the SELECT without adding it to `cols` at the same
  position. Append to both (T1).
- Do **not** add an `aws_cost` column (T2). It does not exist.
- Do **not** change `map_managed_db`'s ACU detection behaviour (T3) — only
  remove the always-`None` clause.
- Do **not** satisfy T4 by hardcoding the four known keys into an allowlist.
  The test must derive the read-set, or it will not catch the fifth one.
- Do **not** skip T4 because T1-T3 fix today's instances. The gap is the issue;
  the four keys are symptoms.

---

## 5. Why this matters beyond the four keys

An earlier work order shipped a correct per-month dedup, it was reviewed, its
tests passed, and it changed nothing in production — because the data it reads
never arrives. The same shape produced `WORKORDER_commitment_rows_unmapped.md`,
where a guard rejected every row it was handed for the same reason.

The common factor is that `dict.get()` on a missing key is indistinguishable
from a field that is legitimately empty. T4 is the only task here that stops
the next instance.

---

## 6. Done

- [ ] `billing_month` fetched; row-dict alignment verified by inspection.
- [ ] Dead `aws_cost` clause removed; `pricing_unit` clause removed.
- [ ] A test derives mapper-read keys and asserts they are a subset of `cols`,
      and it failed before the fixes.
- [ ] Monthly fees price once per month on a multi-month fixture.
- [ ] 3 new tests pass; full suite passes at your recorded baseline + 3.
- [ ] Single-month bill totals unchanged.
