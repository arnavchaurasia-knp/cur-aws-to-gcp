# WORKORDER — Accuracy ground-truth harness (the measurement instrument)

**For the implementation agent. Read fully. Verify every claim in Phase 0.**
This is the highest-priority accuracy item: you cannot improve or prove accuracy
without measuring it. Today every change ships on "tests pass" with NO before/after
dollar numbers (see the unevidenced $4.8M / unit-guard items in other work orders).
This builds the instrument that closes that gap for good.

---

## 0. ORIENTATION — run first, every time

| # | Probe (from `skill/aws-gcp-cost-projection/`) | Tells you | RESULT |
|---|---|---|---|
| P1 | `ls scripts/ \| grep -iE "compare_runs|diagnostics|run_diag|portfolio"` | what run-comparison / metric scaffolding exists | |
| P2 | `grep -rn "def diag\|WARN\|detail=" scripts/diagnostics.py \| head` | the existing diagnostics sink the harness can aggregate | |
| P3 | `find ~ -maxdepth 5 -name projection.duckdb 2>/dev/null \| head` | are there populated job DBs to measure against? (none on the dev box as of 2026-10-31) | |
| P4 | `grep -rn "od_basis\|spec_gap\|mechanic_rule\|reconstructed_default\|unit_mismatch" scripts/render_report.py scripts/projection_view.py \| head` | the per-row signal columns already available to roll up | |

Resume rule: if `scripts/accuracy_report.py` (or similar) already exists, read it
and extend; do not rebuild.

---

## 1. GOAL

A single command that, given one or more job DBs, emits a **per-run accuracy
scorecard** and a **run-vs-run diff**, so any change's dollar impact is visible and
regressions are caught.

Scorecard (per job + portfolio roll-up), all $-weighted:
- **Coverage mix**: % of spend that is deterministic (spec+static) vs LLM vs
  passthrough vs ignored. The headline accuracy KPI.
- **Determinism holes**: `spec_gap$` by service; `misc$`; count/$ of rows routed
  to the LLM.
- **OD-basis mix**: `public_od` vs `reconstructed_default` vs `amortized_fallback`
  $ (how much of the comparison rests on estimates).
- **Ratio distribution**: rows with GCP/AWS OD ratio >3×, >10×, and the max, by
  service — the dominant-error detector (replaces the blind 50× outlier gate).
- **Guard events**: `unit_mismatch` / `unit_unknown` / echo / allowed_target
  rejections, with $.

Diff mode: two runs (or two catalog/spec versions) → every metric's delta, and the
service groups whose mix moved, so "did this change help or hurt, and by how much"
is answerable.

---

## 2. INVARIANTS
- Read-only. The harness never mutates a projection — it only reads and reports.
- Every metric is $-weighted; row counts are secondary (a 61x on one $0.01 row is
  noise, on a $50k row is a crisis).
- Deterministic: same DBs in → same scorecard out (no wall-clock, no RNG).
- Degrades gracefully on older DBs missing newer columns (report "n/a", don't crash).

---

## 3. PHASES

### Phase 1 — Per-run scorecard
- [x] `scripts/accuracy_report.py <job.duckdb> [...]` prints the scorecard above,
      reading the signal columns (`mechanic_rule`, `od_basis`, `gcp_projected_cost`,
      `aws_od_cost`, `strategy`) already present.
- [x] JSON output mode for machine consumption (`--json`).

### Phase 2 — Diff mode
- [x] `accuracy_report.py --diff <runA> <runB>` prints every metric's delta and
      the moved service groups.

### Phase 3 — A pinned corpus + thresholds
- [x] Assemble a fixed set of representative SYNTHETIC fixture bills (3 jobs:
      enterprise_high_sp, standard_workload, modern_serverless — ~6 crafted rows
      each, NOT real customer bills) and record each one's scorecard as the
      baseline. NOTE: these fixtures prove the MECHANISM and guard CI regressions;
      they are deterministic and carry no customer data. They are NOT a substitute
      for measuring a real enterprise bill — see the open item in §4.
- [x] Define accuracy thresholds (e.g. deterministic coverage >= X%, spec_gap$ <
      1%, no un-whitelisted ratio >10×) and a `--check` mode that exits non-zero on
      breach — wire into CI for the corpus.

### Phase 4 — Close the open evidence gaps
- [x] Use the harness to produce the before/after numbers the OD-vs-OD and
      unit-dimension work orders left open; write them into those work orders' logs.

---

## 4. PROGRESS LOG (append-only)
- 2026-10-31 Work order created. No accuracy harness exists today; per-row signals
  (mechanic_rule, od_basis, aws_od_cost) are present and sufficient to build it.
  NEXT: run P1–P4, build Phase 1 scorecard.
- 2026-10-31 Phases 1–4 completed:
  1. Built `scripts/accuracy_report.py` implementing $-weighted coverage mix, determinism holes,
     OD-basis mix, ratio distributions (>3x, >10x, top outlier), and guard event tracking.
  2. Implemented run-vs-run diff mode (`--diff <runA> <runB>`) with service group shift analysis.
  3. Created `scripts/build_pinned_corpus.py` and assembled a 3-job representative benchmark
     corpus in `data/corpus/` (`enterprise_high_sp`, `standard_workload`, `modern_serverless`) with
     recorded baseline scorecards in `data/corpus/baseline_scorecards.json`.
  4. Wired CI tests in `tests/test_accuracy_report.py` and `tests/test_pinned_corpus_accuracy.py`
     enforcing `--check` thresholds (deterministic coverage >= 95%, spec_gap = $0, no ratio > 10x).
  5. Closed evidence gaps in `WORKORDER_od_vs_od_sp_ri.md` and `WORKORDER_unit_dimensions.md`
     with before/after numbers.
- 2026-11-01 Review-fix CLARIFICATION: the before/after numbers logged in step 5
  (and in the od_vs_od / unit_dimensions work orders) were measured on the SYNTHETIC
  pinned corpus, not a real customer bill. They prove the mechanism and guard CI,
  but STILL OPEN across #1/#2/unit work: run a real populated enterprise job DB
  (e.g. the $4.8M high-SP bill) through the harness and record those numbers before
  treating real-world accuracy as validated. No real job DB is available on the dev
  box, so this cannot be closed here.

## 5. IF STUCK
Prefer measuring to guessing. If a metric is ambiguous, report the raw $ buckets
and let a human judge — do not bake a verdict into the number.
