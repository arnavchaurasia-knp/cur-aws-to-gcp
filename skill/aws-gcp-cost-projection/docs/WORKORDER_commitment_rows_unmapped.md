# WORK ORDER — Commitment-fee rows are silently unmapped: a guard reads a column its query never fetched

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/apply_static_mappings.py`, plus one `is_workload` rule in
`scripts/ingest.py`.** You are not changing any rate, SKU or mapper logic.
Expect the GCP total to stay **identical** and the affected rows to become
explicitly `ignore` instead of silently absent.

---

## 1. The problem in plain English

AWS Savings Plan and Reserved Instance **recurring fees** are commitment
payments. Their cost is already baked into the discounted rates on the usage
lines, so projecting them again would double-count. The pipeline has a script
for exactly this — `apply_commitment_ignores.py` — which marks them
`strategy='ignore'`.

**It works. Then the next script throws its output away.**

Traced end to end on a real bill (`job8`, an .xlsx export):

```
1. classify_mechanics.py      -> 6 rows get mechanic_group='commitment_discount'   OK
2. apply_commitment_ignores.py-> writes 6 ignore rows to
                                 projection-audit/mappings/commitment_discount_mappings.json   OK
3. apply_static_mappings.py   -> map_commitment_discount() REJECTS all 6:
      "WARNING: map_commitment_discount received non-commitment row:
       ac521891333f… (: Savings Plans for AWS Compute usage) — routing to LLM/passthrough"
   ...and OVERWRITES commitment_discount_mappings.json with []
4. merge_mappings.py          -> inserts nothing
5. result                     -> rows have NO entry in aws_li_to_gcp_li at all
```

Those rows then carry AWS cost and contribute **$0** to the GCP side — a
silent under-projection that makes the migration look cheaper than it is.

Measured across the corpus: **job8 and job9, 6 rows each, $850 total**, every
one `is_workload = true` and completely unmapped.

### Why the guard rejects them

`map_commitment_discount` (~line 4281) decides whether a row really is a
commitment row:

```python
line_type = (r.get("line_item_type") or "")
blob = f"{line_type} {ut} {product}".lower()
is_commitment = any(tok in blob for tok in _COMMITMENT_TOKENS) or cost < 0 ...
```

`_COMMITMENT_TOKENS` includes `"savingsplanrecurringfee"` and `"savingsplan"`.
The rows' `line_item_type` **is** `SavingsPlanRecurringFee`, so this should
match instantly.

**But the query that builds these row dicts never selects `line_item_type`.**
The SELECT at ~line 8764 fetches 20 columns and that is not one of them, so
`r.get("line_item_type")` is always `""` and the blob becomes:

```
blob today    : " aps3-ec2sp:m6a.1yrnoupfront savings plans for aws compute usage"
blob with fix : "savingsplanrecurringfee aps3-ec2sp:m6a.1yrnoupfront savings plans..."
```

Today's blob contains `"savings plans"` **with a space**, which does not match
the token `"savingsplan"`. Nothing matches, the guard rejects, the file is
clobbered. Note the empty slot before the colon in the warning text —
`(: Savings Plans…)` — that blank *is* the missing `line_item_type`, printed
every run by the warning itself.

### Two independent faults

1. **A guard reads a field its own query does not fetch.** It has therefore
   never worked; it rejects every row it is given.
2. **Two scripts write the same file, and the second clobbers the first
   unconditionally** — even when it produced nothing.

Either fault alone would be survivable. Together they delete valid mappings.

---

## 2. Tasks

### T1 — Fetch `line_item_type` (read this carefully; the obvious edit corrupts data)

The row dicts are built by a **positional zip**:

```python
r = dict(zip(cols, raw))
```

The SQL column order (~8764) and the `cols` list (~8779) must correspond
exactly. **Adding a column to the SELECT without adding it to `cols` at the
same index silently shifts every field after it** — `region` would hold
`operation`, `instance_vcpus` would hold `operating_system`, and the pipeline
would keep running while producing nonsense.

So: append `line_item_type` to the **end** of the SELECT list **and** to the
**end** of `cols`. Appending leaves every existing index untouched, which is
the only edit that cannot misalign anything.

```sql
..., {_os_expr}, {_lic_expr}, line_item_type
```
```python
..., "operating_system", "license_model", "line_item_type"]
```

Then verify by printing one row dict and confirming `region` still holds a
region and `line_item_type` holds the line item type.

### T2 — A mapper must not clobber a group file it did not produce

Even with T1, the structural fault remains: if `map_commitment_discount`
legitimately returns zero rows, it still overwrites a file another script
owns.

In the write loop that emits `<group>_mappings.json`, **do not overwrite an
existing non-empty file with an empty result.** Either skip the write when the
mapper produced no rows and a file already exists, or merge by `aws_li_key`.

Prefer skipping — it is simpler and cannot produce duplicate keys. Log one
line when it happens, so a silently-skipped write is still visible:

```
commitment_discount: 0 rows mapped; keeping existing mappings file (N rows from apply_commitment_ignores.py)
```

**Do not** solve this by removing `commitment_discount` from
`apply_static_mappings`'s group list. The two scripts handle different row
shapes — negative-cost commitment rows reach the mapper legitimately — and
dropping the group would break those.

### T3 — `SavingsPlanRecurringFee` must not be workload in any bill format

Separate but same rows. The CUR path has an explicit allow-list:

```python
_WL_TYPES = {'DiscountedUsage', 'SavingsPlanCoveredUsage', 'Usage'}
```

`SavingsPlanRecurringFee` is not in it, so on a CUR these rows are
`is_workload = false`. The spreadsheet and flat-CSV paths compute
`line_item_type` and then derive `is_workload` from text patterns only,
never consulting it — so the identical economic row comes out
`is_workload = true`. That is how these rows entered the workload baseline at
all.

In both non-CUR branches of `ingest.py`, add the allow-list to the
`is_workload` CASE, alongside the negative-cost guard added previously:

```sql
CASE
    WHEN <cost> < 0 THEN FALSE
    WHEN <line_item_type_expr> NOT IN ('Usage','DiscountedUsage','SavingsPlanCoveredUsage') THEN FALSE
    WHEN {_nonworkload_sql(...)} THEN FALSE
    ELSE TRUE
