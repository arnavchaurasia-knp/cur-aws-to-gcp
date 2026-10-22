# WORK ORDER — Two validator autofixes undo each other; the validator fails 10 of 11 bills

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/validate_fix.py` only.** You are not changing any mapper,
rate, SKU, or ingest. **No dollar total may move.** This work order changes
which rows are *called* violations and stops one autofix undoing another.

---

# STATUS: IMPLEMENTED — but the loop still runs. One correction needed.

The shared predicate, the `no_rate_in_region` stamping, the gate exclusion and
all five tests landed, and the unit test
`test_restore_and_downgrade_cannot_disagree` **passes**. The component-matching
bug you caught in the restore `UPDATE` (adding
`component IS NOT DISTINCT FROM ?`) was a good find beyond the spec — keep it.

**But on real bills the loop is unchanged.** Measured across the rebuilt
corpus, `restored` still equals `downgraded` on every bill:

```
job10  restored=9  downgraded=9      job8  restored=9  downgraded=9
job11  restored=9  downgraded=9      job9  restored=8  downgraded=8
job6   restored=8  downgraded=8      job7  restored=7  downgraded=7
job2   restored=3  downgraded=3      job3  restored=2  downgraded=2
job14  restored=2  downgraded=2      job5  restored=1  downgraded=1
PASS=1  FAIL=11
```

The counts dropped (34 → 9, 30 → 8), so the predicate helped. It did not close
the loop.

## 0. CORRECTION — the predicate is reading a passthrough parity amount

`_has_usable_rate_clause()` tests:

```python
(p.gcp_projected_cost IS NOT NULL AND p.gcp_projected_cost <> 0)
```

**For a passthrough row, `gcp_projected_cost` is the AWS cost**, carried at
parity by the `passthrough_rank` dedup — it is not evidence that a GCP rate
exists. The rows still looping prove it; every one has **no SKU id at all**:

```
comp=transfer  sku_id=None  gcp_cost=69.62  aws=69.62
comp=core      sku_id=None  gcp_cost=72.77  aws=72.77
comp=core      sku_id=None  gcp_cost=98.11  aws=98.11
```

`gcp_cost == aws` exactly. The predicate sees a non-zero number, autofix A
promotes the row to `map`, it then prices to $0 because there is no SKU, and
autofix B demotes it again.

**This is why the unit test passes and production does not.** The test fixture
sets `gcp_projected_cost` from a rate; on a real bill the same column holds a
parity amount for exactly the rows being tested. The test is not wrong — it is
testing a state that cannot occur for these rows.

### C1 — Require a SKU before believing a cost

A row cannot have a reachable rate without a SKU. Add that to the shared
predicate so both autofixes inherit it:

```python
(m.gcp_sku_id IS NOT NULL
 AND p.gcp_projected_cost IS NOT NULL
 AND p.gcp_projected_cost <> 0)
