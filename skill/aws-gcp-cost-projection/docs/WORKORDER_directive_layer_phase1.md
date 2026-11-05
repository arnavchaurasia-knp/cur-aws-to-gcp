# WORKORDER — Directive Layer, Phase 1: unify the existing override mechanisms

Parent design: `docs/DESIGN_report_refinement.md`. This is Phase 1 only — a **pure
refactor**, no new user-facing feature. Goal: replace the four+ scattered override
mechanisms with ONE directive schema + ONE resolver + ONE apply phase, and prove the
output is byte-identical on the pinned corpus. Ships behind a flag; old scripts are
deleted only after parity is green.

**Scope guard:** no AGY/NL translation, no follow-up loop, no new verbs beyond what the
existing mechanisms already do. Those are Phases 2–4. Do not build them here.

Baseline at start: `pytest tests/` must be green (currently 1243 passed). Every step keeps
it green.

---

## 0. What we are unifying (read these first)

| Existing mechanism | File(s) | Input it reads | Phase |
|---|---|---|---|
| Review fixes | `scripts/apply_review_fixes.py` | `<job>/review_fixes.json` (confirm/override/veto per `aws_li_key`) | 3 |
| Outlier fixes | `scripts/apply_outlier_fixes.py` | `<job>/outlier_fixes.json` (same shape) | 5 |
| Commitment ignores | `scripts/apply_commitment_ignores.py` | derived (RIFee/SP/EDP/negative rows) | 2/3 |
| Assumption overrides | `scripts/config_loader.py` `load_assumptions()`, `internal/jobs/job_assumptions_handler.go` | `<job>/assumptions.json` (+ `.baseline.json`) | all |
| Architecture notes | `scripts/apply_architecture_notes.py` | `data/architecture-notes.json` | post-map |

Orchestration order lives in `internal/jobs/orchestrate.go` (e.g. the
`apply_architecture_notes.py` step ~line 166). The rerun trigger + snapshot writer live in
`internal/jobs/job_assumptions_handler.go` (writes `<job>/assumptions.json`, preserves
`assumptions.baseline.json`, launches a `RunType:"refinement"` rerun).

**Leave `apply_architecture_notes` and `apply_commitment_ignores` behaviour intact** — they
are folded in as directive *types* (`annotate`, `set_strategy=ignore`) but keep their
derivation logic; only their *application* goes through the unified path.

---

## 1. Define the directive schema + ledger I/O  (`scripts/directives.py` — NEW)

Create `scripts/directives.py` as the single source of truth:
- `Directive` dataclass matching `DESIGN_report_refinement.md §2.2` (id, author,
  source_utterance, selector, action, status, confidence, applied-provenance).
- `load_ledger(job_dir) -> list[Directive]` reading `<job>/directives.json` (ordered,
  append-only). Returns `[]` when absent — never raises (mirror `load_assumptions`).
- `save_ledger(job_dir, directives)` — atomic write (temp + rename), same pattern as the
  assumptions snapshot writer in Go; add `directives.baseline.json` on first write.
- JSON schema file `data/directive_schema.json` + a `validate_directive(d)` that rejects
  unknown `action.type`, malformed selectors, missing required params.

Action verbs to support in Phase 1 (exactly the ones the old mechanisms already do — no
more): `set_target_service`, `set_strategy` (map|passthrough|ignore), `set_is_workload`,
`set_assumption`, `annotate`. (Family/shape/region/pricing verbs are Phase 3.)

Tests (`tests/test_directives.py`, NEW): load/save round-trip, empty-ledger safety,
schema rejection of bad verbs, atomic-write leaves no partial file on simulated failure.

---

## 2. The resolver  (`scripts/directive_resolver.py` — NEW)

`resolve(rows, directives) -> dict[aws_li_key, EffectiveOverride]`.
- Selector matching uses the **same identity fields** as spec-dispatch
  (`canonical_service`, `product_code`, derived `arch`/`instance_family`, region, tag,
  account) — reuse `aws_normalizer.canonical_service`; do NOT invent a second matcher.
- **Specificity order** (design §2.3): explicit `aws_li_keys` pin > tag/account >
  family+arch > service > global. **Recency breaks ties.** Losing directive on a field is
  marked `superseded` (recorded, not dropped).
- One verb per field: two `set_target_service` on one row → later wins.
- Output per row includes `applied_directives: [ids]` for provenance.

