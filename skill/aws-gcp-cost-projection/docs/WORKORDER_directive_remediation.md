# WORK ORDER — Remediation: close the gaps left in Directives 1 & 2

**For an implementing AI / engineer with NO prior context. Read fully. A prior work order
(`WORKORDER_headline_currency_passthrough_nat.md`) implemented four directives; a verification
review found Directives 3 & 4 DONE but Directives 1 & 2 each still carry ONE high-severity gap
plus smaller items. This order closes them. The two HIGH items survived because the existing
tests don't exercise them — so every fix here REQUIRES the specific failing test described,
written FIRST (it must fail before the fix, pass after).**

## System context
AWS Cost & Usage Report → GCP cost projection. Per-job DuckDB. Row `strategy` values:
`map`/`break_down` (real GCP SKU+rate), `passthrough` (carry AWS cost 1:1), `ignore` ($0 —
commitment/discount/non-workload/free-tier), `outlier_triage` (flagged). The report renders in
three sections: A = mapped (headline savings), B = passthrough (AWS==GCP, cost-neutral),
C = discounts. Two deliverables: HTML (`render_report.py`) and XLSX (`export_report.py`) — they
MUST agree.

## Test command (from `skill/aws-gcp-cost-projection/`)
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
All 913 existing tests must stay green. Each fix below names the NEW test to add.

---

## R-1 — HIGH — `ignore` rows break the report footing invariant (Directive 2)

**Problem:** In the Section B accumulation loop (`render_report.py` ~L1311-1317), every row adds
`aws` to the GCP subtotals: `sec_b_gcp_od += aws` (and 1yr/3yr). But `ignore` rows land in
Section B (Section A only admits `map`/`break_down`), and `_render_row` displays an `ignore`
row's GCP cell as **$0.00** (the `if is_ignore:` branch precedes `elif is_pt:`). So an ignore
row shows $0 in its cell but adds its full AWS cost to the GCP grand total.

**Failure scenario:** a bill with a $5,000 ignore row (e.g. RDS Extended-Support, CloudWatch
free-tier, CPU credits) → the row's GCP cell shows $0.00, but the Section B subtotal and GRAND
TOTAL add $5,000 to GCP OD. Grand total ≠ sum of shown GCP cells (violates the FOOTING
INVARIANT), and reported savings are understated by the full ignore spend. Same on 1yr/3yr.

**Fix:** in the Section B loop, an `ignore` row must contribute **0** to the GCP subtotals while
still contributing its `aws` to the AWS subtotal (so Section B's AWS column still shows the
spend, and the GCP column matches the $0 cells). Mirror the authoritative semantics already in
`export_report.py` (`WHEN m.strategy = 'ignore' THEN 0.0` for gcp_od_cost). Concretely, branch on
the row's strategy:
```python
sec_b_aws += aws
if strategy == 'ignore':
    pass  # GCP OD/1yr/3yr contribution is 0 — matches the $0 cell _render_row shows
else:    # passthrough / reclassified-unpriced
    sec_b_gcp_od  += aws
    sec_b_gcp_1yr += aws
    sec_b_gcp_3yr += aws
```
(Use the row's actual strategy field — confirm its tuple index; the review cited `r[3]`.)

**Test (write first, must fail before fix):** extend/clone the footing test
(`test_directive_2_render_report_section_split_and_strict_footing`) to include an `ignore` row
with cost > 0, and assert **numerically** that the rendered GRAND TOTAL GCP OD equals the sum of
the per-row GCP OD cells (not just string-presence of a subtotal). This is the guard that was
missing.

---

## R-2 — MED — HTML vs XLSX disagree on ignore rows (Directive 2)

**Status:** this is the same root cause as R-1. `export_report.py` already treats ignore as $0
GCP; `render_report.py` does not. **Fixing R-1 makes HTML match XLSX automatically.** After R-1,
add an assertion (in `test_report_discounts.py` or the footing test) that the HTML grand-total
GCP figure equals the XLSX grand-total GCP figure for a bill containing an ignore row. No
separate code change expected — this is the regression guard that proves the two deliverables
agree.

---

## R-3 — HIGH — Mixed USD + single-foreign-currency bill corrupts USD rows (Directive 1)

**Problem:** the ingest FX normalization divides EVERY row by the one bill-level foreign rate
with no per-row currency filter (`ingest.py` ~L1731: `UPDATE aws_li_catalog SET
aws_amortized_cost = aws_amortized_cost / fx_rate` — no WHERE on currency), and
`currency_utils.detect_currency_and_rate` (~L118-120) picks `non_usd_col_codes[0]` as THE bill
currency whenever any foreign code appears. The mixed-guard in `ingest._detect_foreign_currency`
only trips on `len(codes) > 1` *foreign* codes, so a USD+EUR bill passes as "EUR".

**Failure scenario:** a CUR with USD and EUR line-items → every USD row is divided by the EUR
rate (~/0.92 → ~+8.7% inflation) and relabeled EUR, so AWS totals and every ratio/outlier/
passthrough gate run on corrupted values. The directive's "USD rows stay USD" is unmet. (There is
even an internal contradiction: detect stamps `aws_cost_currency='USD'` yet ingest converts the
rows as EUR.)

