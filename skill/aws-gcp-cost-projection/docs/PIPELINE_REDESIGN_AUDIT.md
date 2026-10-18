# Pipeline Design Audit — AWS→GCP Cost Projection

Scope: Layers 0–4 (ingest → identity → classify → price → merge → validate).
Phase 6 (report) deliberately excluded.
Everything below was read in the current tree, not inferred from the work orders.

---

## Verdict

The pipeline's *declared* design (`docs/TARGET_ARCHITECTURE.md`) is sound. The
*wired* pipeline is a different system: spec-driven dispatch is not actually
dispatching, service identity is re-derived three times from free text, and
every failure path converges on the same output — **passthrough at AWS cost** —
which is indistinguishable from a correct answer in both the DB and the report.

That last point is the structural root of "errors were subdued". It is not one
bug; it is the default.

---

## A. Error suppression — failures cannot surface

### A1. Every failure mode produces the same, plausible-looking output
A crashed mapper, an unmapped row, an unresolvable SKU, a wrong region, an
over-projection and an LLM that dropped rows all end as
`strategy='passthrough', gcp_cost = aws_cost`. A reader sees GCP ≈ AWS and
reads it as a finding. No row-level field distinguishes "deliberately
passthrough because GCP has no equivalent" from "passthrough because something
broke".

### A2. `apply_static_mappings.py` suppresses failure three times over
- `main()` per-handler `try/except Exception` → stderr WARNING, writes an
  **empty** mapping file, comments "rows will be passthrough" (L8597–8604).
- `if __name__` wrapper catches everything → `sys.exit(0)` ("Never exit 1")
  (L8697–8702).
- `orchestrate.go` lists it as `?scripts/apply_static_mappings.py` (soft).

Net effect: `map_compute_breakdown` raising on the $4.8M EC2 job yields a run
that passes every gate and reports a projection equal to the AWS bill.

### A3. The mapping_coverage gate can never fail — and its retry is dead code
`ensure_full_coverage.py` is a Phase-2 **PostLLMScript**, so it stamps
"Manual Review — Unmapped" passthrough on every unmapped row *before*
`CheckSQL` runs. The gate then counts zero. Consequences:
- `_build_mapping_coverage_retry_prompt()` (orchestrate.go L1140+) and the
  "last-resort backstop" block after retry are unreachable.
- The script's own docstring — "Runs ONLY after Phase 2's normal LLM pass +
  one retry have both had a chance" — is false as wired.

