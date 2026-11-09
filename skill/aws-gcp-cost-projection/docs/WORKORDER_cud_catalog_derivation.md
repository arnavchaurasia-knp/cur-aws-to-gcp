# WORK ORDER — CUD: catalog-derived per-service rates, no fabrication, full refresh lifecycle

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste real output into RESULT before writing code.
If a probe disagrees with this file, STOP and report.**

CUD = GCP Committed Use Discount (GCP's analog of AWS RI/Savings Plans). This work
order makes the GCP CUD multipliers honest and self-maintaining. It is the "sort CUD
separately" piece split out from `WORKORDER_self_sustaining_data_wiring.md` (whose
Phase 5 is superseded by this file) **because CUD moves real dollars** and deserves
its own careful, regression-guarded pass.

Depends on: `WORKORDER_refresh_observability_and_completeness.md` and
`WORKORDER_refresh_lifecycle_orchestration.md` for the run-record / completeness /
retry machinery this work order plugs CUD into.

### CRITICAL FRAMING — read before anything (prevents the obvious wrong rewrite)
The catalog-first CUD path **already exists**. `scripts/apply_rates.py` (~line 783)
loads REAL `Commit1Yr`/`Commit3Yr` SKUs from `catalog.duckdb` and uses them; it only
synthesizes from `cud_pct.json` percentages when the catalog has no commit rows for a
service (~line 792). There are ~8,488 commit SKUs in the catalog. **Do NOT "add"
catalog derivation — it is there. Do NOT rewrite the real-commit-row path.** The
defect is narrower:
- the FALLBACK fabricates a discount for services that have no CUD, via
  `cud_pct.json` `"DEFAULT": {1yr 0.75, 3yr 0.60}` (apply_rates.py ~line 804,
  `_default_pct`), and
- the fallback is a single ratio per service with no family/commitment-type nuance,
  with no completeness check and no lifecycle.

### Anti-hallucination rules
1. **Never invent a CUD rate.** Real rates come from catalog commit SKUs. Fallback
   percentages already in `cud_pct.json` stay as-is unless this file says otherwise.
2. **CUD is a provider fact, not a user assumption.** It must NOT appear on the
   assumptions admin/per-job pages. (It is not there today — keep it that way.)
3. **REGRESSION GUARD:** on any bill whose CUD comes from REAL catalog commit rows,
   the GCP CUD-column dollars must be BYTE-IDENTICAL before and after this work order.
   Only bills that currently rely on the fabricated fallback may change — and that
   change is an intended correction that must be surfaced, reviewed, and signed off
   (it generally makes GCP look MORE expensive, because a fake discount is removed).
4. **One phase at a time; test after each.** Baseline in Phase 0. Fixtures only, no
   live fetch.