**Fix — convert per the row's OWN currency, not one bill-level code:**
1. If the CUR carries a per-line currency column, convert each row by ITS currency's rate
   (USD rows → rate 1.0, untouched). Stamp each row's `currency` to its own code.
2. If there is no per-line currency column but the bill mixes USD and one foreign symbol in the
   amount text, treat rows the detector identified as USD as USD (rate 1.0) and only convert the
   foreign-denominated rows.
3. Only when the bill is genuinely single-currency-foreign (no USD rows) do the blanket
   bill-level conversion that exists today.
Keep the native amount in `aws_amortized_cost_native` and the final `aws_amortized_cost` in USD,
as now.

**Test (write first, must fail before fix):** a synthetic bill with two rows — one USD $100, one
EUR €100 at rate 0.92 — ingested → assert the USD row's `aws_amortized_cost` stays 100.00 (NOT
108.7) and its `currency` stays USD, while the EUR row becomes ~108.70 USD. This case is
currently untested.

---

## R-4 — MED — Fail-loud on a missing FX rate was weakened to a silent stale fallback (Directive 1)

**Problem:** `currency_utils.detect_currency_and_rate` (~L212-221) only raises when the currency
is in NEITHER the live fetch NOR `data/fx_rates.json`. Since `fx_rates.json` ships 18 currencies,
a non-USD bill with no stated/live rate silently projects at a baked-in constant (e.g. INR 83.5)
with no error. The old `_fetch_fx_rate` failed loud. The literal directive ("never assume 1.0")
holds, but the intent ("don't silently mis-project at a stale rate") is weakened.

**DECISION NEEDED FROM PRODUCT OWNER — pick one, then implement:**
- **(a) Fail loud** — if no live/stated rate, raise and surface "could not obtain a current FX
  rate for <CUR>" (strongest honesty; matches the original directive).
- **(b) Proceed but surface prominently** — use the stale fallback BUT (i) stamp the rate's age/
  source, and (ii) render a LOUD banner in the report + a `data_gaps.json` entry ("GCP figures
  converted at an approximate stored rate dated <date>, not a live rate"). Never silent.

Default recommendation if no answer: **(b)** — a visible-but-approximate number beats a failed
report, as long as it is clearly labeled, which fits this pipeline's "loud, not silent" rule.

**Test:** a non-USD bill with no stated rate and the live fetch stubbed to fail → (a) raises, OR
(b) completes with the stale-rate notice present in the report/data_gaps and the rate's
date/source recorded. Assert whichever branch is chosen.

---

## R-5 — LOW — AI/markdown summary uses a third GCP basis (Directive 2 polish)

**Problem:** the markdown summary (`render_report.py` ~L1787) computes `od_pct` from the old
`gcp_od` variable (`SUM(gcp_projected_cost) WHERE is_workload`) against `aws_gross` — a third
number that matches neither the Section-A card nor the grand total. Narrative only, not table
math, but it should be consistent.

**Fix:** compute the summary's savings % from the Section-A figures (`sec_a_aws` vs
`sec_a_gcp_od`) so the prose matches the headline card. **Test:** assert the summary % equals the
Section-A card %.

---

## R-6 — LOW — NAT dead code + Shield NULL-month edge (pricer cleanups)

- **Delete the dead fleet-VM block** (`apply_static_mappings.py` ~L2401-2427): `vms_per_gw` /
  `n_gateways` / `n_vms` (incl. a per-run read-only duckdb query on `sys.argv[1]`) are computed
  but never consumed after the Directive-4 rewrite. Remove them so it's clear fleet logic no
  longer runs. No behavior change; existing NAT tests must stay green.
- **Shield NULL-`billing_month` guard** (`map_shield` ~L4331/4371): when `billing_month` is NULL
  (e.g. a PDF bill with no detectable date), the per-month dedup key collapses to `"default"`,
  regressing a multi-month bill to a single $3,000. Guard: if `billing_month` is NULL, do NOT
  dedupe (price each subscription row) rather than collapsing them. **Test:** two Shield
  subscription rows with NULL billing_month → both priced ($6,000), not deduped to $3,000.

---

## Explicitly WORKING AS DESIGNED (do NOT "fix")
- **GCP On-Demand card ≠ grand total** is intended: the card is the *mapped* comparison
  (Section A), the grand total is all-in (A+B). Do not make the card include Section B. If
  anything, LABEL them ("Mapped workloads" vs "All-in total") so the difference is legible.
- **Net-AWS discount total** already uses the authoritative `aws_discounts` (= aws_gross −
  aws_grand), so the sub-cent `HAVING > 0.005` per-product floor does NOT affect the net box —
  no change needed there.
- Directive 3 (region fallback) and Directive 4 (NAT cap) are DONE — do not revisit.

## Report back (per item)
Files changed, the NEW test added (and confirmation it failed before the fix), pytest result
(all green), and the R-4 decision if the product owner answered it. Flag anything where the
row-strategy tuple index or column name differed from what this order assumed.
