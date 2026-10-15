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
