# WORKORDER — Full-Pipeline Audit Remediation (end-to-end, staged)

> ## 🤖 READ FIRST — instructions for the implementing AI
>
> **This file is your task list. Every item below is a thing that is NOT built properly** — a confirmed defect, a dead/half-wired feature, or a declared-but-unimplemented decision. There is no "already fine" filler here; if it's in this file, you fix it.
>
> **Execution rules (do not deviate):**
> 1. **Work top-to-bottom by STAGE.** Finish and test a stage before starting the next. Stages are ordered by dependency and risk.
> 2. **For each item: do exactly the "Fix" section, then make its "Acceptance criteria" pass before moving on.** If you cannot meet an acceptance criterion, STOP and report — do not partially apply and move on.
> 3. **Do not invent scope.** Fix only what the item describes. If you find a related bug, add it to this file under a new item; don't silently expand the change.
> 4. **Honor the industry-grade bar** in "How to use this work order" for every item (no silent failure; add a test that would have caught the bug; no magic numbers; keep signatures/schema unless told otherwise; update docs in the same commit).
> 5. **Verify before you claim done.** Run `cd skill/aws-gcp-cost-projection && python3 -m pytest tests/ -q` and, for Go, `go build ./... && go test ./...` from repo root. A green suite is required to close an item.
> 6. **Update the Verification-status table** (below): flip the item's row to ✅ DONE with the commit SHA. That table is the burndown — keep it truthful.
> 7. **Some items end in a DECISION** (e.g. remove-vs-wire the SP knob, NAT per-VM-hour model). Where an item says "decide", make the call, state your reasoning in the commit message, and encode it — do not leave it ambiguous.
> 8. **Git:** branch off `main`, commit per item, push to personal remotes only (`origin2/AGY`, `github-personal/AGY`) — never company origin.
>
> **If anything in this file contradicts the code you see, trust the code and flag the contradiction in your report** — the file:line references were accurate at handoff (2026-11-02) but code may have moved.


**Created:** 2026-11-02
**Source:** 8-way independent audit (pipeline wiring, OS-license, SKU-id routing, logging, mapping engine, assumptions, per-SKU pricing, half-built sweep). All findings below were cross-checked; the license finding and the Phase-5 finding were manually verified by re-reading the code.
**Repo root:** `C:\Users\arnav\Downloads\cur-web-AGY`
**Skill root:** `skill/aws-gcp-cost-projection/` (all `scripts/…` and `data/…` paths are relative to this unless prefixed with `internal/`, `cmd/`, or `frontend/`).

---

## Verification status — 2026-11-02 (pre-implementation baseline)

Every confirmed defect below was re-checked against live code before this work order was handed off. **Nothing is implemented yet** — this is the untouched baseline. The file:line references are accurate as of this date; an implementer can trust them.

| Item | Claim | Re-verified result |
|---|---|---|
| S1-1 (D1) | `apply_outlier_fixes.py` missing | ✅ CONFIRMED — file absent; still referenced at `internal/jobs/orchestrate.go:436,442,515,517,520` as the sole Phase-5 DB write point |
| S1-3 (D8) | `apply_rates.py` soft-wired | ✅ CONFIRMED — `orchestrate.go:393` is `Script: "?scripts/apply_rates.py"` |
| S2-1 (D2) | pricing matrix decorative | ✅ CONFIRMED — zero `.py` references to `pricing_matrix` (docs-only) |
| S2-2 | multi-currency robustness | OPEN — plumbing present (`currency_utils.py`), gaps per item stand |
| S3-1 (D4) | `aws_sp_discount_pct` moves no dollars | ✅ CONFIRMED — `sp_div` computed at `projection_view.py:113-115`, used only in its own guard, never applied; no `sp_div` in `ingest.py` |
| S3-3 (D5) | `assumptions.py` orphaned | ✅ CONFIRMED — grep for `STRATEGY_ASSUMPTIONS`/`UNSUPPORTED_FEATURES`/`total_confidence_penalty_from_features` returns only the file itself |
| S5-1 (D6) | unguarded bare `"Storage"` rule | ✅ CONFIRMED — present at `classify_mechanics.py:598` |
| (license) | GCP license component wired | ✅ CONFIRMED BUILT — `_license_entries` defined `apply_static_mappings.py:8312`, called `:8588`; `map_compute_windows` dispatched `:9075` (NOT a defect; the AGENTS.md "deferred" note is stale — see S0-1) |

