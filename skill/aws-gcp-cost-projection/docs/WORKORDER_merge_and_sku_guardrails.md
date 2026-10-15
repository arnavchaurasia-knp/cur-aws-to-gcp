# WORK ORDER — merge / resolver / apply_rates — REMAINING OPEN ITEMS

**For an implementing AI / engineer with NO prior context. Read fully. Each item is
self-contained with a concrete test. Most of this work order is already implemented and
verified (see the "Already landed" list) — only the items below remain.**

**Context you need:**
- AWS→GCP cost-projection pipeline. `apply_rates.py` (Phase 4) attaches GCP rates and builds the
  projection — the "money" step. `resolve_sku.py` resolves LLM intent → catalog SKU (+ unit
  validation via `unit_equivalence.py`). `merge_mappings.py` funnels mapping files into
  `aws_li_to_gcp_li`.
- Pricing basis: **AWS On-Demand LIST vs GCP On-Demand LIST** (OD-vs-OD); CUD only in its own
  labeled columns.
- Test command (from `skill/aws-gcp-cost-projection/`):
  `SKILL_DIR="$(pwd)" python -m pytest tests/ -q`
- Python 3.14: inline regex flags like `(?i)` mid-pattern are a syntax error — pass
  `re.IGNORECASE`.

---

## ✅ Already landed and verified (2026-10-14) — do NOT redo
merge/resolver: M-1 atomic txn, M-2 validate_row, M-5 merge_report.json, A (unit_equivalence),
G-1b unit validation (RV-2 class-name match + RV-3 merge passes mechanic_group), G-1c single
resolver, B (catalog opened once), C-1b (atomic os.replace in build_catalog_index.py), C-1c
(fail-closed confidence cap).
apply_rates: E (Spot→OD, Preemptible synthesis removed), F (per-month tier blend, both the main
loop and the egress injection), AR-C (transaction wrap), AR-D (region-scoped triage), AR-E
(Aurora MySQL branch), AR-F (Multi-AZ via deployment_option), AR-G (egress per-month), AR-H
(ElastiCache sets Memorystore SKU name), AR-I (ORDER BY in normalization), AR-J (no-catalog
rate_source).
ingest/report: RV-5 (billing_month populated in every ingest path + dedup keys), RV-4 (discounts
block counts only negative-cost rows, not positive SP/RI fees), RV-D1 (headline = GCP On-Demand
for all rows; CUD only in its own columns), RV-D2 (ACU ratio kept 0.25 vCPU / 2.0 RAM with AWS
citation), RV-D3 (currency: FX-direction inversion, ¥ JPY/CNY disambiguation, AWS side converted
via aws_fx_rate).

---

# OPEN — priority first

## RV-1 — Null-SKU resolution must filter by `component` (🔴 money bug — do this first)
**Lives in:** `scripts/apply_rates.py` (~lines 459-490, the null-SKU resolve loop in `main()`).
**Problem:** the loop iterates per `(aws_li_key, component)` row, resolves a SKU for that
component, then UPDATEs `WHERE aws_li_key = ? AND gcp_sku_id IS NULL` with NO `component` filter.
For a multi-component break_down line (e.g. an ElastiCache/managed_db key nulled post-merge with
`core`+`ram`+`storage` components all NULL), the FIRST component processed writes its `sku_id`
onto EVERY still-NULL component of that key; later iterations find nothing NULL and no-op.
**Scenario:** an ElastiCache row remapped to Memorystore resolves the `core` SKU and stamps it
onto the `ram` and `storage` rows too → RAM and storage priced at the core SKU's rate. On the
money path.
**Fix:** add `AND component = ?` (passing the loop's current `component`, handling NULL component
with `IS NOT DISTINCT FROM` or an `IS NULL` branch) to BOTH UPDATE statements so each component
gets only its own resolved SKU — same per-component discipline merge already uses.
**Test:** an ElastiCache row with core+ram+storage components each resolves to its OWN SKU; no
component inherits another component's SKU/rate.