Tests (`tests/test_directive_resolver.py`, NEW): specificity ordering, recency tie-break,
supersession recorded, a pinned `aws_li_key` beats a broad service selector, non-matching
directive is a no-op.

---

## 3. The single apply phase  (`scripts/apply_directives.py` — NEW)

`apply_directives.py <DB>`:
1. Load rows from `aws_li_catalog` + current `aws_li_to_gcp_li`.
2. `load_ledger(job_dir)` → `resolve(...)`.
3. Apply each effective override to `aws_li_to_gcp_li` (service/strategy/is_workload/
   assumption-driven re-map/note) — **one writer**, replacing the per-mechanism writers.
4. Stamp an `applied_directives` column on the mapping table (new, idempotent column, same
   migration style as `canonical_service`).
5. Runs **after** baseline mapping, **before** rate-fill, so `incremental_rerate` re-rates
   only rows whose mapping changed.
6. `--check-only` skips writes (consistency with `validate_fix.py`).

**Translation shim (the parity keystone):** add `scripts/directives_from_legacy.py` that
converts existing `review_fixes.json` + `outlier_fixes.json` + `assumptions.json` +
commitment-ignore derivation into an equivalent `directives.json`. This lets a legacy job
run through the new path unchanged and produce the same projection. Keep the shim until
Phase 2 writes directives natively.

---

## 4. Wire into orchestration (behind a flag)

In `internal/jobs/orchestrate.go`:
- Add a feature flag `DIRECTIVE_LAYER` (env/config, default **off**).
- **Off:** pipeline runs exactly as today (old apply_* steps).
- **On:** run `directives_from_legacy.py` (shim) → `apply_directives.py` in place of
  `apply_review_fixes.py` + `apply_outlier_fixes.py` + `apply_commitment_ignores.py` +
  the assumptions merge. `apply_architecture_notes.py` stays until §6.
- Generalize `job_assumptions_handler.go`: the rerun it already launches should snapshot
  `directives.json` (superset of `assumptions.json`) when the flag is on. Keep writing
  `assumptions.json` too while the shim exists.

Go tests: extend `internal/jobs/job_assumptions_test.go` + `duckdb_test.go` (the
`RunType:"refinement"` case already exists) to cover a directive-triggered rerun.

---

## 5. Parity gate (do NOT delete anything until this passes)

Add `tests/test_directive_parity.py`:
- For each job in the pinned corpus (`tests/corpus/`), run the pipeline **both** ways
  (flag off = legacy, flag on = directive path via the shim) and assert the resulting
  `gcp_projection` is **row-for-row identical** (same gcp_service, strategy, gcp_sku_id,
  projected_cost within rounding, is_workload, notes).
- Any diff is a blocker. The whole point of Phase 1 is "cleaner, provably same output."

Run `python -m pytest tests/ -q` — full suite green, plus the new parity test.

---

## 6. Cut over and delete (only after §5 is green)

1. Flip `DIRECTIVE_LAYER` default **on**.
2. Make `apply_directives.py` own `architecture-notes` application too (fold in as
   `annotate` directives), then delete `apply_architecture_notes.py`.
3. Delete `apply_review_fixes.py`, `apply_outlier_fixes.py`, `apply_commitment_ignores.py`
   and their orchestrate.go steps. Keep the shim one more release for old on-disk jobs,
   then delete it.
4. Update `phases/03-review.md` and `phases/05-outlier-triage.md` to describe the single
   directive apply.
5. Remove the now-dead `review_fixes.json` / `outlier_fixes.json` branches once no job
   produces them.

---

## 7. Done criteria
- One schema (`directives.py`), one resolver, one apply phase.
- Parity test green on the full pinned corpus (byte-identical projections).
- Full `pytest tests/` + Go `go test ./internal/jobs/...` green.
- Four apply_* scripts + their orchestrate steps deleted; architecture-notes folded in.
- Provenance column `applied_directives` populated and readable by the report (rendering
  of it is Phase 2, but the data must be present now).

## 8. Explicitly out of scope (later phases)
AGY NL→directive translation (Phase 2), follow-up/clarification thread (Phase 2), new verbs
`set_target_family`/`set_target_shape`/`set_region`/`region_policy`/`set_pricing_basis`
(Phase 3), reversibility UX + directive history in the report (Phase 5). Do not start them
here — Phase 1 earns its keep as a refactor alone.
