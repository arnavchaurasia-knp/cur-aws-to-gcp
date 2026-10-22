# WORK ORDER — The never-under-provision gate compares AWS against itself for 72% of capacity

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: the `capacity_reconciliation` gate in `scripts/validate_fix.py`, and
the line it logs.** No pricing, mapping or ingest changes. **No dollar figure
may move.** This work order changes what the gate counts as *verified*.

---

## 1. The problem in plain English

The pipeline makes a guarantee: **the GCP shape we project is never smaller
than the AWS instance it replaces.** The `capacity_reconciliation` gate is what
enforces it, summing AWS vCPU/RAM and comparing against the GCP side.

The GCP side is computed like this:

```sql
SELECT SUM(COALESCE(cm.vcpus, c.instance_vcpus * c.instance_count))
FROM aws_li_catalog c
LEFT JOIN core_mapped cm USING (aws_li_key)
WHERE c.is_workload AND c.instance_vcpus IS NOT NULL
```

`core_mapped` only contains rows that were broken into a `core` component with
`strategy IN ('map','break_down')`. **For every other row the `COALESCE` falls
back to the AWS vCPU count** — so the GCP figure *is* the AWS figure, the two
sides are identical by construction, and the comparison cannot fail.

### How much of the check is that fallback

Measured across the corpus:

```
job        real vCPU   fallback    fallback %
job2           3459      11834        77.4%
job8            104         66        38.8%
job9            104         64        38.1%
job6/job7        54      30-31        ~36%
job10/job11     273        118        30.1%
job3             85         20        19.1%
job14           356          4         1.1%

TOTAL          4777      12284        72.0%
```

**72% of the "GCP capacity" number is the AWS number copied across** — 413 rows
of 714.

This is why the validator prints lines like:

```
Capacity check: AWS 108.0 vCPU / 192.5 GB RAM vs GCP 108.0 vCPU / 192.5 GB RAM (vcpu_ok=True, ram_ok=True)
```

Exactly equal on both sides. That is not a match; it is the same number twice.

### It is not a harness artifact

The obvious objection is that these are rows my LLM-free corpus failed to map.
Checked — they are not:

```
strategy of the fallback rows:
  passthrough   398 rows   12258 vCPU
  (unmapped)     12 rows      17 vCPU
  map             3 rows       8 vCPU
```

**398 of 413 are `passthrough`** — rows that are passthrough in production too,
mostly the Arm families with no GCP equivalent in region. 99.9% of the
fallback vCPU is real.

### What is and is not wrong here

Assuming parity for a passthrough row is **defensible as a pricing
convention** — that is exactly what passthrough means, and it is stated.

It is **not defensible as a guarantee.** "We never under-provision" is a claim
about something checked. For 72% of capacity nothing was checked; the number
was assumed and then compared to itself. The gate is not detecting
under-provisioning on those rows — it is incapable of detecting it.

A second, smaller blindness: `WHERE ... instance_vcpus IS NOT NULL` excludes
**651 rows, $274,577**. Most of that is correct — storage, egress and
per-request services have no vCPU to compare. But it also silently excludes
instance rows whose specs failed to resolve, which are precisely the rows least
likely to have been sized correctly.

---

## 2. Tasks

### T1 — Stop substituting the AWS value for the GCP value

Split each row into one of three states instead of coalescing:

| state | meaning |
|---|---|
| `verified` | a mapped `core`/`ram` component exists; compare it to AWS |
| `unverified` | no GCP capacity figure exists for this row |
| `not_applicable` | the row has no instance capacity at all (`instance_vcpus IS NULL`) |

**An `unverified` row must never contribute an assumed value to the GCP total.**
That substitution is the defect.

### T2 — Report all three, and gate only on what was checked

```
Capacity check (verified rows only): AWS 30.0 vCPU vs GCP 32.0 vCPU — OK
  unverified:     398 row(s), 12,258 AWS vCPU carried at assumed parity
  not applicable: 651 row(s) with no instance capacity
```

- The pass/fail verdict comes from `verified` rows only — where a real
  comparison exists, so a shortfall is a genuine violation.