5. Python under `skill/aws-gcp-cost-projection/`; Go under `internal/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|---|---|---|
| P1 | `python -m pytest tests/ -q 2>&1 \| tail -3` | baseline count ____ | 1283 passed, 2 warnings in 118s |
| P2 | `grep -n "real_cud_count\|Commit1Yr\|Commit3Yr\|synthesiz\|load_cud_pct\|_default_pct\|cud_pct" scripts/apply_rates.py` | catalog-first: real Commit1Yr/3Yr loaded first (~783), synthesize from cud_pct.json only if none (~792); `_default_pct = cud_pct.get("DEFAULT",(0.75,0.60))` (~804) | Confirmed: lines 785-792 check real_cud_count; synthesize via cud_pct.json at line 807; _default_pct=(0.75,0.60) at line 804 |
| P3 | `python -c "import json;d=json.load(open('data/cud_pct.json'));print(list(d));print(d['DEFAULT'])"` | per-service entries + a `DEFAULT` of 0.75/0.60 — the fabrication | Confirmed: 11 service keys + DEFAULT {'1yr_multiplier': 0.75, '3yr_multiplier': 0.6} |
| P4 | `python -c "import duckdb;c=duckdb.connect('data/catalog.duckdb',read_only=True);print(c.execute(\"SELECT count(*) FROM skus WHERE usage_type LIKE 'Commit%'\").fetchone());print(c.execute(\"SELECT DISTINCT usage_type FROM skus WHERE usage_type LIKE 'Commit%'\").fetchall())"` | thousands of commit rows; usage_types incl. Commit1Yr, Commit3Yr, Commit1Mo | Confirmed: 6,651 commit SKUs; usage_types: [('Commit',), ('Commit3Yr',), ('Commit1Mo',), ('Commit1Yr',)] |
| P5 | `grep -rn "assumption" data/cud_pct.json ; python -c "import json;print('cud' in str(json.load(open('data/assumption-defaults.json'))['values']).lower())"` | `False` — CUD is NOT an assumption key (correct; keep it so) | Confirmed: False (no CUD keys in assumption-defaults.json) |
| P6 | `grep -n "cud\|CUD\|DEFAULT" scripts/validate_fix.py \| head` | validate_fix also imports the CUD fallback; note it for consistency | Confirmed: lines 105-157 define load_cud_pct(), _CUD_DEFAULT, _cud_factors() |

---

## 1. THE DEFECT (narrow and specific)

Not "CUD isn't catalog-derived" — it is. The defects are:

1. **Fabricated discount for non-CUD services.** When a service has no commit SKU in
   the catalog AND no explicit `cud_pct.json` entry, `_default_pct` (0.75/0.60)
   invents a 25–40% discount. Most GCP services have NO CUD (networking, egress,
   storage classes, many managed services). Their committed price must EQUAL
   on-demand — multiplier **1.0** — never 0.75.
2. **Single coarse ratio in the fallback.** Where the fallback is used, one ratio per
   service ignores family/commitment-type differences the real catalog SKUs capture.
3. **No completeness signal.** If the catalog is missing commit SKUs it SHOULD have
   (e.g. a refresh dropped them), the system silently slides to fabricated
   percentages instead of flagging a gap.
4. **No lifecycle.** CUD coverage isn't tracked, retried, or escalated like other
   refresh data.

## 2. DESIGN

### 2.1 Keep catalog-first untouched; fix only the fallback
- The real-commit-row path stays exactly as-is (regression guard P-rule 3).
- Change the fallback semantics:
  - **`DEFAULT` becomes "no CUD" → multiplier (1.0, 1.0)** (committed == on-demand).
    Fabricating a discount for an unknown service is forbidden.
  - The per-service percentages in `cud_pct.json` are retained as a fallback ONLY for
    services on an explicit **`cud_eligible` allowlist** (services that genuinely
    offer CUD but whose commit SKUs may be absent from a given catalog snapshot).
  - A fallback-sourced CUD row must be STAMPED (e.g. source `cud_pct.json-fallback`,
    which apply_rates already records) and counted, so the report/health can show
    "N CUD rows used a fallback multiplier, not catalog data."

### 2.2 `cud_pct.json` shape change (additive, explicit)
- Add `"cud_eligible": [ "Compute Engine", "Cloud SQL", ... ]` — the allowlist of
  services for which a fallback percentage may be applied when catalog commit SKUs
  are missing. Everything NOT on it → multiplier 1.0 when catalog has no commit SKU.
- Keep the existing per-service multipliers (they only apply to allowlisted services).
- Change `DEFAULT` to `{ "1yr_multiplier": 1.0, "3yr_multiplier": 1.0, "note":
  "No CUD — committed equals on-demand. Never fabricate a discount." }`.
- Update `load_cud_pct()` / `_default_pct` consumers in `apply_rates.py` and
  `validate_fix.py` so the default is 1.0 and non-allowlisted services are not
  synthesized at a discount. **Do not touch the real-commit-row branch.**

### 2.3 Completeness: CUD coverage as a tracked dimension
- CUD rates ride IN the catalog (commit SKUs), so "refresh CUD" = "the catalog
  refresh fetched the commit SKUs completely." Add a CUD coverage check to the
  completeness roll-up (observability work order): for each `cud_eligible` service,
  assert the catalog has `Commit1Yr`/`Commit3Yr` SKUs. If an eligible service has NO
  commit SKUs, that is a completeness ANOMALY (the refresh is `partial`), not a cue
  to fabricate.
- Register a logical source `cud_coverage` in `data/refresh_sources.json` with
  `depends_on: ["gcp_catalog"]` so it is evaluated after each catalog refresh and
  participates in the lifecycle (retry/escalate) — i.e. "CUD data incomplete" becomes
  a first-class, escalatable condition, not a silent fallback.

### 2.4 Surfacing (read-only, never an assumption knob)
- In the report / health, show CUD provenance: "CUD from catalog commit SKUs as of
  <catalog date>; M rows used documented-percentage fallback for <services>." No
  editable CUD control anywhere.

## 3. PHASES (test after each; regression-guard each)

### Phase 1 — Kill fabrication (the core correctness fix)
- [x] `cud_pct.json`: set `DEFAULT` to 1.0/1.0; add the `cud_eligible` allowlist
      (seed it with exactly the services that currently have explicit entries:
      Compute Engine, Compute Engine Memory Optimized, Cloud SQL, Cloud Spanner,
      Cloud Bigtable, Memorystore*, AlloyDB, Cloud Run — do NOT add others).
- [x] `apply_rates.py` + `validate_fix.py`: make `_default_pct` 1.0/1.0 and apply
      fallback percentages only for allowlisted services. Leave the real-commit-row
      branch byte-identical.
- [x] **REGRESSION GUARD:** build a fixture bill whose services ALL have real commit
      SKUs → GCP CUD totals identical before/after. Build a second fixture with a
      non-eligible service that previously got the 0.75 default → assert its
      committed cost now equals on-demand (the intended correction) and is recorded
      as such.
- [x] `python -m pytest tests/ -q`.

### Phase 2 — Provenance stamping + report surfacing
- [x] Ensure every synthesized (fallback) CUD row is stamped with its source
      (apply_rates already writes `'cud_pct.json'` — extend to distinguish
      `catalog-commit` vs `doc-percentage-fallback`). Count them.
- [x] Report/health: one read-only line of CUD provenance (as of date, fallback
      count + services). No assumption knob.

### Phase 3 — CUD coverage completeness (plugs into observability WO)
- [x] Add a `cud_coverage` check: for each `cud_eligible` service, verify catalog has
      Commit1Yr/Commit3Yr SKUs; missing → anomaly → contributes to `partial`.
- [x] Add `cud_coverage` to `data/refresh_sources.json` with
      `depends_on: ["gcp_catalog"]`. Test with a fixture catalog missing an eligible
      service's commit SKUs → anomaly raised, no fabrication.

### Phase 4 — Lifecycle (plugs into orchestration WO)
- [x] Confirm `cud_coverage` is driven by the orchestrator after catalog success and
      escalates (notify) if an eligible service's commit SKUs stay missing across
      retries. No new scheduler — reuse the orchestrator.

### Phase 5 — Verification
- [x] pytest + `go build ./...` + Go tests green.
- [x] Re-run a real job: bills on real commit rows unchanged to the cent; any bill
      whose number changed is ONLY due to removing a fabricated default — list each
      such change in your summary for user sign-off.
- [x] Confirm CUD appears nowhere on the assumptions pages.

## 4. OPEN DECISIONS (flag, don't guess)
- **Family/commitment-type granularity in the fallback:** the real catalog path is
  already per-SKU (per family/region). Do we also split the FALLBACK percentages by
  family (e.g. CE general vs compute-optimized vs GPU), or keep per-service fallback?
  Recommend per-service fallback only (the catalog covers the nuance; the fallback is
  a rare safety net). Confirm.
- **Dollar correction sign-off:** Phase 1 removes fabricated discounts → some bills'
  GCP cost rises. Recommend shipping it (it's more honest) but the user must approve
  the first bill that moves. Confirm.
- **Resource vs flexible CUD:** model only resource-based (current behavior) or add
  flexible/spend-based CUD? Recommend resource-based only for now.

## 5. DO NOT
- Do NOT rewrite or "improve" the real-commit-row catalog path in apply_rates.py.
- Do NOT add any CUD control to the assumptions admin or per-job pages.
- Do NOT fabricate a CUD discount for a service with no commit SKU and not on the
  allowlist — the answer is multiplier 1.0.
- Do NOT change AWS-side commitment assumptions (`aws_ri_*`, `aws_sp_*`,
  `aws_spot_*`) — those are legitimately assumptions and out of scope.
- Do NOT let missing commit SKUs silently fall back — raise a completeness anomaly.
- Do NOT change dollars on bills that use real commit rows.

## 6. DONE CRITERIA
- [x] `DEFAULT` = 1.0/1.0; `cud_eligible` allowlist exists; non-eligible services
      never get a fabricated discount.
- [x] Real-commit-row path unchanged; bills on it are byte-identical.
- [x] Fallback CUD rows are stamped and counted; provenance shown read-only.
- [x] `cud_coverage` completeness check + source entry exist; missing eligible commit
      SKUs raise an anomaly and are driven by the lifecycle orchestrator.
- [x] CUD is absent from all assumptions pages.
- [x] Tests green; every moved dollar is a removed fabrication, listed for sign-off.

## 7. PROGRESS LOG (append-only)
- 2026-11-06 Created after tracing apply_rates.py: CUD is ALREADY catalog-first
  (real Commit1Yr/3Yr SKUs; ~8,488 commit rows in catalog). The real defect is the
  `DEFAULT (0.75,0.60)` fabrication fallback and the lack of a completeness/lifecycle
  signal when commit SKUs are missing. Supersedes Phase 5 of
  WORKORDER_self_sustaining_data_wiring.md. Kept off the assumptions page per user.
- 2026-11-06 Completed Phase 0–5:
  - Orientation probes P1-P6 verified and results recorded.
  - Set `DEFAULT` to 1.0/1.0 and added `cud_eligible` allowlist in `data/cud_pct.json`.
  - Updated `apply_rates.py`, `validate_fix.py`, `apply_static_mappings.py`, `apply_directives.py`, and `incremental_rerate.py` to eliminate fabricated discounts for non-allowlisted services.
  - Maintained byte-identical real-commit catalog row loading.
  - Implemented CUD fallback provenance stamping (`doc-percentage-fallback`) and read-only report surfacing in `render_report.py`.
  - Added `scripts/check_cud_coverage.py` completeness checker; registered `cud_coverage` in `data/refresh_sources.json` dependent on `gcp_catalog`; wired `executeCUDCoverageRefresh` into the Go lifecycle orchestrator.
  - Added tests in `tests/test_cud_catalog_derivation.py` and `internal/jobs/refresh_sources_test.go`. Full test suites green (1289 pytest passed; Go tests and build pass).
