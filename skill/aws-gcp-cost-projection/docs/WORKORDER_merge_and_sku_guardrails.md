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

## ✅ Already landed and verified (2026-10-14/15) — do NOT redo
merge/resolver: M-1 atomic txn, M-2 validate_row, M-5 merge_report.json, A (unit_equivalence),
G-1b unit validation (RV-2 class-name match + RV-3 merge passes mechanic_group), G-1c single
resolver, B (catalog opened once), C-1b (atomic os.replace in build_catalog_index.py), C-1c
(fail-closed confidence cap).
apply_rates: E (Spot→OD, Preemptible synthesis removed), F (per-month tier blend, both the main
loop and the egress injection), AR-C (transaction wrap), AR-D (region-scoped triage), AR-E
(Aurora MySQL branch), AR-F (Multi-AZ via deployment_option), AR-G (egress per-month), AR-H
(ElastiCache sets Memorystore SKU name), AR-I (ORDER BY in normalization), AR-J (no-catalog
rate_source), **RV-1 (component filter on null-SKU resolve), AR-K (zero-usage → base tier),
AR-L (parameterized egress query), AR-M (no-SKUs early-return rate_source), AR-N (MIN(service)
dedup).**
ingest/report: RV-5 (billing_month populated in every ingest path + dedup keys), RV-4 (discounts
block counts only negative-cost rows, not positive SP/RI fees), RV-D1 (headline = GCP On-Demand
for all rows; CUD only in its own columns), RV-D2 (ACU ratio kept 0.25 vCPU / 2.0 RAM with AWS
citation), RV-D3 (currency: FX-direction inversion, ¥ JPY/CNY disambiguation, AWS side converted
via aws_fx_rate), **REGION-1 (unreachable check accepts region/global/dominant → priced at
dominant instead of manual review; triage UPDATE batched per AR-O), NAME-1 (gcp_service
normalized to catalog canonical BEFORE CUD/outlier/report), AR-O (triage batched), Minor (merge docstring reconciled & verified with test).**

Suite green at 922 tests.

---

# All work order items complete.
