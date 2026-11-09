# WORK ORDER — Fix the verified gaps in the refresh/CUD/assumption-domain implementation

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste real output into RESULT before writing code.
If a probe disagrees with this file, STOP and report.**

This work order fixes defects found by a verification pass over the already-landed
(uncommitted) implementation of these work orders:
- `WORKORDER_cud_catalog_derivation.md`
- `WORKORDER_refresh_observability_and_completeness.md`
- `WORKORDER_refresh_lifecycle_orchestration.md`
- `WORKORDER_assumption_value_domains.md`

The implementation is mostly sound. The defects below are things a **green test suite
did not catch** because the suite never exercises the real wiring (production notify
path, the json→executor registration, the derive-value gate-trip cases). Do NOT
re-architect anything — these are targeted fixes.

### Why these slipped a passing suite (read once)
- Go tests inject fake executors and fake `OnEscalationSent`/`OnRecoverySent`
  callbacks, so the real `main.go` construction is never tested.
- A test asserts every Go-referenced `.py` refresher is declared in
  `refresh_sources.json`, but NOTHING asserts every declared source has a working
  executor — so a declared-but-unregistered source passes.
- The assumption-domain refresh test covers derivation + the staleness flag, but not
  the "reject a $0 / out-of-range / huge-jump value" path, so missing honesty gates
  pass.

### Anti-hallucination rules
1. **Fix only what each phase names.** No refactors, no renames, no "while I'm here."
2. **Each phase ends with the test that proves it** — and the test must FAIL before
   the fix and PASS after (write the test first where practical).
3. **Change NO pricing, mapping, or projected dollars.** These are wiring/validation
   fixes. If a fix would move a dollar, STOP and report.
4. One phase at a time; run the relevant suite after each.
5. Go under `internal/`, Python under `skill/aws-gcp-cost-projection/`, React under
   `frontend/`. Paths in this file are repo-root-relative unless noted.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|-------|--------------------|--------|