**Implementation progress:** none yet. As each item lands, change its row to ✅ DONE with the commit SHA, so this table doubles as the burndown.

---

## How to use this work order

- **Do the work in the numbered STAGES below, in order.** Each stage is independently shippable and leaves the pipeline in a working state. Do not start a later stage until the earlier stage's acceptance tests pass.
- Every item has: **Problem → Root cause (file:line) → Fix (industry-grade) → Acceptance criteria → Risk/rollback.**
- **Industry-grade bar for every fix:** (a) no silent failure — fail loud or degrade with a logged, typed reason; (b) add/extend an automated test that would have caught the bug; (c) no new magic numbers — read from `data/*.json` or a named constant; (d) keep the existing public function signatures and DB schema unless the item explicitly says to change them; (e) update the relevant `docs/` + `AGENTS.md`/memory status line in the SAME commit so docs never drift again.
- **Run after every stage:** `cd skill/aws-gcp-cost-projection && python3 -m pytest tests/ -q` (baseline is currently green) and, for Go changes, `go build ./... && go test ./...` from repo root.
- Commit per item (or per tightly-coupled pair), message `fix(<area>): <item id> …`, and push to the personal remotes only (`origin2/AGY`, `github-personal/AGY`) per project rule — never company origin.

---

# STAGE 0 — Stale documentation / status corrections (zero code risk, do first)

These are safe, fast, and stop the next reader from "fixing" things that are already shipped. Do them first so the rest of the work order is trusted.

