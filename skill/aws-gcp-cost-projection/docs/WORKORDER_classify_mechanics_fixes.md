# WORK ORDER — classify_mechanics.py improvements (incremental)

**For an implementing AI / engineer. Read the whole file first. This file grows:
items move BACKLOG → READY as we decide them. Only implement "## READY".**

## Context
- File under work: `skill/aws-gcp-cost-projection/scripts/classify_mechanics.py`
  (~1531 lines). Stamps each `aws_li_catalog` row with `mechanic_group` via an ordered
  first-match rule list `RULES`; `main()` bulk-updates and reports.
- Python **3.14**: no inline regex flags mid-pattern (`(?i)`); pass `re.IGNORECASE`.
- Tests from `skill/aws-gcp-cost-projection/`:
  `SKILL_DIR="$(pwd)" python -m pytest tests/test_classify_mechanics.py tests/test_spec_dispatch.py -q`

## Two kinds of fix (each READY item says which it is)
- **Output-preserving:** must NOT change any row's `mechanic_group`; both suites pass
  unchanged.
- **Correctness (routing-changing):** intentionally fixes routing for broken cases. You
  MUST update the affected cases in `tests/test_spec_dispatch.py` to the new correct
  expectation and note it for shadow-diff. Never change routing silently — every changed
  row must be one the item names.

---

## DONE
- **FIX 1–4 (2026-10-11, output-preserving).** Docstring corrected; rule exceptions
  logged not swallowed; output `mechanic_group` validated against known set; regexes
  lru-cached. Nothing in RULES changed. 191 tests pass.

---

## READY — A) output-preserving (no routing change)

### FIX 5 — Per-row rule trace (`mechanic_rule` column), backward-compatible
Record WHICH rule assigned each row's group without breaking `classify(row) -> str`.
1. Add `classify_with_trace(row) -> tuple[str,str]` that owns the loop and returns
   `(group, f"{group}#{idx}")`; its `except` is also FIX 2's logging site.
2. Reduce `classify()` to `return classify_with_trace(row)[0]` (signature/behavior
   unchanged).
3. In `main()`: idempotent `ALTER TABLE aws_li_catalog ADD COLUMN mechanic_rule TEXT`
   (only if missing), collect `(group, rule_label)`, bulk-UPDATE both columns.
Output-preserving: `mechanic_group` byte-identical; only the new column is added.

### FIX 6 — Remove the `_BILL_HAS_DISCOUNTED_USAGE` module global
1. In `main()` compute the bill-wide bool into a local `bill_has_discounted`.
2. Set `row["_bill_has_discounted_usage"] = bill_has_discounted` on each row dict.
3. In the `commitment_discount` lambda, use `r.get("_bill_has_discounted_usage", True)`.
4. Delete the module-level global + the `global` statement.
Output-identical; verify a bill WITH and WITHOUT DiscountedUsage both classify as before.

### FIX 7 — Ordering-invariant / property tests (new file, no code change)
Create `tests/test_classify_invariants.py` (pytest + script runnable). Assert against
`classify_mechanics.classify`:
1. `(BoxUsage|HeavyUsage):t[234]g?\.` + hourly → `compute_burstable` (never compute_breakdown).
2. Graviton `(BoxUsage|HeavyUsage):<family-with-g>\.` → `compute_arm`.
3. RDS non-hourly GB-Mo/storage row → `block_storage` (never managed_db).
4. `aws_amortized_cost < 0` → `negative_cost` regardless of product.
5. Every classified group ∈ `{g for g,_ in RULES} | {"misc"}`.
Pure additions; classify_mechanics.py not modified.

### Acceptance for FIX 5–7
- Both suites still pass (`classify` output + signature unchanged).
- New invariants test passes. `git diff` of classify_mechanics.py touches only
  `classify_with_trace`/reduced `classify`/`main()` loop+ALTER/`commitment_discount`
  lambda + removed global. RULES ordering/logic unchanged.

---

## READY — B) correctness fixes (routing-changing; update tests + shadow-diff)

### FIX 8 — Tighten `_has_instance_type` to stop version-token false positives
`_has_instance_type` matches version-ish tokens today: `"v2.0"`, `"h264.profile"` →
True, widening `compute_breakdown`'s first clause. Require a real EC2 size suffix:
```python
re.search(r'\b(?:db\.|cache\.|kafka\.)?[a-z]+\d[a-z0-9-]*\.(nano|micro|small|medium|large|metal|\d*xlarge)\b', text, re.IGNORECASE)
```
Verify: `"m5.xlarge"`,`"db.r5.large"`,`"c6gd.4xlarge"`,`"t3.medium"` → True;
`"v2.0"`,`"h264.profile"`,`"tier1.foo"` → False. Run both suites; update any row that
legitimately changes (expect none in current tests — they use real instance types).

### FIX 9 — Normalize whitespace before matching (PDF nbsp / double spaces)
PDF extraction injects non-breaking spaces (`\xa0`) and double spaces, breaking
multi-word matches like `"Elastic Compute Cloud"`. Add:
```python
def _norm(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\xa0', ' ')).strip()
```
Match against `_norm(value)` inside `_ilike` and `_re` (NOT the patterns). This can
newly-match EC2 rows that previously fell to misc on PDF bills — intended; update tests.