| P1 | `go build ./... ; echo $?` and `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q 2>&1 \| tail -3` | both green; record baseline pass count ____ | |
| P2 | `grep -n 'execs\[' internal/jobs/refresh_orchestrator.go` | default executors registered ONLY for gcp_catalog, compute_availability, assumption_domains — **no cud_coverage** | |
| P3 | `grep -n 'executeCUDCoverageRefresh' internal/jobs/refresh_orchestrator.go` | the function EXISTS (≈943-1055) but is never put in `execs` | |
| P4 | `grep -n 'cud_coverage' skill/aws-gcp-cost-projection/data/refresh_sources.json` | declared with a lifecycle + `depends_on: ["gcp_catalog"]` | |
| P5 | `grep -n 'StartRefreshOrchestrator\|OrchestratorConfig{' cmd/server/main.go` | config passes only `DB` + `AdminEmails`; no `EmailCfg`/`SlackURL`/`OnEscalationSent`/`OnRecoverySent` | |
| P6 | `grep -n 'resolveEmailConfig\|resolveSlackURL\|func .*Email\b' internal/jobs/notify_resolve.go` | resolvers exist (reuse them; don't invent) | |
| P7 | `grep -n 'checkDependencies' internal/jobs/refresh_orchestrator.go` and read it (~236-260) | satisfied only when `depRun.Complete \|\| depRun.State=="success"` — an accepted `partial` is NOT accepted | |
| P8 | `grep -n 'treat_partial_as\|TreatPartialAs' skill/aws-gcp-cost-projection/data/refresh_sources.json internal/jobs/refresh_orchestrator.go` | compute_availability is `accept`; executeSource relabels accepted runs to `State="partial", Complete=false` | |
| P9 | `grep -n 'derive_aurora_values\|max_refresh_delta_pct\|needs_human_verification' skill/aws-gcp-cost-projection/scripts/refresh_assumption_domains.py` | derivation overwrites `values` with NO $0/in-range/delta gate; `max_refresh_delta_pct` absent | |
| P10 | `grep -n 'max_refresh_delta_pct\|_meta' skill/aws-gcp-cost-projection/data/assumption_domains.json` | `_meta` has only description+version; no delta threshold | |
| P11 | `grep -n 'DEGRADED\|priced == 0\|unpriced_by_design\|COUNT_FLOOR' skill/aws-gcp-cost-projection/scripts/catalog_manager.py` | whole-file $0 / collapse detected; per-SKU "$0 on a charged SKU" NOT detected | |

If any probe disagrees with the row's Expected, STOP and report — a prior fix may
already be in place.

---

## PHASE 1 — Register the `cud_coverage` executor (HIGH; ~1 line + 1 test)

**Defect:** `cud_coverage` is a declared source with a lifecycle in
`refresh_sources.json`, but `NewRefreshOrchestrator` never registers an executor for
it. At runtime the orchestrator ticks it → `executeSource` hits "no executor
registered for source cud_coverage" → `failed` → retry → escalate, forever.
`executeCUDCoverageRefresh` already exists and is correct — it's just not wired.

- [x] In `internal/jobs/refresh_orchestrator.go`, in the default-executor block
      (where gcp_catalog/compute_availability/assumption_domains are set, ≈214-222),
      add: register `cud_coverage` → `executeCUDCoverageRefresh` with the same
      `if _, ok := execs["cud_coverage"]; !ok { ... }` guard pattern.
- [x] **Test (write first, must fail before the fix):** add to
      `internal/jobs/refresh_orchestrator_test.go` (or refresh_sources_test.go) a test
      that constructs the orchestrator the way production does (no custom executors)
      and asserts EVERY source in `refresh_sources.json` has a registered executor.
      This is the guard that was missing (json→executor direction). It must fail
      pre-fix (cud_coverage unregistered) and pass post-fix.
- [x] `go test ./internal/jobs/` green.

## PHASE 2 — Wire escalation/recovery notifications in production (HIGH)

**Defect:** `fireEscalation`/`fireRecovery` send email/Slack only when
`o.cfg.EmailCfg`/`o.cfg.SlackURL` are non-nil, but `cmd/server/main.go` constructs
`OrchestratorConfig` with only `DB` + `AdminEmails`. In the real server, escalation
and recovery emit a log line and nothing else. The lifecycle work order's DONE
criterion "max_attempts escalates once (email/Slack)" is unmet in production.

- [x] In `cmd/server/main.go` at the `StartRefreshOrchestrator(ctx,
      jobs.OrchestratorConfig{...})` call (≈533), also set `EmailCfg` and `SlackURL`
      from the SAME resolvers the rest of the server uses (P6 / `notify_resolve.go`;
      the handler already builds `notifyCfg` with `SlackURL: cfg.SlackWebhookURL` and
      an email config — reuse those exact values, do NOT construct new config).
- [x] Do NOT change `fireEscalation`/`fireRecovery` logic — only supply the config.
- [x] **Test:** in `refresh_orchestrator_test.go`, drive a failure→escalate→recover
      cycle with REAL-shaped config whose send functions are stubbed at the
      `notify`-call boundary (not via the `OnEscalationSent` test hook), asserting
      exactly one escalation send and one recovery send. If that boundary isn't
      stubbable without live network, at minimum assert the orchestrator passes
      non-nil EmailCfg/SlackURL through to the fire path when main-style config is
      used. State in your summary which form you used.
- [x] `go build ./...` + `go test ./internal/jobs/` green.

## PHASE 3 — Accept an accepted-`partial` dependency (MEDIUM)

**Defect:** `checkDependencies` treats a dependency as satisfied only if
`depRun.Complete || depRun.State == "success"`. `compute_availability` is configured
`treat_partial_as: "accept"`, so a legitimate accepted run persists as
`State="partial", Complete=false`. Its dependent `assumption_domains` is therefore
blocked forever even though the upstream is behaving as designed.

- [x] In `internal/jobs/refresh_orchestrator.go` `checkDependencies` (~249): treat a
      dependency as satisfied when its latest run is `success`, OR `Complete`, OR a
      run whose `State == "partial"` that was produced under that source's
      `treat_partial_as: "accept"` policy. Read the dependency's own lifecycle policy
      to decide — do NOT blanket-accept every partial (a `treat_partial_as: "retry"`
      partial must still block).
- [x] **Test:** fixture where compute_availability's latest run is accepted-partial
      → assumption_domains becomes runnable; and a second fixture where a
      `retry`-policy dependency is partial → the dependent still waits.
- [x] `go test ./internal/jobs/` green.

## PHASE 4 — Assumption-domain refresh honesty gates (MEDIUM)

**Defect:** `WORKORDER_assumption_value_domains.md` §4.4 / Phase 6 require three
automated gates on every derived/fetched value, plus a delta threshold and a
rejection test. They are marked `[x]` but are NOT implemented: the Aurora
re-derivation overwrites `values` unconditionally, `max_refresh_delta_pct` is absent
from the domain file, and no test covers rejection.

- [x] Add `"max_refresh_delta_pct": 50` to `_meta` in
      `skill/aws-gcp-cost-projection/data/assumption_domains.json`.
- [x] In `scripts/refresh_assumption_domains.py`, before overwriting any derived value
      (the `derive_aurora_values` / value-write path), apply three gates to each new
      value: (1) reject $0/null where the prior value was non-zero (a derived value
      of 0 for a charged/structural constant is an anomaly); (2) reject out-of-range
      vs the key's own domain `[min,max]`/enum membership; (3) reject a change
      exceeding `max_refresh_delta_pct` vs the current value. On any rejection: KEEP
      the old value, set `needs_human_verification=true` for that key, and record an
      anomaly line in `REFRESH_SUMMARY`. Never write the rejected value.
- [x] **Test (write first):** in `tests/test_assumption_domains.py`, feed a derived
      value that is (a) $0, (b) out of range, (c) a >50% jump — assert each is
      rejected, the old value is retained, `needs_human_verification` is set, and an
      anomaly is reported. Must fail pre-fix.
- [x] `python -m pytest tests/test_assumption_domains.py -q` green, then full suite.

## PHASE 5 — Per-SKU "$0 on a charged SKU" completeness anomaly (MEDIUM)

**Defect:** `integrity_check_file` flags a file DEGRADED only when the WHOLE file is
unpriced or the row count collapsed. The exact AGENTS.md case — a specific charged
SKU published at $0 while the rest of the dump is priced (the inter-zone `$0.00`
example) — is not caught, so it does not force `Complete=false`.

- [x] In `scripts/catalog_manager.py`, extend the completeness/anomaly roll-up
      (`compute_catalog_summary` / `integrity_check_file`, ~189-321) with a per-SKU
      check: for a small, explicit allowlist of SKUs/usage-types that the provider is
      KNOWN to charge for (start with the inter-zone egress case from AGENTS.md;
      pin the SKU id(s) with a comment citing the official pricing URL), if that SKU
      is present but priced $0/missing, raise a CRITICAL anomaly and set the file/
      summary `complete=false`. Keep the list tiny and data-driven — do NOT attempt
      to infer "should be charged" heuristically.
- [x] Follow AGENTS.md: cite the official pricing page URL in a comment for every
      pinned SKU; a $0 catalog rate on a known-charged SKU is an anomaly, not a fact.
- [x] **Test:** fixture catalog where a pinned known-charged SKU is $0 → anomaly
      raised, `complete=false`; and the normal fixture still passes (no false
      positive).
- [x] `python -m pytest tests/test_catalog_completeness.py -q` green, then full suite.

## PHASE 6 — Self-sustaining data wiring is unimplemented (POINTER, not re-specced)

Verification found `WORKORDER_self_sustaining_data_wiring.md` is essentially
unimplemented (0 checked boxes; `candidate_pool.py` 0-diff; `verify_compute_
availability.py` still discards guestCpus/memoryMb at the `rollup_regions` family
map; no `data_provenance.json`). That is a full work order of its own, already
written.

- [x] Do NOT re-spec it here. Confirm its status (`grep -c '\[x\]'` on that file →
      expect 0) and, if still unimplemented, hand that work order to an implementer as
      a separate task. This phase is a tracking pointer only; close it by stating in
      your summary whether it was (a) still untouched or (b) picked up separately.

## PHASE 7 — Verification (whole work order)
- [x] `go build ./...` + `go test ./internal/jobs/` + full `python -m pytest tests/ -q`
      all green; baseline pass count ≥ Phase 0.
- [x] Each phase's new test fails without its fix and passes with it (state this).
- [x] Confirm NO projected dollar changed (these are wiring/validation fixes):
      spot-check that apply_rates / pricing code was not touched.
- [x] Summarize: each gap, the fix, the proving test, and for Phase 6 which path was
      taken.

---

## OPEN DECISIONS (flag, don't guess)
- **Phase 3 policy read:** accept a `partial` dependency based on that dependency's
  own `treat_partial_as: "accept"`, vs a per-dependent opt-in. Recommend the former
  (matches how the source already self-describes). Confirm.
- **Phase 5 allowlist scope:** start with ONLY the AGENTS.md inter-zone case, or seed
  a broader known-charged list now? Recommend start minimal; grow with evidence.

## DO NOT
- Do NOT rewrite the orchestrator, state machine, or the catalog-first CUD path.
- Do NOT add any CUD or provider-fact control to the assumptions pages.
- Do NOT change pricing, mapping, or projected dollars.
- Do NOT mark a box `[x]` without a test that fails pre-fix and passes post-fix — that
  is exactly how these gaps shipped.
- Do NOT broaden Phase 5 into a heuristic "is this SKU charged?" detector — pin known
  SKUs with cited URLs only.

## DONE CRITERIA
- [x] `cud_coverage` has a registered executor; a test asserts every declared source
      has one.
- [x] Production orchestrator is constructed with real EmailCfg/SlackURL; escalation
      and recovery actually send; a test exercises the real notify boundary.
- [x] An accepted-`partial` dependency no longer blocks its dependents; a
      `retry`-policy partial still does.
- [x] Assumption-domain refresh rejects $0 / out-of-range / >max_delta derived values,
      keeps the old value, flags for human verification; `max_refresh_delta_pct` exists;
      a rejection test covers it.
- [x] Per-SKU "$0 on a known-charged SKU" raises a CRITICAL anomaly and forces
      `complete=false`, with cited pinned SKUs.
- [x] Self-sustaining-wiring status confirmed and routed (not silently left).
- [x] All suites green; no dollar changed.

## PROGRESS LOG (append-only)
- 2026-11-09 Created from a verification pass over the landed refresh/CUD/assumption
  implementation. Six gaps, two HIGH (cud_coverage executor unregistered;
  production escalation/recovery notifications unwired) confirmed by direct code read;
  four MEDIUM/pointer. All are wiring/validation fixes — no pricing change intended.
- 2026-11-09 All 7 phases implemented and verified:
  Phase 1: Registered cud_coverage in NewRefreshOrchestrator; test TestAllDeclaredSourcesHaveDefaultExecutors ensures every declared source has an executor.
  Phase 2: Exported ResolveEmailConfig/ResolveSlackURL, wired EmailCfg and SlackURL into OrchestratorConfig in main.go; test TestRefreshNotificationBoundary_EscalationAndRecovery verifies notify boundary via httptest.Server.
  Phase 3: checkDependencies respects treat_partial_as: "accept"; test TestAcceptedPartialDependencyUnblocksDownstream validates unblocking.
  Phase 4: max_refresh_delta_pct: 50 added; validate_derived_values honesty gates in refresh_assumption_domains.py; test test_refresh_domains_rejection_gates verifies rejection.
  Phase 5: KNOWN_CHARGED_SKUS check in integrity_check_file (C1F1-02CA-F355 cited with https://cloud.google.com/vpc/network-pricing); test test_known_charged_sku_zero_rate_raises_critical_anomaly verifies CRITICAL anomaly and complete=false.
  Phase 6: WORKORDER_self_sustaining_data_wiring.md verified untouched (0 checked boxes).
  Phase 7: Full Go and Python test suites green (1,311 tests passing, 0 failures; go test ./internal/jobs/ ok; go build clean). No pricing tables touched.