---

# OPEN — hardening (lower priority; do after RV-1)

## AR-K — `blended_rate` returns the CHEAPEST tier when usage is zero (🟡 silent under-price)
**Lives in:** `scripts/apply_rates.py`, `blended_rate()` (~line 102).
**Problem:** `return total_cost / total_qty if total_qty > 0 else tiered_rates[-1].get("rate", 0)`
— the `[-1]` tier is the highest-volume, CHEAPEST rate. Any SKU whose usage sums to 0 (join miss,
or a genuinely 0-usage SKU) gets the cheapest tier applied to EVERY row of that SKU → silent
under-pricing.
**Fix:** on zero/absent usage return the FIRST tier (`tiered_rates[0]`, base/most-expensive) or 0
— never the cheapest tier.
**Test:** a SKU with total_qty=0 resolves to the base-tier rate, not the top-volume tier rate.

## AR-L — Parameterize the egress fallback usage query (🟡 crash risk)
**Lives in:** `scripts/apply_rates.py` (~lines 768-773).
**Problem:** `WHERE m.gcp_sku_id = '{_sku_id}' OR m.gcp_sku_name = '{_sku_name}'` is f-string
built. `_sku_name` is internal today, but an apostrophe in any egress name would throw
mid-transaction → the whole rate fill rolls back.
**Fix:** use `?` placeholders like every other query in the file.
**Test:** the egress fallback usage query runs with a name containing an apostrophe without error.

## AR-M — "No SKUs to fill" early return skips `rate_source` stamping (🟡)
**Lives in:** `scripts/apply_rates.py` (~lines 520-523).
**Problem:** when a bill maps to zero priceable SKUs (all passthrough/ignore), the function
creates the view and RETURNS before the rate_source block, leaving `rate_source` NULL on every
row (report may mislabel). The no-catalog branch already stamps rate_source; this branch does not.
**Fix:** stamp `rate_source` ('passthrough'/'ignored'/'unknown' as appropriate) before this early
return, same as the no-catalog branch.
**Test:** an all-passthrough bill comes out with non-NULL `rate_source` on every row.

## AR-N — `skus_used` can bind a SKU to an arbitrary service (🟡)
**Lives in:** `scripts/apply_rates.py` (~lines 496-557).
**Problem:** `SELECT DISTINCT gcp_sku_id, gcp_service` → if one `sku_id` carries two
`gcp_service` labels (LLM inconsistency), `sku_to_service` keeps whichever row comes last and the
rate is inserted under that arbitrary service. Later normalization (~941) repairs the mapping
rows, but the inserted rate row's service is nondeterministic.
**Fix:** deduplicate `sku_id → service` deterministically (catalog's own `service_name` for that
sku_id, or `MIN(gcp_service)`).
**Test:** a sku_id presented with two service labels inserts its rate under a single
deterministic service on repeated runs.

## AR-O — Reduce redundant work in main() (🟡 perf, low risk)
**Lives in:** `scripts/apply_rates.py`.
**Problem:** `create_projection_view` recreated up to 5× (522, 550, 863, 888, 1042); the
unreachable-triage downgrade runs one UPDATE per unreachable pair in a Python loop; the
gcp_service normalization runs the same correlated subquery three times.
**Fix (optional, only if cheap):** compute `dominant_region` once and build the view the minimum
number of times; batch the triage UPDATE; compute the normalization target once into a temp/CTE.
Behavior must stay identical.
**Test:** outputs unchanged; view-creation / query counts reduced.

## Minor — reconcile merge docstring with missing-group behavior (LOW)
`merge_mappings.py` warns-and-continues on missing group files; make any stale docstring describe
the real behavior (missing groups recorded in `merge_report.json`, backstopped by
`ensure_full_coverage.py`).

---

## Report back
Files changed, the test added, and anything that needed a decision you couldn't make. Keep both
mapping/classifier suites green; no row's dollar value may change except AR-K (zero-usage rows,
intended).