END AS is_workload
```

Repeat the `line_item_type` expression rather than referencing the alias, as
the existing code does.

A helper already encodes this rule — `_is_workload_py` (~line 75). Use it as
the reference for what the SQL must express; if you can route the SQL through
the same constant (`_WL_TYPES`) rather than retyping the three strings, do.

### T4 — Tests

1. `test_commitment_guard_sees_line_item_type` — a row dict built by the real
   query path has a non-empty `line_item_type`, and
   `map_commitment_discount` accepts a `SavingsPlanRecurringFee` row.
2. `test_mapper_row_dict_fields_aligned` — after T1, a built row dict has a
   region in `region` and an instance type in `instance_type`. **This is the
   guard against the positional-zip trap and matters most.**
3. `test_empty_mapper_result_does_not_clobber` — an existing non-empty
   `commitment_discount_mappings.json` survives a mapper run that yields no
   rows.
4. `test_sp_recurring_fee_not_workload_spreadsheet` — a
   `SavingsPlanRecurringFee` row in the spreadsheet format comes out
   `is_workload = false`.
5. `test_sp_recurring_fee_workload_matches_cur` — the same economic row in CUR
   and spreadsheet formats produces the same `is_workload`. The cross-format
   test file already exists for exactly this invariant.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_static_mappings.py scripts/ingest.py
```

- Record the test count first; confirm it rises by exactly 5.
- Re-ingest a bill containing `SavingsPlanRecurringFee` rows (an .xlsx export
  works) and confirm:
  - `commitment_discount_mappings.json` is **not** `[]`,
  - those rows appear in `aws_li_to_gcp_li` with `strategy='ignore'`,
  - the `map_commitment_discount received non-commitment row` warning no
    longer appears for them,
  - they are `is_workload = false`.
- **Bill totals must not change.** These rows keep their AWS cost; only their
  workload flag and mapping change.
- The GCP projection total must not change either — `ignore` projects $0,
  which is what an absent mapping also produced. **If GCP moves, something
  else was wrong.**

---

## 4. Do NOT

- Do **not** add the column to the SELECT without adding it to `cols` at the
  same position (T1). Appending to both ends is the safe edit.
- Do **not** remove `commitment_discount` from `apply_static_mappings`'s group
  list (T2).
- Do **not** make these rows `passthrough`. A commitment fee has no GCP
  counterpart *and* its cost is already inside the discounted usage rates —
  passthrough would double-count it. `ignore` is correct.
- Do **not** change `_COMMITMENT_TOKENS`. The tokens are right; the input was
  empty.
- Do **not** touch the CUR path's `is_workload` (T3). It is already correct.

---

## 5. Out of scope — note in your summary

Audit the **other** mapper guards for the same fault: `map_commitment_discount`
reads `line_item_type`, and it was never fetched. Check whether any other
handler reads a key absent from the 20-column `cols` list — `r.get(...)`
returning `None` silently is the same failure, and the warning text printing an
empty value is the only symptom. A quick check is to grep the mapper bodies for
`r.get("...")` keys and diff that set against `cols`.

---

## 6. Done

- [ ] `line_item_type` fetched and positionally aligned; row dicts verified.
- [ ] `map_commitment_discount` accepts `SavingsPlanRecurringFee` rows.
- [ ] An empty mapper result never clobbers an existing group file.
- [ ] `SavingsPlanRecurringFee` is non-workload in every bill format.
- [ ] 5 new tests pass; full suite passes at your recorded baseline + 5.
- [ ] Bill totals and GCP totals unchanged; the 6 rows per bill now carry
      `strategy='ignore'`.
