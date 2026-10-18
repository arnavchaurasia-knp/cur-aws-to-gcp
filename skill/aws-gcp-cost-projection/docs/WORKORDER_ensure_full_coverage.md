# WORK ORDER — Harden `ensure_full_coverage.py` (Phase 2 coverage backstop)

**For an implementing AI / engineer with NO prior context. Read fully. Four fixes,
independent, do in the given order, stop/report per item. This is HARDENING, not a
rewrite — the file's design (a thin, idempotent, honest last-resort net) is correct and
must stay that way.**

## File under change
`skill/aws-gcp-cost-projection/scripts/ensure_full_coverage.py`
(~105 lines). It is the LAST PostLLMScript in Phase 2 (`orchestrate.go`, run as
`?scripts/ensure_full_coverage.py $DB`). Its job: find every `aws_li_catalog` row with no
`aws_li_to_gcp_li` mapping and insert ONE passthrough/ignore row so the report never dies
over a handful of rows the LLM dropped.

## Project rules that constrain these fixes
- **Honest fallback only** — never invent a GCP number; unmapped → carry AWS cost 1:1 as
  `passthrough` and flag for manual review, OR `ignore` ($0) for non-workload. Never blend
  into a confident number.
- **Pricing basis = On-Demand vs On-Demand** (not relevant to logic here, but do not add any
  commitment math).
- **Single source of truth for "is this real workload cost" = the `is_workload` column**, not
  a hardcoded list of mechanic_group names.
- Keep it **idempotent** (only inserts where no mapping exists) and **loud** (prints what it
  backstopped).

## Test command (run from `skill/aws-gcp-cost-projection/`)
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
There is currently no dedicated test file for this script — create
`tests/test_ensure_full_coverage.py` as part of FIX-1 and add cases per item below. Use an
in-memory / temp DuckDB fixture that creates minimal `aws_li_catalog` + `aws_li_to_gcp_li`
tables matching the INSERT's columns.

---

## FIX-1 — Make "never fatal" actually true (robustness)

**Problem:** the docstring promises *"Never fatal — prints what it backstopped and exits 0."*
Only the `import duckdb` is wrapped in try/except. The DB work (`connect` / `execute` /
`executemany` / `commit`) is NOT guarded. A locked DB, a schema drift (e.g. an older job DB
missing the `gcp_sku_unit` column), or any DuckDB error throws → non-zero exit → the one
script whose purpose is "don't let a small gap kill the report" kills the report.

**Fix:** wrap the body of `main()` (everything after reading `db = sys.argv[1]`) in a
try/except that prints the error to stderr and `sys.exit(0)`. Keep the existing prints inside
the try. Example shape:
```python
def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    db = sys.argv[1]
    try:
        con = duckdb.connect(db)
        ... existing logic ...
    except Exception as e:
        sys.stderr.write(f"ensure_full_coverage: non-fatal error ({e}); skipping\n")
        sys.exit(0)
```

**Test:** point the script at a DB whose `aws_li_to_gcp_li` table is missing a column the
INSERT needs → assert it exits 0 (does not raise) and prints the skip message.

---

## FIX-2 — Decide ignore-vs-passthrough from `is_workload`, not a hardcoded group list (correctness + architecture)

**Problem:** the row currently decides "ignore or passthrough?" purely from:
```python
_IGNORE_GROUPS = ("commitment_discount", "negative_cost")
```
Everything NOT in that 2-item tuple is carried as a `passthrough` AWS cost onto the GCP side.
But whether a cost is real GCP-bound workload is already answered authoritatively by the
`is_workload` column. Re-deriving it from group NAMES is a second, weaker copy of that policy
and silently misclassifies any non-workload row whose group isn't in the tuple.

**Scenario it breaks on:** `map_non_workload` in `apply_static_mappings.py` drops an
Enterprise Support row (`mechanic_group='non_workload'`, `is_workload=false`, e.g. $58K). It
reaches this backstop. `non_workload` is not in `_IGNORE_GROUPS` → it is carried as a
**$58K passthrough onto the GCP side** — a non-workload charge inflating the GCP total.

**Fix — drive the decision from `is_workload`, keep the group list as a secondary signal, and
guard negatives:**
```python
def _is_ignore(is_workload, mechanic_group, cost):
    # Authoritative: non-workload cost never lands on the GCP side.
    if not bool(is_workload):
        return True
    # Belt-and-suspenders: known ignore groups even if is_workload got stamped true.
    if mechanic_group in _IGNORE_GROUPS:
        return True
    # A negative cost (refund/credit) mis-grouped into misc must not become a
    # negative GCP passthrough that silently offsets the total.
    if (cost or 0) < 0:
        return True
    return False
```
Use `_is_ignore(is_workload, mechanic_group, cost)` to choose BOTH the `gcp_service` label
and the `strategy`:
- ignore  → `gcp_service="Non-workload / Commitment (ignored)"`, `strategy="ignore"`
- else    → `gcp_service="Manual Review — Unmapped"`, `strategy="passthrough"`

Note the SELECT already returns `aws_amortized_cost` (currently unpacked as `_cost`) — pass it
into `_is_ignore`. Keep `_IGNORE_GROUPS` defined (now a secondary check); do NOT delete it.