```

On this corpus the SKU check alone closes the loop, because every looping row
has `gcp_sku_id IS NULL`. Keep the cost test as well — a resolved SKU with no
rate in region is the other half of the case.

**Do not instead compare `gcp_projected_cost` to `aws_amortized_cost` to detect
parity.** A real rate can legitimately land within rounding of the AWS cost,
and you would reject it.

### C2 — The test must use a fixture that can actually occur

Extend `test_restore_and_downgrade_cannot_disagree` with a third row matching
the production shape: `strategy='passthrough'`, **`gcp_sku_id = NULL`**, and
`gcp_projected_cost` set equal to `aws_amortized_cost`. Assert it is **not**
promoted.

That row is what the current test is missing, and it is the only reason this
shipped looking correct.

### C3 — Verify against bills, not only the fixture

A unit test cannot confirm this one. Re-run the validator across the corpus and
confirm:

- `restored` and `downgraded` are **no longer equal** on any bill — expect
  `restored` to fall to near zero, since these rows never had a rate;
- the `passthrough_on_mappable_service` count falls with it, because the rows
  now keep their `no_rate_in_region` reason and the gate excludes reasoned
  passthrough;
- failures drop well below 11 of 12 — **and job1 still passes, while any bill
  with genuinely unexplained passthrough still fails.** A validator that passes
  everything is the worse outcome.
- **no dollar total moves.**

---

## 1. The problem in plain English

`validate_fix.py` applies autofixes and then runs gates. Two of those autofixes
have opposite policies and operate on the same rows:

**Autofix A — "restore illegal passthrough"** (~line 628). Finds rows that are
`strategy='passthrough'` on a service we normally map, and promotes them to
`strategy='map'`. Its test for "can we map this?" is:

```python
if gcp_service and gcp_sku_name:        # a NAME is present
```

**Autofix B — "downgrade map-with-no-rate"** (~line 1106). Finds rows that are
`strategy IN ('map','break_down')` whose projected cost is NULL or 0, and
demotes them back to `strategy='passthrough'`.

A promotes rows that have a SKU **name**; B demotes rows that have no SKU
**rate**. A row with a name but no reachable rate satisfies both. A promotes
it, B immediately demotes it, and the gate then counts it as a violation.

### The evidence — every bill in the corpus

```
job     restored  downgraded  passthrough_on_mappable_service  result
job10        34        34                 40                   FAIL
job11        34        34                 40                   FAIL
job6         31        31                 33                   FAIL
job7         30        30                 32                   FAIL
job8         30        30                 36                   FAIL
job9         28        28                 34                   FAIL
job2          3         3                398                   FAIL
job3          2         2                  2                   FAIL
job14         2         2                  2                   FAIL
job5          1         1                  2                   FAIL
job13         0         0                  1                   FAIL
job1          0         0                  0                   PASS
```

**`restored == downgraded` on every single bill.** Not approximately — exactly.
Every row promoted by A is demoted by B before the gates run.

**10 of 11 real bills FAIL the validator.** The one that passes is the smallest
bill in the corpus ($1,217).

The arithmetic closes perfectly on job8: A restored 30 and reported
`6 mappable passthrough row(s) could not be auto-restored`; 30 + 6 = 36, and
the gate reports exactly `passthrough_on_mappable_service=36`. The two
autofixes cancel, and the gate fails the bill on the full original set.

### Why A is wrong, not B

B is correct. Look at what A's own repair log says it is promoting:

```
[repair] c80b174884f7… passthrough→map (Amazon Elastic Compute Cloud / T2A Arm core)
[repair] eaae8b0f814f… passthrough→map (Amazon Elastic Compute Cloud / no GCP rate found in region core)
```

A SKU **name** was resolved; no **rate** exists for it in that region. For such
a row `passthrough` is the correct answer — carry the AWS cost and say why.
Promoting it to `map` would produce a row that prices to zero, which is exactly
the silent-under-projection class the gates exist to catch.

So the defect is in **A's predicate** ("a name exists") and in **the gate's
premise** (that passthrough on a mappable service is always wrong). A row with
no reachable rate is a *legitimate* passthrough, not a violation.

---

## 2. Tasks

### T1 — A must test for a reachable rate, not a name

In the restore autofix (~628-655), promote a row only when a **rate** can
actually be resolved for its SKU in its region. A resolved `gcp_sku_name` with
no rate is not a mappable row.

The rate lookup already exists — `apply_rates.py` resolves rates into
`gcp_sku_rates`, and the projection carries `gcp_projected_cost`. Use the same
source of truth the downgrade autofix uses, so the two cannot disagree:

```sql
-- promote only if a non-null, non-zero rate is reachable for this row
JOIN gcp_projection p
  ON p.aws_li_key = m.aws_li_key
 AND p.component IS NOT DISTINCT FROM m.component