### S0-1. Correct the stale AGENTS.md / MEMORY status lines
**Problem:** Several status lines contradict shipped code and actively mislead implementers.
**What to do** — edit `AGENTS.md` (and the matching memory files under `C:\Users\arnav\.claude\projects\C--Users-arnav-Downloads-cur-web-AGY\memory\`):
1. **License "deferred" note** — the "Deferred (1) OS license component" line is FALSE. The GCP projection license component is built and wired: `_license_entries` defined at `scripts/apply_static_mappings.py:8312`, called at `:8588` inside `map_compute_breakdown`; `map_compute_windows` registered in the mapper dispatch at `:9075`; AWS-side split `scripts/license_split.py` wired into reporting (`export_report.py:11,36,462`, `render_report.py:180,1345`). Rewrite the line to: "OS license modelling — DONE both sides (GCP core+ram+license components + AWS display split). Remaining: ~65 instance types lack `license_fraction`; ratio-bounds safety (see WORKORDER_licensing_and_safe_ratios.md Item 4)."
2. **"ec2+rds only" specs note** — `data/service_specs.json` now specs **54 services**. Replace "foundation seeded (ec2+rds), full rewire pending" with "spec-dispatch OWNS classification (`spec_dispatch.OWNS_CLASSIFICATION=True`); 54 services specced; legacy RULES retained only as long-tail fallback for unspecced services."
3. **"C-7/C-8 block_storage exclusions" note** — those per-service exclusions were REMOVED at the spec-dispatch cutover (see comment `classify_mechanics.py:1290-1294`). Replace with "misroute now prevented by identity-first dispatch; the old block_storage cross-service exclusions were deleted — do not re-add them."
**Acceptance:** `grep -rn "deferred" AGENTS.md` no longer returns the license line; the three notes read as above.
**Risk:** none (docs only).

### S0-2. Fix the two stale "hard gate" comments in the orchestrator
**Problem:** `orchestrate.go:423-424` claims `detect_outliers.py` "fails hard if total rows > 20"; `detect_outliers.py:348` explicitly does NOT exit(1). `orchestrate.go:440` claims `outlier_gate.py` is a "hard gate"; it is invoked soft (`?`, line 445) so its exit(1) is swallowed — the only real Phase-5 enforcement is the Go `CheckSQL`.
**What to do:** correct both comments to describe actual behavior (advisory/soft), OR — if hard enforcement was intended — see S1-2. Comment-only change here; behavior change is tracked in S1.
**Acceptance:** comments match the `?` prefixes actually present.
**Risk:** none.

---

# STAGE 1 — Critical correctness (silent data loss)

### S1-1. [CRITICAL] Phase-5 applier `apply_outlier_fixes.py` does not exist
**Problem:** Phase 5 (outlier triage) LLM output is silently discarded. The orchestrator references `scripts/apply_outlier_fixes.py` as the sole point where triage decisions are written to the DB (`orchestrate.go:436-438,442`: "reads outlier_fixes.json … LLM never touches the DB directly"), but the file does not exist (verified: `ls scripts/apply_outlier_fixes.py` → not found; repo-wide grep finds only references, never a definition, incl. `auto_triage.py:12,70` which writes `triage_candidates.json` "for apply_outlier_fixes.py"). Because it is invoked soft (`?`), `python3 scripts/apply_outlier_fixes.py` exits non-zero (file not found), `run_script` logs `soft_script_failed`, and the run continues. Net: first-pass outlier fixes and all sub-threshold decisions are lost; only rows that trip the `over_and_under_projection` gate get repaired, via the narrower targeted-retry SQL path.
**Root cause:** missing module; soft invocation masks the missing file.
**Fix (industry-grade):**
1. **Create `scripts/apply_outlier_fixes.py`** modeled on the existing, working `scripts/apply_review_fixes.py` (Phase 3's analogue) — same contract: read the LLM's `outlier_fixes.json` from the job dir, validate each record (pydantic or explicit schema check), and apply via parameterized UPDATEs to the mapping/projection tables inside a transaction. Reject unknown `aws_li_key`s, unknown strategies, and targets not allowed for the row's canonical service; log every applied and every rejected fix with a reason. Re-use `auto_triage.py`'s `triage_candidates.json` shape so the two agree on keys.
2. **After it applies, it must trigger a re-rate** of touched rows (call `incremental_rerate` for the changed keys, as the orchestrator already does post-apply).
3. **Make the invocation fail loud if the file is missing but expected:** add a startup existence check in the orchestrator's script resolution so a referenced-but-missing script is a hard error at job start (fast fail), not a per-run soft swallow. (This prevents the whole class of "referenced script silently absent.")
**Acceptance:**
- File exists; `python3 scripts/apply_outlier_fixes.py <jobdir>` on a fixture job with a hand-written `outlier_fixes.json` updates exactly the targeted rows and logs applied/rejected counts.
- New test `tests/test_apply_outlier_fixes.py`: valid fix applied; malformed fix rejected with reason; unknown key rejected; DB unchanged on rejection.
- Orchestrator startup check: a referenced script that doesn't exist on disk aborts the job with a clear error (add a Go unit test that every script in `phaseSpecs` resolves on disk).
**Risk:** writes to DB — wrap in a transaction, dry-run flag for testing. Rollback: revert the file; behavior returns to today's (lossy but non-crashing).

### S1-2. [HIGH] Decide and enforce Phase-5 gate semantics
**Problem:** `outlier_gate.py` is advisory-only (soft) despite comments calling it a hard gate (see S0-2). Phase-5 structural-outlier enforcement therefore rests entirely on the Go `CheckSQL` thresholds.
**Fix:** Make an explicit decision and encode it: either (a) promote `outlier_gate.py` to a real gate (drop the `?`, ensure its exit codes are meaningful and it only fails on genuinely actionable rows), or (b) keep it advisory and delete the misleading comment (done in S0-2) — but then document that the Go `CheckSQL` is the sole Phase-5 enforcement and verify its thresholds match the matrix (see S2). Prefer (a) only if S1-1 lands first (so the gate runs on post-fix state).
**Acceptance:** the wiring and the comment agree; a test asserts the chosen behavior.
**Risk:** promoting to hard could newly fail bills — stage behind S1-1.

### S1-3. [HIGH] `apply_rates.py` (builds the whole projection) is soft-wired
**Problem:** `orchestrate.go:393` invokes the projection-building script soft (`?scripts/apply_rates.py`). If it crashes, the failure is swallowed; `gcp_projection` is never created; the Phase-4/5 gate SQL then errors → `gate()` returns `None` → "not evaluated — continuing"; the run limps to Phase 6 where `render_report.py` (hard) fails late with a misleading error.
**Fix:** This script produces the artifact every downstream step reads — it should be **hard** (remove the `?`), OR keep it soft but add an explicit post-condition gate immediately after Phase 4 that hard-fails if `gcp_projection` is empty/absent, with a message pointing at apply_rates. Prefer making it hard; it is Phase 4's `Script:` (the phase's main job), consistent with `ingest.py` and `render_report.py` being hard.
**Acceptance:** a simulated apply_rates crash produces a clear Phase-4 failure (`failure.txt` naming apply_rates), not a Phase-6 render error. Add a test.
**Risk:** low — surfaces failures earlier and more accurately.

---

# STAGE 2 — Pricing correctness: matrix enforcement + currency robustness

### S2-1. [HIGH] Make `data/pricing_matrix.json` authoritative, not decorative
**Problem:** The Layer-3 "auditable per-SKU matrix" is consumed by zero scripts (grep `pricing_matrix` → only `docs/*.md`). It has drifted from the real decisions and contradicts them: **KMS** matrix=SKU but code keeps/passthroughs (AWS $1.00/key-mo vs GCP $0.06 → ~16x over); **Secrets Manager** matrix=SKU but passthrough (AWS $0.40 vs GCP $0.06 → ~6.7x over); **Route 53** matrix=SKU but review/passthrough (zone $0.50 vs $0.20 → ~2.5x over). Lambda (GB-sec FORMULA + requests SKU) and NAT (gateway-hours) are matrix-declared but implemented as LLM/passthrough.
**Fix (industry-grade, staged within the item):**
1. **Reconcile the matrix with reality first.** For every row, set `status` to the TRUE current state (`active`/`declared_not_implemented`/`rejected`) — stop marking rejected rows `active`. Where the IMPLEMENTATION STATUS block in `AGENTS.md` rejected a decision, the matrix must say `rejected` with the reason.
2. **Wire a post-merge assertion** (new `scripts/assert_pricing_matrix.py`, run as a soft diagnostic post-script in Phase 4, before the gate): for each projected row, look up `(canonical_service, charge_type)` in the matrix; if the row's actual decision (strategy/target) disagrees with an `active` matrix row, emit a `diag(WARN, "matrix_mismatch", …)` with service, expected, actual, and row spend. This makes "decision source: matrix v1" real and auditable, and surfaces drift automatically instead of in a future audit.
3. **Implement the three cheap, high-row-count parity SKUs that are currently overprojecting:** KMS key-versions + ops, Secrets Manager, Route 53 zones/queries — add them as proper spec charge-types / static mappers resolving pinned GCP SKU ids (not passthrough). These are near-deterministic and materially wrong today.
4. **Lambda + NAT** are larger (Lambda needs a GB-sec formula + requests SKU; NAT needs a per-VM-hour modeling decision). Leave them marked `declared_not_implemented` in the matrix and carry them to Stage 5 / a design decision — do NOT silently leave them as `active`.
**Acceptance:**
- `assert_pricing_matrix.py` runs in Phase 4 and logs zero mismatches on a fixture bill for all `active` rows.
- KMS/Secrets Manager/Route 53 rows resolve to pinned GCP SKUs; a regression test asserts the projected $ is within parity of the GCP list price, not the AWS passthrough.
- `pricing_matrix.json` `status` fields are truthful.
**Risk:** the new parity SKUs change projected dollars for those services — intended (they were wrong). Keep passthrough as the fallback if the pinned SKU can't resolve.

### S2-2. [HIGH] Industry-grade multi-currency handling (bill in ANY currency)
**Problem/goal:** The system must correctly project a bill denominated in **any** currency, not just USD. Today the plumbing exists and is reasonable (`currency_utils.py` detects bill currency, resolves FX bill-stated → live → fallback table `data/fx_rates.json`, and converts GCP USD into the bill currency so both sides compare natively — `currency_utils.py:102-291`), but it has gaps that make non-USD bills fragile.
**Known gaps to fix:**
1. **Silent staleness** — if the live FX lookup fails, a week-old committed fallback rate is used silently (warned, not blocked). For a currency with a volatile rate this can skew the whole bill.
2. **Missing-currency behavior undefined** — if the bill currency code isn't in the fallback table AND live lookup fails, confirm the pipeline fails loud rather than defaulting to 1.0 (which would treat, e.g., JPY as USD → ~150x error).
3. **Mixed-currency bills** — confirm behavior if a single bill contains rows in more than one currency (some consolidated/marketplace bills do).
**Fix (industry-grade):**
1. **Make FX resolution explicit and typed:** a single `resolve_fx(bill_ccy, as_of_date) -> FxResult{rate, source, as_of, is_stale}` with source ∈ {bill_stated, live, fallback_table}. Never return a bare float that loses provenance.
2. **Hard-fail closed on unknown currency:** if `bill_ccy` is unknown to all sources, abort ingest with a clear error ("unsupported bill currency XXX — add to data/fx_rates.json or supply a rate") — never default the multiplier to 1.0.
3. **Staleness policy from config:** add `fx_max_staleness_days` to `data/assumption-defaults.json` (default e.g. 7) and to the admin assumptions UI; if the only available rate is older than that AND the bill has no stated rate, emit a prominent WARN in the report header and stamp affected rows with an `[fx-stale]` note (not just a buried log line). Optionally gate hard if `> fx_hard_fail_days`.
4. **Record the FX basis in the report and in the DB** (rate, source, as_of) so every non-USD projection is auditable — the report should state "Projected in <CCY> at <rate> (<source>, <date>)".
5. **Mixed-currency:** detect >1 distinct currency in a bill; if found, either project per-currency sub-totals or fail loud with a clear message — do not silently pick one.
6. **Round-trip test matrix:** add `tests/test_currency.py` covering USD (identity), EUR, GBP, INR, JPY (large magnitude, catches the 1.0-default bug), a currency present only in the fallback table, and an unknown currency (must raise). Assert both sides compare in bill currency and the headline totals convert correctly.
**Acceptance:** all currency tests pass; an INR/JPY fixture bill projects correct magnitude; unknown currency aborts with the specified message; report header shows FX basis.
**Risk:** fail-closed on unknown currency could block a bill that previously (wrongly) ran — that is the correct, safer behavior; document it.

---

# STAGE 3 — Dead / misleading controls

### S3-1. [HIGH] `aws_sp_discount_pct` admin knob moves no dollars
**Problem:** The assumption is seeded, admin-editable, version-hashed, and listed in `assumption_impact.json` as mapped to `ingest.apply_od_basis` — but it has **zero** effect. `ingest.py:76-81` derives only `spot_div`, `ri1_div`, `ri3_div`; Savings-Plan-covered usage is grossed up with the **RI** divisor at `ingest.py:346`. `projection_view.py:113` computes `sp_div` then never references it (`:150-151` use `ri1_div`/`ri3_div`). Editing the knob changes the audit log and version hash but not the projection.
**Fix:** Decide intent and make the system honest:
- If OD-vs-OD netting (per the pricing decision memory) means SP discount is genuinely not a projection input, **remove the knob** from the DB seed, defaults JSON, admin UI, and `assumption_impact.json` — a control that does nothing is worse than no control.
- If SP should net separately from RI, **wire `sp_div`**: compute it in `ingest.py`, apply it on the SP-covered branch (currently the RI branch), and delete the dead `sp_div` in `projection_view.py` or make it the real path.
Prefer removal unless there's a concrete reason SP must differ from RI.
**Acceptance:** either the knob is gone end-to-end, or changing it provably changes projected dollars (new test asserts a delta).
**Risk:** low.

### S3-2. [HIGH] Strengthen the assumptions "ratchet" test
**Problem:** `tests/test_assumptions.py:76 test_every_default_key_referenced_in_scripts` only checks each key *string* appears in `scripts/*.py`. `aws_sp_discount_pct` passes because of the **dead read** — "referenced into an unused variable" slips through. The guard gives false comfort.
**Fix:** Upgrade the test to assert each assumption key actually influences output: for each key, run a tiny projection on a fixture twice (default vs perturbed value) and assert the projected total (or a targeted row) changes. Any key with no measurable effect fails the test (forcing either wiring or removal — see S3-1).
**Acceptance:** the perturbation test passes for all live keys and would fail for a dead one.
**Risk:** low; makes the suite stronger.

### S3-3. [HIGH] `scripts/assumptions.py` is orphaned dead code
**Problem:** The qualitative `STRATEGY_ASSUMPTIONS` / `UNSUPPORTED_FEATURES` registry and `total_confidence_penalty_from_features` / `manual_review_required` logic (`assumptions.py:230-301`) are imported/called by nothing. Nitro Enclaves, FPGA, Mac, Outposts etc. confidence penalties are computed by nobody.
**Fix:** Decide — this is either a missing feature or dead weight:
- **If the intent was real** (flag unsupported features in the report and penalize confidence): wire it into the pipeline — call it during classification/review, attach the penalties to row confidence, and surface the unsupported-feature list in the report. This is genuinely useful for bills with exotic SKUs.
- **If not:** delete the module and its data to stop future readers trusting a feature that doesn't exist.
Recommend wiring it (it closes a real accuracy-honesty gap) but at minimum resolve the ambiguity.
**Acceptance:** either the penalties demonstrably affect confidence on a fixture with an unsupported feature (new test), or the module is deleted and no doc references it.
**Risk:** if wiring, validate penalties don't double-count with existing confidence caps.

---

# STAGE 4 — Logging completeness ("no data left out")

### S4-1. [HIGH] Mapping/validation layer is invisible in the default log stream
**Problem:** Only `ingest.py`, `apply_rates.py`, `render_report.py` use `log_utils.get_logger()` → `debug.log` (the "pipeline" stream, `internal/jobs/job_logs.go:228`). The other ~56 scripts — incl. `aws_normalizer.py`, `classify_mechanics.py`, `apply_static_mappings.py`, `merge_mappings.py`, `resolve_sku.py`, `validate_fix.py` — use bare `print()`, landing in `agy.log` (engine) interleaved with LLM chatter. An operator on the default tab sees almost nothing from the layer that does the cost logic.
**Fix:** Adopt `log_utils.get_logger()` across the mapping/validation scripts (or route their stdout into `debug.log`). Minimum: the normalizer, classifier, static mappers, merge, resolve_sku, validate_fix. Keep structured `diagnostics.jsonl` as-is (it's good).
**Acceptance:** on a fixture run, the "pipeline" stream contains classification/validation activity; a test or manual check confirms key events appear there.
**Risk:** low; more log volume — ensure levels are sane (INFO for milestones, DEBUG for row detail).

### S4-2. [HIGH] Permanent digest undercounts errors and loses the mapping layer
**Problem:** `Watcher.recordJobLogDigest` (`internal/jobs/watcher.go:690-751`) builds the digest from `debug.log` only and returns early; `agy.log` is a fallback used only when `debug.log` is absent (it never is). So `ErrorCount`/`WarnCount`/`FirstError` exclude everything printed by the mappers/gates/normalizer, and `LastLines` is the tail of render_report, not the mapping/gate activity. After the ephemeral job dir is deleted, the digest is the only surviving record (served as `source:"digest"`), so the UI's "Errors: N | Warnings: M" banner is systematically low and the mapping history is gone.
**Fix:** Build the digest from **both** `debug.log` and `agy.log` (and ideally fold in `diagnostics.jsonl` error/warn counts, which are already structured and reliable). Count errors/warns across all sources; keep a tail that includes the end of the run (where failures occur). Prefer the structured diagnostics counts over substring matching (see S4-4).
**Acceptance:** digest error/warn counts match the true totals on a fixture that emits mapper-layer warnings; `FirstError` can be a mapper error.
**Risk:** low.

### S4-3. [HIGH] Unrecognized-products table capped at 20 and mislocated
**Problem:** `aws_normalizer.py:199-204` prints the unrecognized-product table but slices `[:20]`, and uses `print()` (→ agy.log, not pipeline). Its own docstring calls it the "loud failure, not silent misc fallthrough" — but everything past the top-20-by-spend is silently dropped and it's in the wrong stream.
**Fix:** Remove the `[:20]` cap (or make it configurable, default unbounded); emit via `log_utils` so it lands in the pipeline stream; also write it to `diagnostics.jsonl` as a structured record (service, spend, rows) so it survives into the digest.
**Acceptance:** all unrecognized products appear in the pipeline log and in diagnostics; test asserts a 25-product fixture shows all 25.
**Risk:** none.

### S4-4. [MED] Log viewers show the head, not the tail; cap hides truncation
**Problem:** `sliceItems` (`internal/jobs/job_logs.go:320-330`) returns `items[offset:offset+limit]` from the start; both viewers fetch `offset=0` (`JobLogsViewer.tsx:52` limit=500, download limit=2000 capped server-side at `:129`; `frontend/src/api/facets.ts:47` limit=500). For any non-trivial bill the UI shows the oldest 500 lines, can't retrieve past 2000, and failures are at the END. Neither viewer paginates or shows "showing 500 of N" (the `total` field is returned but unused).
**Fix:** Default the viewers to the **tail** (most recent lines) with a toggle for head; wire pagination using the already-returned `total`; raise or remove the 2000 cap for the "Download full" path (stream the file). Show "showing X of N".
**Acceptance:** a >2000-line run shows the last lines by default and can page through all of them; the count is displayed.
**Risk:** low (frontend + one Go handler).

### S4-5. [MED] diagnostics channel silently no-ops if its import fails
**Problem:** `validate_fix.py:49-51` falls back to `def diag(*a,**k): pass` if `from diagnostics import …` fails — every structured diagnostic from the validator is then discarded with no trace.
**Fix:** In the fallback, at least emit one loud log line ("diagnostics disabled: import failed — structured records will be lost") so the degradation is visible. Better: make the import robust (it's a sibling module; fix the path) so it never silently disables.
**Acceptance:** forcing the import to fail produces a visible warning, not silence.
**Risk:** none.

---

# STAGE 5 — Mapping engine upgrades (regex → spec) and remaining half-built modules

These are the "upgrade basic logic" items. None misroutes the big specced services today, but they're the correct backlog and the latent-risk vectors.

### S5-1. [MED] Guard or migrate the unspecced long-tail generic rules
**Problem:** The legacy `RULES` cascade (`classify_mechanics.py:177-1256`, reached via `classify_long_tail_with_trace:1258` for unspecced services) still contains an **unguarded bare `"Storage"` substring rule** (`:598`) and a broad generic `per_request` rule (`:776-851`). For any new/long-tail service with "Storage" in its usage_type and no spec, this re-introduces the exact misroute class the user is worried about (a service with "cloud"/"storage" in the name routed to Persistent Disk). The authors left `# TACTICAL: migrate into service_specs.json` TODOs at `:484,746,760`.
**Fix:** Either (preferred) migrate these generic rules into per-service specs so nothing hits the bare regex, or add a guard so the generic `block_storage`/`per_request` rules only match when the row's canonical service is actually a storage/request service (reuse the identity key). Execute the authors' own TACTICAL TODOs.
**Acceptance:** a synthetic unspecced service "FooCloud Storage Processing" no longer routes to block_storage; test added.
**Risk:** medium — changes long-tail routing; validate against the corpus (`tests/corpus/`).

### S5-2. [MED] Make `service_classifier` match on canonical id first, substring as fallback
**Problem:** The Layer-2 GCP-target forcer `_rule_matches` (`scripts/service_classifier.py:92-109`, `data/service_map.json` 82 rules) matches by raw substring on the product NAME or usage_type **first**, with canonical equality only the third fallback. This is where a "cloud"/substring fragment can false-match an unrelated product at the target layer.
**Fix:** Reorder: match on `canonical_service` equality first; fall back to substring only when the row has no canonical. Add `allowed_targets` per rule is optional (the earlier audit rejected the full machinery as premature) — but the id-first reorder is cheap and closes the false-match hole.
**Acceptance:** a row whose name contains a rule's substring but whose canonical differs is NOT force-targeted; test added.
**Risk:** low/medium — some rules may rely on substring for unspecced rows; keep the fallback.

### S5-3. [MED] GCP rate resolution picks SKUs by word-overlap, not pinned ids
**Problem:** `resolve_sku.resolve_intent_to_sku` (`scripts/resolve_sku.py:232-363`) selects a GCP SKU by `_score_sku_match` bag-of-words overlap between the catalog description and the provided name (`:153-163,301-324`). There's a unit-dimension guard (good) and some static mappers pin ids, but the general path is name-overlap — the clearest remaining "price picked by name" risk on the GCP side.
**Fix:** Where a charge-type has a deterministic GCP SKU (the matrix "SKU" decisions), **pin the GCP SKU id** in the spec/static-mapper instead of relying on word-overlap. Keep word-overlap only as the last-resort resolver for genuinely ambiguous long-tail rows, and log when it's used (so overlap-resolved prices are auditable). Tie this to S2-1 (the matrix becomes the registry of pinned ids).
**Acceptance:** matrix "SKU" charge-types resolve by pinned id; a test asserts the resolved `gcp_sku_id` is the expected pinned one for KMS/S3/EBS/etc.
**Risk:** medium; do after S2-1.

### S5-4. [MED] Consume the stamped `canonical_service` column instead of recomputing
**Problem:** `aws_normalizer.main()` writes `canonical_service` to `aws_li_catalog`, but `classify_mechanics.main():1688` and `service_classifier.main():150` **recompute** it in memory and never SELECT the stored column. Layer-1's persisted output is effectively write-only. Deterministic, so not a bug today, but it means the "Layer 1 is consumed" story is half-wired and the column is dead weight.
**Fix:** Have the consumers SELECT and use the stored `canonical_service` (fall back to recompute only if null), closing the Layer-1 loop. Low-risk since values match.
**Acceptance:** consumers read the column; a test asserts the stored and recomputed values agree on the corpus (and that the column is actually read).
**Risk:** low.

### S5-5. [MED] `classify_transfer.py` is documented as authoritative but never executed
**Problem:** `data/service_specs.json:455,885` and `phases/02-mapping/data-transfer.md:3` state data-transfer direction is "derived by classify_transfer.py," but no script imports or runs it, and `data/transfer-direction-map.json` is read by nothing. Direction is falling back to whatever `spec_dispatch` inlines.
**Fix:** Either wire `classify_transfer.py` into Phase 2 (as a post-script after classification, before merge) so direction is derived as documented and consumes `transfer-direction-map.json`, or — if spec_dispatch's inline handling is sufficient — delete the module/data and fix the specs/docs that claim otherwise. Verify direction correctness on egress/inter-AZ/inter-region fixtures either way (this affects networking $).
**Acceptance:** data-transfer direction is produced by a real, tested code path; docs match.
**Risk:** medium — networking rows are dollar-significant; test against corpus.

### S5-6. [LOW] Remaining dead/placeholder modules
**Problem/fix:**
- `scripts/schema_mapper.py` — fully dead (docs-only references); OPEN-4 "ingest reads spec columns" is designed-not-built (`ISSUES_REGISTER.md:127`). Decide: build OPEN-4 or delete the module + mark OPEN-4 explicitly deferred.
- `scripts/candidate_pool.py` / `candidate_tier.py` — explicit placeholder scoring engine reading placeholder YAML (`candidate_pool.py:19`), wired to nothing. Decide: wire into the pipeline for real, or move to a `scratch/`/`experimental/` dir so it's not mistaken for live code.
- **Catalog B5** (versioned catalog + pointer-swap) — still open; `catalog_manager.py:445-447` does single-file atomic replace only. Implement versioned artifacts + pointer-swap if catalog rollback/pinning is wanted; otherwise close the item in docs.
- **Maintenance scripts with package-style imports** (`equivalence_review.py:27`, `promote_equivalence.py:26` do `from scripts.equivalences import …`; `candidate_pool.py:32` imports external `yaml`) — would `ModuleNotFoundError` if run the way the pipeline runs scripts. Not in the critical path, but fix the imports (sibling-style) or document they must run from skill root, so operators don't hit confusing failures.
**Acceptance:** each item is either wired+tested or removed/relocated+documented; no "authoritative per docs, absent in code" modules remain.
**Risk:** low.

---

## Suggested commit/PR sequencing

- **PR 1 (Stage 0):** doc/status corrections — trivial, merge immediately.
- **PR 2 (Stage 1):** apply_outlier_fixes.py + startup existence check + apply_rates hard-wire + gate-semantics decision. Highest value; silent data loss.
- **PR 3 (Stage 2):** pricing-matrix reconciliation + assertion + KMS/Secrets/Route53 parity SKUs; currency robustness. (Can split into 2a pricing / 2b currency.)
- **PR 4 (Stage 3):** sp_discount knob resolution + ratchet-test upgrade + assumptions.py resolution.
- **PR 5 (Stage 4):** logging adoption + digest-from-both-logs + unrecognized-table + viewer tail/pagination.
- **PR 6 (Stage 5):** mapping-engine upgrades + half-built module resolution; the riskiest, do last with corpus validation.

## Global acceptance gate (run before closing this work order)
1. `cd skill/aws-gcp-cost-projection && python3 -m pytest tests/ -q` — all green, with the new tests added per item.
2. `go build ./... && go test ./...` from repo root — green.
3. Re-run a representative bill end-to-end in each currency fixture (USD + at least one non-USD incl. JPY) and confirm: no silent soft-script failures in the log, Phase-5 fixes applied, matrix assertion clean, FX basis printed in the report.
4. Confirm `AGENTS.md`/memory status lines now match shipped code (S0-1).