- `unverified` is reported with its row count and vCPU weight, through the
  existing `diag()` channel as well as the log line, so a bill where most of
  the capacity guarantee is assumed says so.
- Do **not** fail the run on `unverified`. Those rows are passthrough by
  design; the problem is silence, not their existence.

**The log line must never print a GCP total containing assumed values.** That
single line is what made this look fine for so long.

### T3 — Do not let `instance_vcpus IS NULL` hide instance rows

Rows with no capacity at all are genuinely `not_applicable`. But a row that
*is* an instance (it has an `instance_type`) and whose specs failed to resolve
is `unverified`, not `not_applicable` — it should appear in the unverified
count, not vanish.

Distinguish on `instance_type IS NOT NULL`, not on `instance_vcpus IS NOT NULL`.

### T4 — Tests

1. `test_unverified_row_not_counted_as_gcp_capacity` — a passthrough row with
   no mapped core contributes **nothing** to the GCP total. **This is the
   regression that matters most.**
2. `test_gate_fails_on_real_shortfall` — a mapped row whose GCP core is smaller
   than its AWS vCPU count produces a violation. Nothing currently proves the
   gate can fail at all.
3. `test_gate_passes_when_verified_rows_are_adequate` — verified rows meeting
   or exceeding AWS pass, regardless of how many unverified rows exist.
4. `test_unverified_count_reported` — the row count and vCPU weight of
   unverified rows reach the log and a diagnostic.
5. `test_unresolved_instance_is_unverified_not_na` — an instance row with NULL
   `instance_vcpus` counts as unverified (T3).

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/validate_fix.py
```

- Record the test count first; confirm it rises by exactly 5.
- **No dollar figure changes anywhere.**
- Run the validator across the corpus. Expected: the capacity line now shows a
  *smaller* verified AWS total (the 28% that was genuinely checked), the two
  sides are **no longer identical**, and a separate unverified count appears —
  roughly 398 rows / 12,258 vCPU corpus-wide.
- **Expect no new failures.** Where a real comparison exists today it already
  passes; this work order reveals how little was being compared, it does not
  change any mapping. If a bill newly fails the capacity gate, inspect the row
  before believing it — it is more likely a classification error in T1 than a
  genuine shortfall.

---

## 4. Do NOT

- Do **not** keep the `COALESCE` and merely relabel the output. Substituting
  the AWS value is the defect (T1).
- Do **not** fail the run on unverified rows. They are passthrough by design.
- Do **not** try to infer a GCP capacity for passthrough rows to make them
  verifiable. If we could size them, they would not be passthrough.
- Do **not** print a combined GCP capacity total that mixes measured and
  assumed values (T2).
- Do **not** change any mapper to reduce the unverified count. That is a
  different piece of work.

---

## 5. Why this one matters

This is the fifth defect of the same shape found in this codebase: a check that
cannot see the case it exists for.

- the unreachable-rate check filtered `gcp_sku_id IS NOT NULL`, missing rows
  with no SKU;
- the commitment guard read a column its own query never fetched;
- the reconciliation gate reads a file format its bills are not written in;
- the validator's rate predicate reads a passthrough parity amount as evidence
  of a rate;
- and this gate compares AWS against itself.

Each time the code read correctly, the tests passed, and the log said fine. The
common thread is a **default that silently stands in for a missing value** —
`COALESCE` here, `NULL`-exclusion there. The fix is always the same: make
"not known" a distinct, counted, reported state rather than a value that looks
like a result.

---

## 6. Done

- [ ] Rows are classified `verified` / `unverified` / `not_applicable`; no
      assumed value enters the GCP total.
- [ ] The verdict uses verified rows only; unverified is reported with rows and
      vCPU and never fails the run.
- [ ] Instance rows with unresolved specs count as unverified, not N/A.
- [ ] The log line no longer prints identical AWS and GCP totals.
- [ ] 5 new tests pass, including one proving the gate *can* fail; full suite
      passes at your recorded baseline + 5.
- [ ] **No dollar figure moved.**