### A4. The outlier gate clamps instead of failing
`outlier_gate.py` clamps over-projections to AWS-cost passthrough and
`sys.exit(0)` unconditionally ("a report with clamped rows is better than no
report"), and is additionally invoked soft. An A1 over-projection is never
blocking; it is silently converted into A1-class passthrough.

### A5. Most of `validate_fix.py` is computed and then ignored
It exits 1 on hard violations, but runs `?` (soft) in Phases 4 and 5. Only
three of its checks were re-implemented as Phase-4 `CheckSQL` (null cost /
null gcp_service / echo). **`passthrough_budget`, phantom-zero,
`unreachable_rate` and bill reconciliation are written to
`validation_report.json` and never enforced anywhere in-pipeline.** The 5%
passthrough budget exists only as prompt text and a report footnote.

### A6. The one hard enforcement point is disabled on Windows (verified)
`watcher.go` fail-closes on the validator — but shells out to the literal
`"python3"` (L420, L424). This machine has `python` and no `python3`
(verified). `exec.Command` then fails with a non-`ExitError`, which the
function treats as "environment problem" → **fail open**, gate skipped.
`orchestrate.go` resolves the interpreter properly via `pythonBin()`; the
watcher, `catalog_refresher.go:104` and `compute_availability_refresher.go:116`
do not — so on Windows the final validator never runs and the catalog never
refreshes, both silently.

### A7. A gate that cannot evaluate is logged as "skipping", not as failure
`gate()` returns `None` on any exception → `"could not evaluate (skipping)"`.
The duckdb-CLI dependency was removed, but a schema error, a missing table or
a locked DB still degrade to None, and a skipped gate is indistinguishable
from a passing one in `agy.log`.

### A8. Scale of it
41 `sys.exit(0)` sites across `scripts/`; 13 scripts exit 0 from a bare
`except Exception`. There is no severity taxonomy — "soft" is a `?` prefix in
a Go string, decided per call-site, not a property of the failure.

---

## B. Context extraction — fields are lost without a trace

### B1. Service identity is derived three independent times
1. `ingest.py` enrichment: `if "RDS" in product ... elif "ElastiCache" in product ...`
2. `classify_mechanics.py`: **118** `_ilike(r["product"], ...)` tests vs 55
   references to `canonical`; `canonical` is only ever an extra `or` clause,
   never the key.
3. `apply_static_mappings.py`: each of 34 mappers re-sniffs product/usage_type.

Layer 1 (`aws_normalizer.canonical_service`) exists and is stamped, but nothing
downstream is *keyed* on it. Every new service is therefore fixed 3× — the
whack-a-mole pattern.

### B2. Enrichment misses are silent
In `ingest.py`, `if not itype: continue` and the `if not spec:` path write
nothing but `instance_type` and move on. No counter, no column, no report.
A compute row with NULL `instance_vcpus` is indistinguishable from a row that
legitimately has none — it falls to the LLM or to passthrough, and the reason
is unrecoverable by the time anyone looks.

### B3. Hardcoded spec patch inside the row loop
`LEGACY_SPECS` (8 instance types) is defined **inside** the per-row `for` loop
and rebuilt on every iteration. It patches gaps that belong in
`data/ec2-instance-types.json`.

### B4. `billing_format` is captured, stamped, selected — and never used
It is only ever `'raw_cur'` or `'flat_csv'`; PDF bills are stamped `flat_csv`,
so the format that actually breaks field extraction is not representable.
`classify_mechanics` SELECTs the column into every row dict and **no rule reads
it**. Instead each rule carries its own copy-pasted "PDF bills leave usage_type
blank, also check operation/product" fallback — exactly the DashboardHour,
CPUCredits and Fargate bug class, re-litigated per rule.

### B5. Faithfulness is checked in dollars only
`check_faithfulness()` reconciles totals. Nothing reconciles *field coverage* —
how many compute rows got vCPU/RAM, how many rows got a product_code, how many
got a charge type. A bill can reconcile to the cent with 60% of its context
dropped.

---

## C. Architecture drift — the design is documented but not wired

### C1. Spec dispatch does not dispatch
`data/service_specs.json` has 39 services; `spec_dispatch.dispatch()` is called
from exactly one place in `classify_mechanics.py` (L1629) — **to build misc
guidance text for the LLM**. Classification is still the ~1,400-line ordered
`RULES` lambda chain. `TARGET_ARCHITECTURE.md §Layer 1.5` describes the inverse.

### C2. Four registries, hand-synced, silent on drift
`RULES` groups · `service_specs.json` · `apply_static_mappings.handlers{}` ·
`service_map.json`. Worse, `apply_static_mappings.main()` repeats its group
list **three times in one function** (SQL `IN`-list, `by_group` init dict,
`handlers` dict). A group present in `classify_mechanics` but missing from the
`by_group` init raises `KeyError` outside the per-handler try → the outer
`except` → `exit(0)` → **zero mappings for the entire bill, exit code 0**.

### C3. Generic mechanics claim rows and are then immune to correction
`DETERMINISTIC_GROUPS` includes the generic buckets (`per_request`,
`flat_hourly`, `block_storage`, `object_storage`). First-match ordering lets a
generic rule claim a service-specific row, and membership then exempts it from
`service_classifier.py` correction. Per-service pricing fixes do not land.

### C4. The monolith
`apply_static_mappings.py` is 8,702 lines / 34 mappers. It cannot be
unit-tested per service, cannot be read in one context, and is why per-service
changes keep colliding.

### C5. No decision provenance
There is no per-row record of *which layer decided this and on what grounds*.
The declared Layer-3 artifact (`data/pricing_matrix.json`, service × charge_type
→ decision + reason) was never created. No gate can assert an actual mapping
against a declared intent, and the report cannot show "decision source".

---

## D. Orchestration

- **D1.** The Phase-2 LLM contract is "write three JSON files by convention".
  Nothing asserts the files exist or match a schema before the soft merge; a
  silent LLM no-op surfaces only as coverage, which A3 has already masked.
- **D2.** `merge_mappings.py` `DELETE`s all of `aws_li_to_gcp_li` then
  re-inserts. The mapping_coverage retry prompt tells the LLM to `INSERT`
  directly into that table — those rows are destroyed by any later merge.
- **D3.** Gate retries resend the *entire* phase prompt except for two
  special-cased gates.
- **D4.** `CRITICAL_PHASES = {1,2}`. A crash in review, rate-fill or triage is
  logged and skipped; the report is produced from whatever is in the DB.
- **D5.** `phase_checkpoint.json` can resume at Phase 4 over a DB whose Phase-2
  artifacts were produced by an earlier version of the mappers.

---

## Proposed clean design

**1. Resolve identity once, at ingest.**
`canonical_service` + `charge_type` + a real `billing_format`
(`cur` | `pdf` | `flat_csv` | `xlsx`) become first-class columns. Downstream
code is forbidden from reading `product` as free text — enforced by a test that
greps for `_ilike(.*product` outside the normalizer.

**2. Specs own classification; `RULES` is deleted.**
`spec_dispatch` becomes the only classifier: global pre-filters, then
first-match **within** the service. Split `service_specs.json` into
`data/specs/<service>.json`, one file per service, each charge_type declaring
`decision: SKU | FORMULA | PASS | IGNORE` + `reason` + `mechanic`. That file
*is* the pricing matrix — documentation and behaviour stop being two artifacts.

**3. One registry, verified at startup.**
Split `apply_static_mappings.py` into `scripts/mappers/<mechanic>.py`, each
self-registering. On start, assert every mechanic named by a spec has a mapper
and vice-versa; mismatch = fatal, before any row is touched. Registry drift
becomes impossible instead of silent.

**4. Extraction writes a coverage report.**
`extraction_coverage.json`: per field, resolved / missing / why, plus the
instance types with no spec. A gate on field coverage sits beside the
dollar-reconciliation gate.

**5. A failure taxonomy, replacing the `?` prefix.**
Three classes only: `FATAL` (stop), `DEGRADE` (allowed, but the script **must**
write a typed degradation record: row keys, reason code, dollars affected),
`SILENT` (banned — no bare `except: pass`, no `exit(0)` on exception).
Every passthrough carries `passthrough_reason` ∈ {`no_gcp_equivalent`,
`unresolved_sku`, `mapper_error`, `llm_dropped`, `clamped_outlier`}. Report and
gates read that column; A1 stops being possible.

**6. One gate runner.**
Gates declared in `data/gates.json` with severity; a single `run_gates.py`
executes them. Delete the duplicated `CheckSQL` strings from `orchestrate.go`
(no logic in two languages). A gate that cannot evaluate is a **FAIL**, never a
skip. `passthrough_budget` becomes enforcing.

**7. Interpreter + platform hygiene.**
One resolved interpreter path used by every Go call site (watcher, both
refreshers), matching `pythonBin()`.

**Implementation order:** 5 and 7 first — make failures visible, otherwise
every later change is unverifiable — then 1, then 4, then 2+3 together, then 6.
