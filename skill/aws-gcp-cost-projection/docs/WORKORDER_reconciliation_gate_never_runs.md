# WORK ORDER — The reconciliation gate has never run on a single bill

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `extract_bill_total()` in `scripts/validate_fix.py`, and how its
failure is reported.** No pricing, mapping or ingest changes. **No dollar
figure may move.** This work order makes an existing check capable of running.

---

## 1. The problem in plain English

`validate_fix.py` has a reconciliation gate: it reads the **bill's own stated
grand total**, compares it to the sum of `aws_li_catalog`, and fails the run if
they differ by more than `max($1, 0.5%)`. It is the check that catches ingest
losing or duplicating money.

**It has never run.** Measured across the corpus:

```
bills with a catalog: 12
reconciliation skipped: 12
```

Every run prints:

```
[WARN] bill_total not found in input — reconciliation skipped
```

### Two independent reasons it cannot succeed

**1. It only opens two filenames.**

```python
for name in ("input.txt", "input.csv"):
```

The corpus input formats are **6 csv, 4 pdf, 5 xlsx**. Nine of fifteen files
are never opened at all — including every PDF, which is the format whose
wording the regexes were actually written for.

**2. All three patterns require the literal token `USD`.**

```python
BILL_TOTAL_RES = [
    r"grand\s+total[:\s]*USD\s*([0-9][0-9,]*\.[0-9]{2})",
    r"total\s+pre-?tax\s*USD\s*([0-9][0-9,]*\.[0-9]{2})",
    r"^\s*total[^0-9]*USD\s*([0-9][0-9,]*\.[0-9]{2})",
]
```

That is AWS **PDF invoice prose**. In a CSV the total is a *row*, not a
sentence. `job14.csv` carries its total as:

```
Total ($),Global,Total ($),,0.00,19358.76
```

No `USD` token, so no match — and that bill's total is exactly the $19,358.76
the catalog should be checked against.

### Why this matters more than it looks

`ingest.py` has its own faithfulness check, and it passes:

> `Faithfulness OK: 82 catalog rows, total $19,358.76 reconciles to extracted bill total $19,358.76`

But that compares the catalog against **ingest's own extraction** — it is
self-referential. If extraction drops a section of the bill, both sides of that
comparison drop together and it still says OK.

The validator's reconciliation is the **only independent check** that the
catalog matches what the bill says it should be. It is the backstop for the
entire ingest layer, and it has never executed.

---

## 2. Tasks

### T1 — Open the actual input file, whatever it is called

Replace the two hardcoded names with a glob of `input.*` in the job directory,
handling each by extension. Keep it tolerant: an unreadable file is a
"not found", never an exception.

### T2 — Extract a total from tabular formats, not just prose

For `.csv` (and `.xlsx`, which `ingest.py` can already read), the total is a
**row with a label cell and a numeric cell**, not a sentence. Find a row whose
first non-empty cell matches `^\s*total\b` (case-insensitive, allowing
`Total ($)`, `Total (USD)`, `Grand Total`) and take the largest numeric cell in
that row.

Worked example that must pass — `job14.csv`:

```
Total ($),Global,Total ($),,0.00,19358.76   ->   19358.76
```

**Guard against the obvious false positive:** a row labelled `Total` inside a
per-service subtotal block is not the bill total. Prefer the **last** matching
row in the file, and if two candidate rows disagree by more than the gate's own
tolerance, treat it as not-found rather than guessing — a wrong total would
fail every bill.

### T3 — PDFs: reuse ingest's text, do not reimplement extraction

`ingest.py` already extracts PDF text (`_load_pdf`). Reuse that path rather
than adding a second PDF reader that can drift from it. If the extracted text
is not available at validation time, that is a legitimate not-found — say so
(T4) rather than silently skipping.

The existing `USD` regexes are correct **for this format** and should be kept
for it.

### T4 — A skipped safety check must not read as normal

Today the skip is a bare `print("[WARN] ...")` plus a report note saying
"Verify GCP total manually". That is indistinguishable from routine output,
which is why this went unnoticed across every bill.

Emit it through the existing diagnostic channel (`validate_fix.py` already
imports `diag`):

```python
diag(WARN, "reconciliation_skipped", "validate_fix.py",
     f"Bill total could not be read from {input_name!r} — the catalog-vs-bill "
     f"reconciliation gate did not run for this job.")
```

State plainly that **a gate did not run**. "Could not extract a value" and "a
safety check was skipped" are different sentences, and the operator needs the
second one.

### T5 — Tests

1. `test_bill_total_from_csv_total_row` — a CSV with a `Total ($)` row returns
   that number. Use the `job14.csv` shape above.
2. `test_bill_total_from_pdf_prose` — the existing `USD` patterns still work;
   this is a regression guard on T3.
3. `test_bill_total_opens_any_input_extension` — an `input.xlsx` is opened, not
   skipped because of its name.
4. `test_conflicting_totals_return_none` — two `Total` rows disagreeing beyond
   tolerance yield not-found, not a guess (T2).
5. `test_skip_emits_diagnostic` — when the total cannot be read, a
   `reconciliation_skipped` diagnostic is emitted.
6. `test_reconciliation_fails_on_mismatch` — with a total that differs beyond
   `max($1, 0.5%)`, the gate produces a violation. **This is the one that
   proves the gate works end to end**, and nothing has ever exercised it.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/validate_fix.py
```

- Record the test count first; confirm it rises by exactly 6.
- **No dollar figure changes anywhere.** This work order only enables a check.
- Run the validator across the corpus: reconciliation should now run on the CSV
  and PDF bills instead of 12 of 12 skipping. Report how many run and how many
  still legitimately cannot.
- **Expect the gate to find nothing on these bills.** Ingest reconciles
  correctly today — I verified `job14` independently against its source file.
  A sudden crop of reconciliation violations means the extractor is reading the
  wrong number, not that ingest is broken. Check the extracted value against
  the file by eye before believing it.

---

## 4. Do NOT

- Do **not** fall back to ingest's own computed total when extraction fails.
  That makes the check self-referential and worthless — it is the entire point
  that this number comes from the file independently (§1).
- Do **not** loosen the tolerance to make bills pass. If a real mismatch
  appears, that is the gate doing its job.
- Do **not** add a second PDF text extractor (T3).
- Do **not** guess between conflicting candidate totals (T2).
- Do **not** leave the skip as a bare `print`. A safety check that did not run
  must say so distinctly (T4).

---

## 5. Why this one is worth doing properly

Three defects found in this codebase have the same shape: a check that cannot
see the case it exists for. The unreachable-rate check filtered
`gcp_sku_id IS NOT NULL` and so missed rows with no SKU. The commitment guard
read a column its own query never fetched. This gate reads a format its bills
are not written in.

In each case the code looked correct, the tests passed, and the log said
everything was fine. This one is the most consequential of the three, because
it is the last line of defence for the ingest layer — and it is also the
easiest to verify once it runs: give it a bill whose total you have checked by
hand (T5.6).

---

## 6. Done

- [ ] `extract_bill_total` opens `input.*`, not two hardcoded names.
- [ ] Tabular totals (CSV/XLSX) are extracted; PDF prose still works via
      ingest's existing text path.
- [ ] Conflicting candidates yield not-found rather than a guess.
- [ ] A skipped gate emits a `reconciliation_skipped` diagnostic saying a check
      did not run.
- [ ] 6 new tests pass, including one that makes the gate actually fail on a
      mismatch; full suite passes at your recorded baseline + 6.
- [ ] Reconciliation runs on the corpus bills; **no dollar figure moved.**
