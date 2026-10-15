# WORK ORDER — Harden `apply_commitment_ignores.py`

**For an implementing AI / engineer with NO prior context. Read fully. Two changes to one
file + its test. Both are small and independent; do both, run the test, report.**

## File
`skill/aws-gcp-cost-projection/scripts/apply_commitment_ignores.py`

## What this script does (context)
It runs in Phase 2 BEFORE the LLM. It reads rows that `classify_mechanics.py` already
labelled `mechanic_group='commitment_discount'` (RI/SP/EDP fees) or `'negative_cost'`
(credits/refunds/negations), and writes "ignore" mapping JSON files so those non-workload
rows contribute $0 to the GCP projection and never reach the LLM. It does NOT write to the
database — it only writes two JSON files under `projection-audit/mappings/`
(`commitment_discount_mappings.json`, `negative_cost_mappings.json`). Keep that discipline:
**read-only on the DB, JSON out only.**

---

## FIX 1 — NULL cost must not crash the note (robustness)

**Problem:** The `negative_cost` note is built with an f-string that formats the cost with
`:.2f`:
```python
f"negative_cost: credit/refund/negation (${r[1]:.2f}, {r[2] or 'unknown type'}) — no GCP equivalent"
```
`r[1]` is `aws_amortized_cost`. If a malformed / PDF-sourced bill yields a `negative_cost`
row whose `aws_amortized_cost` is NULL, `None:.2f` raises `TypeError` and the WHOLE script
dies — so NO ignore files are written, commitment rows go unmapped, and downstream coverage
breaks. Guard it.

**Fix:** coalesce the cost to `0.0` before formatting (in BOTH the SQL default and the
Python format, belt-and-suspenders). Use `(r[...] or 0.0)` at the format site.

**Test (add to the test added in FIX 2 or its own case):** a `negative_cost` row with a NULL
`aws_amortized_cost` must NOT raise; the note renders with `$0.00` and the row still gets
`strategy='ignore'`.

---

## FIX 2 — Carry `service`, `product_code`, and `amount` as STRUCTURED fields

**Why:** a decided future report feature — the "Discounts by service" block + net-AWS-total
at the bottom of the report (watch-list #3 / `WORKORDER_pipeline_hardening.md` H-3) — needs,
per ignored row: WHICH service the discount belongs to and HOW MUCH. Today the commitment
record carries neither (no service, no amount); the negative_cost record buries the amount
in free-text only. Adding them now makes these two JSON files the clean, auditable source for
that block instead of forcing a re-query. Adding fields is harmless to current consumers
(they ignore unknown keys).

**Fix — both queries and both record builders:**

1. **Commitment query** — change
   ```sql
   SELECT aws_li_key FROM aws_li_catalog WHERE mechanic_group = 'commitment_discount'
   ```
   to also select `product`, `product_code`, `aws_amortized_cost`:
   ```sql
   SELECT aws_li_key, product, product_code, aws_amortized_cost
   FROM aws_li_catalog WHERE mechanic_group = 'commitment_discount'
   ```

2. **Negative-cost query** — add `product`, `product_code` to the existing select
   (keep `aws_li_key, aws_amortized_cost, line_item_type`).

3. **Both record builders** — add three structured keys to each mapping dict, alongside the
   existing `aws_li_key / strategy / mapping_confidence / projection_note`:
   ```python
   "aws_service": <product>,            # may be None — pass through as-is, do not fabricate
   "aws_product_code": <product_code>,  # may be None
   "aws_amortized_cost": <cost or 0.0>, # numeric, NULL-coalesced to 0.0
   ```
   Keep the existing `projection_note` text unchanged (still human-readable).

**Guards:**
- If `product` / `product_code` columns do not exist in `aws_li_catalog` on some older schema,
  the script must still work. Check the columns via
  `DESCRIBE aws_li_catalog` (or `information_schema.columns`) once at the top; if a column is
  missing, select `NULL` in its place (same defensive pattern the global audit uses). Do NOT
  let a missing optional column crash the script.
- Do not change `strategy`, `mapping_confidence` (stays `1.0`), or the output file paths /
  names — downstream `merge_mappings.py` depends on them.

**Test:** a `commitment_discount` row and a `negative_cost` row each produce a mapping dict
that includes `aws_service`, `aws_product_code`, and a numeric `aws_amortized_cost`; strategy
is still `ignore`; files are still written to the same two paths.

---

## Verification (run from `skill/aws-gcp-cost-projection/`)
1. `SKILL_DIR="$(pwd)" python -m pytest tests/ -q` — all existing tests pass.
2. New/updated test for this file passes (NULL cost no-crash + new structured fields present).
3. Syntax-compile the file: `python -m py_compile scripts/apply_commitment_ignores.py`.

## Report back
Files changed, the test added, and confirm the two JSON files still land at
`projection-audit/mappings/commitment_discount_mappings.json` and
`.../negative_cost_mappings.json` with the new fields.