WHERE ... AND p.gcp_projected_cost IS NOT NULL AND p.gcp_projected_cost <> 0
```

**The two autofixes must share one predicate for "has a usable rate".** Extract
it into a single helper used by both, so this cannot drift apart again. That
shared helper is the real deliverable of this task.

Rows that fail it keep `strategy='passthrough'` and must be stamped with a
reason that says so — use the existing `passthrough_reason` vocabulary in
`data/passthrough_reasons.json`, adding a value such as `no_rate_in_region` if
none fits. Do not invent a parallel mechanism.

### T2 — The gate must not count legitimate passthrough as a violation

`passthrough_on_mappable_service` currently counts every passthrough row on a
mappable service. After T1 those rows carry a reason explaining that no rate is
reachable. The gate must **exclude rows whose passthrough is reasoned and
intentional** and count only rows that are passthrough with *no* stated reason —
i.e. an unexplained passthrough, which is the real defect this gate was built
for.

There is precedent in the file: `intentional_passthrough_exclude_clause()` is
already used by autofix A's own query. Reuse it rather than writing a new
exclusion.

**Report the excluded rows.** They are not violations, but they are not nothing
either: they are workload we cannot price in that region. Emit them as a
diagnostic (`diag(WARN, ...)` — the file already imports it) with the row count
and dollar exposure, so a bill that is 30% unpriceable is visible even though
it passes.

### T3 — Do not let a repair log line print for a repair that will be undone

A's `[repair] ... passthrough→map` lines currently print for rows that B
reverts seconds later. After T1 this stops happening naturally. Confirm it
does: the log must not claim a repair that does not survive the run.

### T4 — Tests

In the validator's test file (find it under `tests/` — look for existing
`validate_fix` coverage):

1. `test_no_rate_row_is_not_promoted` — a passthrough row on a mappable service
   whose projection cost is NULL stays `passthrough` and gains a reason.
2. `test_rated_row_is_promoted` — the same row *with* a non-zero projected cost
   is promoted to `map`.
3. `test_restore_and_downgrade_cannot_disagree` — over a mixed fixture, the set
   promoted by A and the set demoted by B are **disjoint**. This is the
   regression that matters most; it is the invariant the bug violated.
4. `test_reasoned_passthrough_not_counted_as_violation` — a passthrough row
   with `no_rate_in_region` does not appear in
   `passthrough_on_mappable_service`.
5. `test_unexplained_passthrough_still_violates` — a passthrough row with no
   reason still counts, so the gate keeps its teeth.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/validate_fix.py
```

- Record the test count first; confirm it rises by exactly 5.
- **No dollar total moves.** `strategy` changes affect how rows are *labelled*,
  and a passthrough row already carries AWS cost — if any bill's GCP or AWS
  total changes, you have altered pricing, which is out of scope.
- Re-run the validator across the corpus. Expected after the fix:
  - `restored` and `downgraded` are **no longer equal** — in most bills
    `restored` drops to near zero, because those rows never had a rate.
  - bills that fail drop from **10 of 11** to only those with genuinely
    unexplained passthrough.
  - a new diagnostic reports unpriceable workload per bill.
- **Do not declare success on "FAIL became PASS" alone.** A gate that passes
  because it stopped looking is worse than one that fails loudly. Check that
  T4.5 still fails an unexplained passthrough.

---

## 4. Do NOT

- Do **not** "fix" this by deleting autofix B. B is correct — it prevents rows
  that price to zero from being called `map`.
- Do **not** make the gate pass by widening the intentional-passthrough
  exclusion to everything. Only rows with a *stated, reasoned* passthrough are
  excluded (T2).
- Do **not** change any mapper, rate or SKU resolution to make rates appear.
  The missing Arm/regional rates are a **separate** issue (§5).
- Do **not** change `passthrough_budget` or any other gate's thresholds to get
  a pass.
- Do **not** touch dollar amounts anywhere.

---

## 5. Out of scope — but note it in your summary

The underlying reason so many rows have a name and no rate: **GCP Arm (T2A/C4A)
and some regional SKUs have no reachable rate in the regions these bills use.**
A's repair log names it directly — `T2A Arm core`,
`no GCP rate found in region core`. That is a catalog-coverage problem, not a
validator problem, and it is why these rows exist at all. Fixing it would
genuinely reduce the passthrough count; this work order only stops the
validator lying about it.

Also noted and not addressed here: `[WARN] bill_total not found in input —
reconciliation skipped` appears on these bills, so the reconciliation gate does
not run at all.

---

## 6. Done

- [ ] One shared "has a usable rate" predicate, used by both autofixes.
- [ ] A promotes only rate-backed rows; the rest stay passthrough with a reason.
- [ ] The gate excludes reasoned passthrough and still catches unexplained ones.
- [ ] Unpriceable workload is reported as a diagnostic with rows and dollars.
- [ ] `restored` and `downgraded` sets are provably disjoint (test 3).
- [ ] 5 new tests pass; full suite passes at your recorded baseline + 5.
- [ ] **No bill's AWS or GCP total changed.**
