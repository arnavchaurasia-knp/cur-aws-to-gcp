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

## DONE (collapsed — all verified in repo, 775 tests passing)
- **FIX 1–16 + ADD-3.** docstring/logging/output-validation/regex-cache; `mechanic_rule`
  trace; `_BILL_HAS_DISCOUNTED_USAGE` global removed; invariants test;
  `_has_instance_type` tightened; `_norm`; SageMaker + Neptune/QLDB/Timestream excluded
  from compute_breakdown; canonical fallback arity; `_default` contract; no-DiscountedUsage
  test; drop pandas `fetchdf`; single-source `DETERMINISTIC_GROUPS`; `_is_accelerator`
  includes usage_type.
- **Sibling work orders landed:** Inferentia/Trainium → Cloud TPU; misc guidance →
  `llm_guidance.build_guidance` wired into Phase 2 (`_misc_reason` superseded).
- **FIX 17–20 (2026-10-13) — verified in repo, 780 tests pass.**
  - FIX 17: `data/misc_service_context.json` (curated context for recognized-but-unspecced
    misc services); `llm_guidance.build_guidance` consults it before the generic fallback.
  - FIX 18: misc rows carry `field_state` (via `annotate_misc_field_state`) and skip
    `_drop_null_cols`; other groups still trimmed.
  - FIX 19: manifest SELECT builds columns dynamically with `NULL AS` guards (no schema-
    variance crash).
  - FIX 20: `check_manifest_integrity()` flags any group excluded from the LLM with no
    deterministic handler (no silent drops).

---

- **FIX 17–20 (2026-10-13, misc context + manifest robustness).**
  - FIX 17: Curated context file `data/misc_service_context.json` created and wired into `llm_guidance.build_guidance`.
  - FIX 18: Full-column context preserved for `misc` rows with per-row `field_state` annotations attached; non-misc groups continue token-saving null-column drops.
  - FIX 19: Manifest `SELECT` column list dynamically generated from `existing_cols` with `NULL AS <col>` fallbacks, preventing crashes on schema variances.
  - FIX 20: Manifest partitioning integrity check `check_manifest_integrity` enforced, preventing silent drops of unhandled groups.
  All 780 tests passing.

---

## READY — (none currently in READY; see BACKLOG for decisions)

---

## REVIEWED — audit items deliberately NOT actioned (do not re-implement)
- **Audit #13 (add `cloudwatch` to `spec_dispatch._EXCLUSION_ORDER`) — REJECTED.**
  `cloudwatch` is a SERVICE (it has a `service_specs.json` entry), handled by `dispatch`,
  not a global service-agnostic pre-filter. Adding it would double-handle it. The real
  concern it gestures at (opensearch cross-cutting capture) is NEW-4.
- **Audit #6 (KMS) / #7 (Secrets Manager) static mappers — PREVIOUSLY DECIDED, not now.**
  `service_map` already forces Cloud KMS / Secret Manager passthrough at near-parity, so
  passthrough ≈ correct dollars; a SKU mapper only adds failure modes (see AGENTS.md
  IMPLEMENTATION STATUS, "rejected on skeptical review"). Re-open only with new evidence.
- **Audit #1 (Fargate operation-field gap) — LOW, impact overstated.** A plain-ECS Fargate
  row with the signal only in `operation` does NOT land in flat_hourly (flat_hourly has no
  ECS pattern) — it falls to misc (honest LLM), not an ALB/NAT mis-price. Optional cheap
  hardening: add `or _re(r.get("operation",""), r"[Ff]argate")` to the per_request Fargate
  catch. Not P0.
- **Audit #8 (compute_arm PDF fallback) — LOW.** Real (no operation fallback) but
  `family_mapper` already prices ARM correctly via `instance_arch`; only the mechanic
  LABEL is off. Add the `fallback_match` if convenient; not a pricing bug.
- **Audit #3 (OpenSearch charge-type split) = NEW-4** (already logged below).
- **Audit #12 / #23 (`_misc_reason` dead code / dedup) = DONE** — superseded by
  `llm_guidance.build_guidance`, now wired into Phase 2.
- **Audit #11/#14/#16/#18/#20/#21 — cosmetic/low**, not scheduled (dup rule names mitigated
  by the new `mechanic_rule` trace; `len(ut)>10` heuristic; emr product_codes work via
  canonical; schema-version migration premature).

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

### NEW-11 — Identity self-heal for brand-new product codes (the real new-service blocker)
FIX 17 (misc context file) only helps services we can IDENTIFY. A brand-new service with a
product code not in `data/aws-product-codes.json` resolves to `canonical=None` → it can't
be keyed into the context file or any spec → generic-guess forever. DECISION NEEDED: wire
an LLM "product-code backfill" (the agy-backfill from memory) to run BEFORE classify — when
a row's product_code/name is unknown, propose its canonical identity (+ GCP target), write
it to `aws-product-codes.json` / the registry for review, so the NEXT bill treats it as a
known fact instead of a guess. OPEN: does the backfill run today before classify at all?
(verify) and the write-then-review flow. This is the one non-low-decision piece of the
new-service problem; FIX 17/18 are the low-decision context improvements.

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

---

## FUTURE — capstone layers (user will build after the queued work; design-only here)
Two layers that make the pipeline self-healing + self-checking. Not scheduled yet; do NOT
start without the user. Recorded so the direction isn't lost.

### Refresh file / process — persist learned facts + keep prices fresh
- When the LLM identifies a brand-new/unknown service by name (Case-C, `canonical=None`
  today — see NEW-11), WRITE the learned identity (+ GCP target) into
  `data/aws-product-codes.json` / the registry, so the NEXT bill treats it as a known
  deterministic fact instead of re-guessing. This is the self-heal that closes NEW-11.
- Also refresh prices: product codes, GCP rates, pinned rates (`TPU-V5E-ONDEMAND`, egress),
  catalog staleness. Closes the "verify live rate" caveats.

### LLM reviewer layer — advisory QA above the deterministic gates
Post-run, judgment-level pass over a projection (mappings, confidences, misc%, outliers,
data_gaps) that flags SPECIFIC issues in the ADMIN route (`internal/jobs/admin_handlers.go`
/ Facets panel already exist). Complements validate_fix / outlier_gate (mechanical
violations) by catching "technically valid but smells wrong."
Design rules to honor:
- READ-ONLY / advisory — flags only; humans + curation act. Never auto-mutate mappings.
- CLOSE THE LOOP — each finding is a candidate FACT for the refresh file (e.g. "Clean
  Rooms→BigQuery 0.8× looks wrong, suggest X") → admin approves → refresh persists → next
  bill correct. Reviewer-finds → refresh-persists → improves per bill.
- STRUCTURED findings: `{job, aws_li_key, what, why/evidence, suggested_fix, severity}`.
  Evidence already exists: `mechanic_rule` trace, `misc_annotation`, confidence, outlier
  flags, `data_gaps.json`.

(Full design note: memory `project_refresh_and_reviewer_layer`.)