**Tests:**
- `non_workload` row, `is_workload=false`, cost $58K → inserted as `strategy='ignore'`
  (NOT passthrough), GCP cost not carried.
- ordinary `misc` row, `is_workload=true`, cost $10 → `strategy='passthrough'`, label
  "Manual Review — Unmapped".
- `commitment_discount` row with `is_workload=true` (mis-stamped) → still `ignore` via the
  group check.
- a `misc` row with cost = -$200 (mis-grouped refund) → `ignore`, not a negative passthrough.

---

## FIX-3 — Surface backstopped rows in `data_gaps.json` (observability)

**Problem:** these rows are the pipeline's genuine "could not map" cases, but the script only
writes a `projection_note` string on each row. The report's loud-failure block and the planned
reviewer layer expect a structured list in `data_gaps.json` (the H-5 design). Today they'd
have to scrape notes to find them.

**Fix:** after a successful insert, append the backstopped rows to
`<job_dir>/projection-audit/data_gaps.json` (resolve the path relative to the DB file's
directory — the DB lives at `<job_dir>/projection-audit/projection.duckdb`, so write the JSON
next to it). Read-existing-then-merge so it is additive and idempotent:
```python
# pseudo:
gaps_path = <dir of db>/data_gaps.json
existing = json.load(gaps_path) if exists else []
existing_keys = {g["aws_li_key"] for g in existing}
for row in backstopped_rows:
    if row.aws_li_key not in existing_keys:
        existing.append({
            "aws_li_key": ..., "product": ..., "cost": ...,
            "mechanic_group": ..., "reason": "LLM left row unmapped after Phase 2 retry",
            "resolution": "ignore" or "passthrough",   # match FIX-2's decision
            "source": "ensure_full_coverage.py",
        })
json.dump(existing, gaps_path)
```
Wrap this whole block in its own try/except that only warns on failure — writing data_gaps
must NEVER make the backstop fatal (respects FIX-1's contract). If `data_gaps.json` has a
different established schema elsewhere in the repo, MATCH that schema instead of the shape
above — grep for `data_gaps` first and conform.

**Test:** run the backstop on a DB with 2 unmapped rows → `data_gaps.json` exists and contains
those 2 keys with the right `resolution`; run it again → still 2 entries (idempotent, not
duplicated).

---

## FIX-4 — Minor cleanups (low)

**4a — skip zero-cost rows from the loud summary count.** $0 unmapped rows still get a
mapping row (fine, keep inserting them so the coverage gate stays satisfied), but they dilute
the "backstopped N rows totaling $X" signal. In the printed summary, report zero-cost rows
separately, e.g. `"... (K of them zero-cost)"`, so a reviewer sees the real dollar exposure at
a glance. Do NOT stop inserting them — the gate needs every row mapped.

**4b — confirm column/placeholder parity after FIX-2.** The INSERT lists 12 columns and 12
placeholders; the record tuple must stay 12 elements. After editing the tuple construction in
FIX-2, re-verify the count by eye and with a smoke insert in the test.

**Test:** a mix of $0 and non-$0 unmapped rows → summary line reports the zero-cost count
separately; smoke insert succeeds (parity intact).

---

## Explicitly OUT OF SCOPE (do not do)
- Do NOT turn this into a "smart" mapper or add any heuristic GCP mapping — it stays a
  passthrough/ignore net by design. Real mapping belongs upstream (specs / LLM).
- Do NOT remove `_IGNORE_GROUPS` — FIX-2 keeps it as a secondary check.
- Do NOT add commitment/discount math.

## Architecture note (context, not a task)
This file should SHRINK over time, not grow. Every row it touches is an upstream LLM drop. As
the spec-dispatch cutover makes the `misc` group deterministic, fewer rows are LLM-mapped, so
fewer rows reach this net. Keep it thin; invest in making it *needed less*, not smarter.

## Report back (per item)
Files changed, the test added, pytest result (all existing tests must stay green), and
anything that required a decision you could not make (e.g. an existing `data_gaps.json` schema
you had to conform to).

---

# ADDENDUM (2026-10-15) — issues found AFTER FIX-1..4 were implemented

FIX-1..4 are in and correct. A follow-up review (verified against the code AND the existing
tests) found two confirmed bugs that the CURRENT tests actively lock IN (they assert the wrong
behavior). A third suspected issue (NULL `is_workload`) was traced and RETRACTED — see note.
Context you need: `classify_mechanics.py` ALSO writes `data_gaps.json` (rows shaped
`{canonical, product, cost}`, NO `aws_li_key`), running in Phase-2 PRE-LLM; this backstop runs
Phase-2 POST-LLM and MERGES into that file; `render_report.py:~1632-1641` reads the file's
top-level `total_cost`/`count` and renders a customer-facing banner: *"$X could not be
classified — passed through at 1:1 AWS cost parity."*

## FIX-5 — HIGH-value — the swallowed-INSERT path reports FALSE success
**Problem:** FIX-1 correctly wrapped `con.executemany` in its own try/except so a schema-drift
DB can't make the script fatal. But when that INSERT is swallowed (L112-113), execution
CONTINUES and still (a) prints `"backstopped N row(s) totaling $X … loud, not silent"`
(L115-124) and (b) writes those rows into `data_gaps.json` with `resolution: "passthrough"/
"ignore"` (L126-169) — even though **nothing was inserted into `aws_li_to_gcp_li`**. So on an
INSERT failure the logs claim success, `data_gaps.json` marks the rows resolved, yet the DB
still has them unmapped → the mapping_coverage gate fails anyway (or the report ships missing
rows), and every surfaced signal says the opposite of what happened. "Loud, not silent" becomes
"loudly wrong." The EXISTING test `test_fix1_missing_column_exits_zero` exercises exactly this
path but only asserts exit 0 + the stderr line — it does not catch the false "backstopped"
claim or the false data_gaps entries.
**Fix:** track `insert_ok = False`; set it True only after `con.commit()` succeeds. Then:
- Only print the `"backstopped …"` success summary when `insert_ok`.
- When NOT `insert_ok`, print a LOUD failure line (e.g. `"ensure_full_coverage: INSERT FAILED —
  N row(s) totaling $X remain UNMAPPED; coverage gate will flag them"`).
- Still write the rows to `data_gaps.json`, but with `resolution: "INSERT_FAILED"` (not
  passthrough/ignore) so the gap is surfaced truthfully, not falsely cleared.
**Test (must fail before the fix):** UPDATE `test_fix1_missing_column_exits_zero` (or add a
sibling) to assert that on the broken-schema path the stdout does NOT contain "backstopped",
DOES contain the INSERT-FAILED notice, and that any `data_gaps.json` written carries
`resolution == "INSERT_FAILED"` for those rows.

## FIX-6 — MED — the data-gap banner $ counts ignore rows and can double-count
**Two confirmed defects in the merged `data_gaps.json` total that `render_report` shows as
"could not be classified — passed through at 1:1":**

**(6a) `ignore` rows are counted as a passed-through data gap.** This backstop writes its
`ignore` rows (commitment/non-workload) into `data_gaps.json` and the recomputed `total_cost`
sums them (confirmed: `test_fix3_data_gaps_created_and_idempotent` asserts `total_cost ==
420.50`, which includes a $300 `non_workload` ignore row). Those rows are $0 on GCP and are NOT
passed through, so the banner dollar is overstated by every ignore row's cost, and the "passed
through at 1:1" wording is false for that portion.
**Fix:** exclude `resolution == "ignore"` rows from the `count`/`total_cost` that drive the
banner. Keep ignore rows in a separate list if you want them recorded (e.g. a sibling
`ignored` array), but they MUST NOT inflate the "could-not-classify / passed-through" figure.

**(6b) Double-count across the two writers.** Dedup builds `existing_keys` only from rows that
HAVE an `aws_li_key` (L139-142). `classify_mechanics.py`'s rows have NO `aws_li_key`, and those
rows are `misc` rows handed to the LLM — i.e. exactly the rows the LLM can drop, which is when
THIS backstop also writes them. The same line item then appears twice (once keyed, once not) →
`total_cost` counts it twice in the banner. (`test_fix3_data_gaps_additive_merge_with_existing`
only tests non-overlapping rows, so it misses this.)
**Fix:** give the two sources a common identity so the union can de-duplicate. Preferred: have
`classify_mechanics.py` include the row's `aws_li_key` in its data_gaps entries (one-line change
there — it already has the row), and dedupe on `aws_li_key` across BOTH sources. If you cannot
touch classify in this unit of work, dedupe on a fallback identity (`aws_li_key` if present else
`(product, round(cost,2))`) and note the coupling. Either way, a row flagged by both must be
counted ONCE.
**Test (must fail before the fix):** (i) a bill with one passthrough row + one ignore row →
assert the banner-driving `total_cost` equals ONLY the passthrough row's cost (ignore excluded);
(ii) seed `data_gaps.json` with a classify-style entry (no `aws_li_key`) whose
`(product, cost)` matches a row this backstop will also write → assert the final `total_cost`
counts it once, not twice.

## RETRACTED — NULL `is_workload` guard (was proposed FIX-7)
Traced fully: all 7 `INSERT INTO aws_li_catalog` paths in `ingest.py` populate `is_workload`
with a non-null TRUE/FALSE (CASE `ELSE`, `_is_workload_py` bool, or literal `false`); there is
no `UPDATE … SET is_workload` and no non-ingest writer of the table. So `is_workload` is never
NULL on a real row, and the `_is_ignore(None, …) → True` branch is unreachable defensive code.
NOT a bug — do not add a guard, do not "fix" the test that exercises the None input.

## Report back (addendum)
For FIX-5 and FIX-6: files changed (note if `classify_mechanics.py` was touched for 6b), the
test added AND confirmation it failed before the fix, and pytest result (all green). The two
existing tests that assert the wrong behavior (`test_fix3_data_gaps_created_and_idempotent`'s
`total_cost == 420.50`, and the swallowed-INSERT assertions) must be UPDATED to the corrected
expectations, not left as-is.