### FIX 10 — Exclude SageMaker from compute_breakdown (DECIDED: route to Vertex AI)
SageMaker ML instances (`ml.*`, incl `ml.p4d`/`ml.trn1` accelerators) are NOT in the
`compute_breakdown` exclusion list, so an instance-hour-phrased SageMaker row would be
CPU-sized (same class as the Inferentia bug). Add to the existing `and not (...)` block
that already lists RDS/Aurora/EMR:
```python
or _ilike(r["product"], "SageMaker")
or r.get("canonical") == "sagemaker"
```
Effect: SageMaker → `misc` → the existing `sagemaker` spec routes it to Vertex AI
(review); ML-accelerator rows follow the same honest path as Inferentia/Trainium. Add a
test: a SageMaker `ml.p4d` instance-hour row asserts `misc` (not `compute_breakdown`).

### Acceptance for FIX 8–10
- Both suites pass after you update intentionally-changed rows.
- `git diff` touches only `_has_instance_type`, `_ilike`/`_re`+`_norm`, and the
  compute_breakdown exclusion block. Nothing else in RULES.
- Report each row whose `mechanic_group` changed; confirm each is an intended case.

---

## DECIDED → own work orders
- **NEW-3 (is_workload unification)** — DECIDED. Full spec:
  `docs/WORKORDER_is_workload_unification.md` (ingest-side: one canonical non-workload
  definition in `data/workload_classification.json`, all 4 parsers use it; then classify
  trusts the flag as a follow-on step).
- **NEW-4 (split greedy service rules)** — DECIDED. Full spec:
  `docs/WORKORDER_greedy_service_split.md` (registry-side: split `*_all` specs so egress→
  data_transfer, storage→block_storage; confirmed live for OpenSearch egress → LLM
  misprice; depends on shadow-diff to scope all affected services).

## BACKLOG — need a decision (being thought through)

### NEW-3 — (DECIDED — see own work order above; kept for context)
Make `is_workload` authoritative AND logically sound for all formats
Decision taken: classify should TRUST ingest's `is_workload` — BUT only after the
`is_workload` basis is unified and correct across formats. Today ingest derives it FOUR
divergent ways:
  - raw CUR (~966): `line_item_type IN (Usage,DiscountedUsage,SavingsPlanCoveredUsage) AND NOT marketplace` — GOLD STANDARD (AWS LineItemType enum).
  - spreadsheet (~1163): Category ILIKE support/marketplace/tax/SavingsPlans/Registrar → FALSE. **Misses Credit/Refund/RIFee/EDP.**
  - flat CSV (~1660): service/description ILIKE marketplace/support → FALSE. **Misses tax, credits, savings-plans, RIFee entirely.**
  - PDF (~1575): long keyword list — most complete.
Concrete bug: a **Credit** row is non-workload in raw-CUR/PDF but **workload=TRUE in a
flat-CSV bill** → counted as real spend.
DESIGN TO FINALIZE: one canonical "non-workload" definition = AWS LineItemType semantics
— workload is ONLY actual consumption (Usage / DiscountedUsage / SavingsPlanCoveredUsage);
everything else is non-workload (Tax, *Fee, RIFee, SP Upfront/Recurring/Negation, Credit,
Refund, EdpDiscount, Private/BundledDiscount, Marketplace, Support). Centralize this set
in ONE place (helper or data file); every parser maps its available fields onto the SAME
predicate (formats without LineItemType reconstruct it from category/description/product).
THEN classify trusts the flag (route `is_workload=False` → non_workload/ignore).
OPEN: exact canonical set + centralization location. (Separate work order to be written.)

### NEW-4 — Greedy service rules vs shared mechanics (egress/storage sub-rows)
Service rules that match ALL of a product's rows (`cloudwatch`, `opensearch`, `redshift`,
the spec `*_all` charge_types) can steal that service's egress/storage rows before the
shared `data_transfer`/`block_storage` rules. Decision: service owns all its rows, or
yields egress→data_transfer and storage→block_storage? Leaning: yield (split `*_all`),
but confirm with shadow-diff which services actually have stolen sub-rows first.

### NEW-7 — `negative_cost` rule: net-negative vs actually-a-credit
`negative_cost` (rule #1) ignores ANY row with `cost < 0`. A dedup group that nets
negative (real usage minus a larger grouped discount) is zeroed out. Decision: keep broad,
or tighten to "negative AND credit-type line_item_type"? Leaning: tighten.

### NEW-8 — `$0` / free-tier rows policy
A `$0` AWS row flows to normal rules → CPU-priced → GCP>$0 vs AWS $0 → phantom-zero
outlier noise. Decision: price normally, skip, or passthrough at $0 with a note? Leaning:
passthrough at $0 with a note. Depends on how free-tier should show in the report.

### misc > 15% — gate vs reported metric
Leaning: reported METRIC over WORKLOAD spend (classification_health.json + report banner),
non-blocking; optional very-high (~50%) safety fail behind review-config. Not a hard gate.

---

## PARKED (subsumed by the spec migration / handled elsewhere)
- **Full canonical-only identity** (remove `_ilike` re-sniffing) + **name-substring
  collisions (NEW-9)** — subsumed by the spec registry (keys on canonical); do at cutover.
- **Normalize PDF/flat field-location fallbacks once** — subsumed (`any_field_re` already
  does this declaratively in specs).
- **Generic field-resolution + provenance** — see SERVICE_SPECS_DESIGN.md "PARKED".
- **External bug-scar registry** — accrues in spec `source_rule`/`notes` during migration.
- **Untrusted-input regex hardening (NEW-10)** — note only; no ReDoS found.
- **Inferentia/Trainium → TPU** — NOT a classify_mechanics bug (family_mapper); own
  work order / session.
