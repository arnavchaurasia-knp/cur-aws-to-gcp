# WORK ORDER — Internal run diagnostics: record every degradation without failing the job or surfacing it externally

**For an implementing AI / engineer with no prior context. Read this file fully
before editing anything.**

Paths starting `scripts/` or `data/` are relative to
`skill/aws-gcp-cost-projection/`. Paths starting `internal/` are relative to the
repository root.

This work order touches **Go and Python**. It changes **internal logging and
diagnostics only** — no pricing, no SKU selection, no classification, no
mapping, nothing in the HTML report, nothing a customer sees, and (with one
narrow exception in §4 Step 3) no control flow. If a dollar figure moves, you
have made a mistake.

**Companion:** `WORKORDER_passthrough_reason_provenance.md` labels each degraded
row at the row level. This work order aggregates those labels into an internal
run summary. They are independent — either can land first.

---

## 0. Design principles — read these first, they govern every decision below

**P1 — Fail open.** This is a customer-facing tool. A produced report is almost
always better than no report. Nothing in this work order becomes newly fatal.
The only hard-stop conditions remain the existing ones (bill cannot be ingested,
model API unavailable), which already write `failure.txt` and are not touched.

**P2 — Internal only.** Everything produced here is for the engineering team.
Nothing written by this work order appears in the HTML report, the UI, the job
status, a customer email, or a Slack notification. The customer-facing output is
unchanged, byte for byte.

**P3 — Neutral, factual wording.** Records state what happened and what it cost,
in plain operational language: `"412 rows ($48,213.55) carried at AWS cost —
compute_breakdown mapper raised KeyError"`. No verdict labels, no severity
banners, no editorialising. Specifically: **do not** use the words
`unverified`, `untrustworthy`, `not trustworthy`, `weakness`, `broken`,
`failure`, `RED`/`AMBER`/`GREEN`, or any ALL-CAPS banner anywhere in this work
order's output.

**P4 — Quiet in the log, complete in the file.** One short line per event in
`agy.log` so it is greppable. The full structured detail lives in JSON files in
the job directory, read on demand. No end-of-run banner.

The problem being solved is not that the pipeline continues. It is that today a
run that degraded and a run that went perfectly leave behind identical evidence,
so nobody on the team can tell them apart afterwards.

---

## 1. The problem

### 1.1 The final validator never runs on Windows

`internal/jobs/watcher.go` runs `scripts/validate_fix.py --check-only` after a
job finishes, as the literal string `"python3"`:

```go
if err := exec.Command("python3", script, jobDir).Run(); err != nil {       // L420
out, err := exec.Command("python3", script, "--check-only", jobDir).CombinedOutput()  // L424
```

This machine has `python` and no `python3`. `exec.Command` returns a
`*exec.Error` (not an `*exec.ExitError`), and the function treats that as an
environment problem and returns "passed".

Everywhere else already handles this — `facets.go` (×3), `handler.go`,
`orchestrate.go` all use `pythonBin()`; both background refreshers do an inline
`LookPath` fallback. `watcher.go` is the only place that was missed, and it is
the validator.

### 1.2 Its result is discarded

`runValidatorGate`'s doc comment claims it fails closed. The call site (~L181)
does not:

```go
if ok, summary := runValidatorGate(jobDir); !ok {
    slog.Warn("validation gate violations (report still served)", ...)
}
if err := w.db.UpdateJobDone(jobID, spend); err != nil {
```

Serving the report anyway is correct (P1). Discarding the summary is not — it
reaches one `slog.Warn` and nowhere else, so it is gone by the time anyone looks.

### 1.3 A gate that errors is indistinguishable from a gate that passed

```python
def gate(sql):
    try:
        ...
    except Exception:
        return None
```
```python
if v is None:
    log("gate %s: could not evaluate (skipping)" % ...)
```

A locked DB, a missing table, a schema change or a SQL typo all produce the same
quiet line, and `validation_report.json` has no field recording that a gate ran.

### 1.4 The coverage backstop fires before the retry it was built to follow

`scripts/ensure_full_coverage.py` passthroughs every unmapped row. Its docstring
says it *"Runs ONLY after Phase 2's normal LLM pass + one retry have both had a
chance."* In `orchestrate.go` it is the last entry of Phase 2's
`PostLLMScripts`, which run **before** `CheckSQL`. So the `mapping_coverage`
gate always counts 0, never fails, and `_build_mapping_coverage_retry_prompt()`
(~L1140) is unreachable code — a row the LLM dropped never gets a second
attempt.

A second copy of the same inversion sits inside the gate-failure handler
(`if v and v > 0 ... run_script("?scripts/ensure_full_coverage.py $DB")`), again
before the retry.

---

## 2. The design

### 2.1 One internal diagnostics channel

Scripts that degrade the projection append a typed record to
`<jobDir>/projection-audit/diagnostics.jsonl` (one JSON object per line,
append-only, safe for concurrent writers) and print one short line to stdout so
it lands in `agy.log`.

```json
{"ts":"2026-10-15T11:04:22","level":"error","code":"mapper_error",
 "source":"apply_static_mappings.py","phase":2,
 "message":"compute_breakdown mapper raised KeyError: 'instance_vcpus'; 412 rows carried at AWS cost",
 "rows":412,"usd":48213.55,"detail":{"mechanic_group":"compute_breakdown"}}
```

Log line (neutral, greppable, one line):

```
[diag] error mapper_error src=apply_static_mappings.py rows=412 usd=$48,213.55 :: compute_breakdown mapper raised KeyError
```

### 2.2 Three levels

| `level` | Meaning |
|---|---|
| `error` | A material part of the projection is not a real GCP price (mapper raised, rows unmapped, a check could not be evaluated). |
| `warn` | Expected or immaterial (small clamp, free-tier ignore, unresolved instance spec on a low-cost row). |
| `info` | Notable, not a degradation. |

No fourth "blocker" level: genuine hard stops already use `failure.txt` and are
out of scope.

Impact is measured in **dollars**, not row count — that is what tells the team
how much of the number is affected.

### 2.3 An internal run summary — counts, no verdict

After the report is produced, `scripts/run_diag_summary.py` aggregates
`diagnostics.jsonl` into `<jobDir>/run_diagnostics.json` and prints a compact
2–6 line block to `agy.log`.

**No GREEN/AMBER/RED. No banner. No adjectives.** It reports: workload spend,
spend affected by `error`-level records, the count of checks that could not be
evaluated, and a per-code breakdown. That is enough for the team to answer
"what happened on job X, and how much did it touch?" and nothing more.

### 2.4 Nothing leaves the job directory

`diagnostics.jsonl`, `run_diagnostics.json` and `validation_verdict.txt` are
internal artifacts in the job dir alongside `agy.log` and `debug.log`.
`render_report.py` is not modified and must not read them. Job status, emails
and Slack notifications are unchanged.

---

## 3. Files to read before you start

- `internal/jobs/watcher.go` — `runValidatorGate` (~L407-434) and its call site (~L181)
- `internal/jobs/orchestrate.go` — `phaseSpecs()` Phase 2 and Phase 6 blocks, and
  inside `orchestratorTemplate`: `gate()`, `run_script()`, `run_phase()`, `log()`
- `scripts/log_utils.py` — existing logging conventions and `debug.log` location
- `scripts/validate_fix.py` — `main()`, how `report` / `counts` / `n_hard` are built
- `scripts/apply_static_mappings.py` — `main()`'s per-handler `except` block

**Critical note on `orchestrate.go`:** the Python orchestrator lives inside a Go
raw string literal (`orchestratorTemplate`) rendered with `fmt.Sprintf`. Inside
it, **every literal `%` must be written `%%`**. Get this wrong and the
orchestrator emits corrupted source that only breaks at job runtime.

---

## 4. Step-by-step implementation

Do the steps in order. Build/test after each.

---

### STEP 1 — The diagnostics helper: `scripts/diagnostics.py`

New file. The only writer of `diagnostics.jsonl`.

```python
#!/usr/bin/env python3
"""Internal diagnostics channel for the projection pipeline.

Scripts that degrade the projection — a mapper that raised, rows left unmapped,
a check that could not be evaluated, a clamped outlier — record it here so the
engineering team can see afterwards what happened and what it cost.

These records are INTERNAL. They stay in the job directory next to agy.log and
debug.log. They are never read by render_report.py and never reach a customer.

Nothing in this module raises and nothing exits: a diagnostics problem must
never affect a run.

Usage:
    from diagnostics import diag, ERROR, WARN
    diag(ERROR, "mapper_error", "apply_static_mappings.py",
         "compute_breakdown mapper raised KeyError; 412 rows carried at AWS cost",
         rows=412, usd=48213.55, phase=2,
         detail={"mechanic_group": "compute_breakdown"})
"""
import json
import os
import time

ERROR = "error"
WARN = "warn"
INFO = "info"

_VALID = (ERROR, WARN, INFO)


def _path(job_dir=None):
    base = job_dir or os.environ.get("JOB_DIR") or os.getcwd()
    audit = os.path.join(base, "projection-audit")
    try:
        os.makedirs(audit, exist_ok=True)
    except Exception:
        return None
    return os.path.join(audit, "diagnostics.jsonl")


def diag(level, code, source, message, rows=0, usd=0.0, phase=None,
         detail=None, job_dir=None):
    """Append one diagnostic record. Never raises."""
    try:
        if level not in _VALID:
            level = WARN
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": level,
            "code": str(code),
            "source": str(source),
            "phase": phase,
            "message": str(message)[:2000],
            "rows": int(rows or 0),
            "usd": round(float(usd or 0.0), 2),
            "detail": detail or {},
        }
        print("[diag] {level} {code} src={source} rows={rows} usd=${usd:,.2f} :: {message}"
              .format(**rec), flush=True)
        p = _path(job_dir)
        if p:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def read_all(job_dir=None):
    """Return every diagnostic record. Never raises; returns [] on any problem."""
    out = []
    try:
        p = _path(job_dir)
        if not p or not os.path.exists(p):
            return out
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out
```

Verify, then delete the test file it creates:

```bash
cd skill/aws-gcp-cost-projection
python -c "import sys; sys.path.insert(0,'scripts'); from diagnostics import diag, ERROR; diag(ERROR,'test','x.py','hello',rows=3,usd=1.5); print('ok')"
```

---

### STEP 2 — Fix the interpreter and keep the validator result

All in `internal/jobs/watcher.go`.

**2a.** Replace both literal `"python3"` occurrences in `runValidatorGate` with
`pythonBin()`. That function already exists in `orchestrate.go`, same package —
no import, no new helper. **Do not write a second copy.**

**2b.** Change `runValidatorGate` to return three outcomes, so "could not run"
is no longer collapsed into "passed":

```go
// validatorOutcome describes what actually happened when the gate ran.
// "could not run" is deliberately distinct from "passed": in that case the
// check produced no result at all, which the team needs to be able to see.
type validatorOutcome int

const (
	validatorClean  validatorOutcome = iota // exit 0
	validatorFailed                         // exit 1 or 2 — violations reported
	validatorNotRun                         // could not execute at all
)

func runValidatorGate(jobDir string) (validatorOutcome, string) {
	script := filepath.Join(skill.ResolveDir(), "scripts", "validate_fix.py")
	if _, err := os.Stat(script); err != nil {
		return validatorNotRun, "validator script absent at " + script
	}
	// Autofix pass first (regional SKU repair, per-N clamping, CUD synthesis).
	if err := exec.Command(pythonBin(), script, jobDir).Run(); err != nil {
		log.Printf("autofix pass failed for %s (non-fatal): %v", jobDir, err)
	}
	out, err := exec.Command(pythonBin(), script, "--check-only", jobDir).CombinedOutput()
	summary := strings.TrimSpace(string(out))
	if err != nil {
		if _, ok := err.(*exec.ExitError); ok {
			return validatorFailed, summary
		}
		return validatorNotRun, "validator could not execute (" + err.Error() + "): " + summary
	}
	return validatorClean, summary
}
```

**2c.** Replace the call site (~L181). Keep the job flow identical — only the
recording changes:

```go
		outcome, vsummary := runValidatorGate(jobDir)
		// The report is always served (see WORKORDER §0 P1). This only keeps the
		// verdict: persist it next to the report so the team can tell afterwards
		// whether the check was clean, reported violations, or never executed.
		// Internal only — not surfaced to the customer or the job status.
		writeValidationVerdict(jobDir, outcome, vsummary)
		switch outcome {
		case validatorFailed:
			slog.Warn("validator reported violations", "job_id", jobID, "summary", vsummary)
			metrics.ValidationViolations.Add(1)
		case validatorNotRun:
			slog.Warn("validator did not execute", "job_id", jobID, "detail", vsummary)
			metrics.ValidationNotRun.Add(1)
		default:
			slog.Info("validator clean", "job_id", jobID)
		}
```

**Note:** no `BLOCK_ON_*` flag and no `UpdateJobFailed` call here. An earlier
draft of this work order proposed one; it is deliberately omitted. The job is
marked `done` exactly as it is today, in every outcome.

**2d.** Add the helper in the same file:

```go
// writeValidationVerdict records the gate outcome as a file in the job dir so
// the verdict survives log rotation. Internal artifact; nothing reads it
// automatically.
func writeValidationVerdict(jobDir string, outcome validatorOutcome, summary string) {
	label := "clean"
	switch outcome {
	case validatorFailed:
		label = "violations"
	case validatorNotRun:
		label = "not_run"
	}
	body := label + "\n" + time.Now().UTC().Format(time.RFC3339) + "\n\n" + summary + "\n"
	path := filepath.Join(filepath.Clean(jobDir), "validation_verdict.txt")
	if err := os.WriteFile(path, []byte(body), 0644); err != nil {
		log.Printf("writeValidationVerdict: %v", err)
	}
}
```

**2e.** Add two counters in `internal/metrics/`, matching the exact declaration
style of the existing `JobsDone` / `JobsFailed`:

```go
ValidationViolations // validator reported violations
ValidationNotRun     // validator could not execute
```

**2f.** In `internal/preflight/check.go`, the tool list
`[]string{"python3", "duckdb", "jq", "gzip"}` warns about `python3` even when
`python` is present and usable. Change that one entry to accept `python3` **or**
`python`, warning only when neither exists. Leave `duckdb`, `jq`, `gzip`
untouched.

Verify:

```bash
go build ./... && go vet ./internal/...
grep -rn '"python3"' internal/jobs/watcher.go      # must return nothing
```

---

### STEP 3 — Put the coverage backstop back behind its retry

In `internal/jobs/orchestrate.go`. **The only control-flow change in this work
order.** It adds no failure mode — it restores a retry that is currently
unreachable.

**3a.** In the Phase 2 `PostLLMScripts` list, **delete** this line:

```go
				"?scripts/ensure_full_coverage.py $DB",
```

Leave every other entry, in its existing order, untouched.

**3b.** Inside `orchestratorTemplate`, in `run_phase()`, **delete** this
pre-retry block:

```python
        if v and v > 0 and ph.get("check_name") == "mapping_coverage":
            # Deterministically backstop unmapped rows before deciding whether to invoke LLM
            run_script("?scripts/ensure_full_coverage.py $DB")
            v = gate(sql)
```

**3c.** Leave the **post-retry** backstop exactly as it is — the block guarded by
`if v2 and v2 > 0 and ph.get("check_name") == "mapping_coverage":`. That one is
correctly placed and becomes the only invocation.

**3d.** Update the stale comment above that block: it says the script *"was
never actually invoked anywhere in this orchestrator until now"*. Replace that
sentence with a note that this is now the only invocation and that it runs after
the LLM retry has had its chance.

**3e.** In `scripts/ensure_full_coverage.py`, emit one record before it writes
the backstopped rows:

```python
    try:
        from diagnostics import diag, ERROR
    except ImportError:
        def diag(*a, **k): pass
        ERROR = "error"
    ...
    if backstopped_rows:
        diag(ERROR, "rows_unmapped_after_retry", "ensure_full_coverage.py",
             f"{len(backstopped_rows)} row(s) had no mapping after the Phase-2 "
             f"retry; carried at AWS cost",
             rows=len(backstopped_rows), usd=backstopped_usd, phase=2)
```

Use the script's existing variable names for the row list and their summed
`aws_amortized_cost`; add a plain `sum()` if it does not already compute one.

**Expected consequence:** the `mapping_coverage` gate can now fail, so the
targeted retry will start firing on bills where the LLM drops rows — one extra
LLM call on those jobs. The job still completes either way: if the retry does
not fix it, the backstop runs exactly as before and now also records one line.

---

### STEP 4 — Record checks that could not be evaluated

Inside `orchestratorTemplate`. **Nothing here becomes fatal.** The run continues
exactly as today; only the record changes.

**4a.** Change `gate()` to report *why* it failed:

```python
def gate(sql):
    # Returns (violations:int|None, error:str|None).
    # None violations means the gate could NOT be evaluated. The run continues;
    # the condition is recorded so it is distinguishable from a clean check.
    try:
        import duckdb as _duckdb
        _con = _duckdb.connect(DB, read_only=True)
        try:
            row = _con.execute(sql).fetchone()
        finally:
            _con.close()
        if not row or row[0] is None:
            return None, "gate SQL returned no value"
        return int(row[0]), None
    except Exception as _e:
        return None, "%%s: %%s" %% (type(_e).__name__, _e)
```

**Remember the `%%` rule** (§3).

**4b.** Update the three `gate(sql)` call sites in `run_phase()` to unpack the
tuple. The first:

```python
    sql = ph.get("check_sql")
    if sql:
        v, gerr = gate(sql)
        if v is None:
            log("gate %%s: not evaluated (%%s) — continuing"
                %% (ph.get("check_name","?"), gerr))
            _diag("error", "check_not_evaluated", "orchestrator",
                  "gate '" + str(ph.get("check_name","?")) + "' not evaluated: "
                  + str(gerr), phase=ph["num"])
        elif v > 0:
            ...
```

Apply the same unpacking to the post-retry and post-backstop calls. There, a
`None` result is logged and recorded but does **not** trigger another retry —
there is nothing to retry when the check itself did not run.

**4c.** Add a thin shim near `write_progress()` in the template, so the
orchestrator can record without importing the skill module (`SKILL_DIR` may not
be importable from the job dir):

```python
def _diag(level, code, source, message, rows=0, usd=0.0, phase=None):
    """Append a diagnostic record. Mirrors scripts/diagnostics.py's format.
    Never raises."""
    try:
        rec = {"ts": time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S"),
               "level": level, "code": code, "source": source,
               "phase": phase, "message": str(message)[:2000],
               "rows": int(rows or 0), "usd": round(float(usd or 0.0), 2),
               "detail": {}}
        log("[diag] " + level + " " + code + " :: " + str(message)[:500])
        d = os.path.join(JOB_DIR, "projection-audit")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "diagnostics.jsonl"), "a", encoding="utf-8") as _f:
            _f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass
```

**4d.** In `run_script()`, the soft-script branch currently logs and returns. Add
one record — a `?`-script that died today leaves only a single log line:

```python
        if soft:
            log("[SCRIPT] soft script exited %%d (non-fatal): %%s" %% (result.returncode, " ".join(full)))
            _diag("error", "soft_script_failed", "orchestrator",
                  "soft script exited " + str(result.returncode) + ": " + " ".join(full))
            return
```

**4e.** Grep the template for `could not evaluate` and make sure no remaining
gate message uses the word `skipping`.

---

### STEP 5 — Record the places that already degrade silently

Minimal and surgical. In each case **add the `diag(...)` call and change nothing
else.**

| File | Where | Record |
|---|---|---|
| `scripts/apply_static_mappings.py` | `main()`'s per-handler `except Exception` block | `error`, `mapper_error` — group name, row count, summed `aws_amortized_cost` |
| `scripts/apply_static_mappings.py` | end of `main()`, if `all_llm_rows` non-empty | `warn`, `rows_sent_to_llm` — count + usd |
| `scripts/outlier_gate.py` | after the clamp `UPDATE` | `error`, `outlier_clamped` — clamped row count + usd |
| `scripts/fix_storage_misroute.py` | after its `UPDATE` | `warn`, `storage_misroute_rerouted` — count + usd |
| `scripts/ingest.py` | enrichment loop, the `if not spec:` / `if not itype: continue` paths | accumulate during the loop, then **one** `warn` `instance_spec_unresolved` record after the loop with the total count, usd and up to 20 example instance types. **Never one record per row.** |
| `scripts/validate_fix.py` | end of `main()`, if `n_hard > 0` | `error`, `validator_violations` — `n_hard` plus the per-gate counts in `detail` |

Import pattern for all of them — these scripts already do the
`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` dance:

```python
try:
    from diagnostics import diag, ERROR, WARN
except ImportError:
    def diag(*a, **k): pass
    ERROR, WARN = "error", "warn"
```

**A diagnostics import must never break a script.**

---

### STEP 6 — The internal run summary: `scripts/run_diag_summary.py`

New file. Reads `diagnostics.jsonl`, writes `run_diagnostics.json`, prints a
compact block. **No verdict, no banner, no adjectives** (P3).

```python
#!/usr/bin/env python3
"""run_diag_summary.py <jobdir>

Aggregates projection-audit/diagnostics.jsonl into run_diagnostics.json and
prints a short summary to the job log. Internal engineering artifact — not read
by render_report.py, not shown to a customer.

ALWAYS exits 0. This is a reporting step, never a gate.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagnostics import read_all

NOT_EVALUATED_CODES = {"check_not_evaluated"}


def main():
    job_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    recs = read_all(job_dir)

    workload = 0.0
    try:
        import duckdb
        db = os.path.join(job_dir, "projection-audit", "projection.duckdb")
        if os.path.exists(db):
            con = duckdb.connect(db, read_only=True)
            try:
                workload = float(con.execute(
                    "SELECT COALESCE(SUM(aws_amortized_cost),0) "
                    "FROM aws_li_catalog WHERE is_workload"
                ).fetchone()[0] or 0.0)
            finally:
                con.close()
    except Exception as e:
        print("run_diag_summary: could not read workload total (%s)" % e)

    errors = [r for r in recs if r.get("level") == "error"]
    warns = [r for r in recs if r.get("level") == "warn"]
    error_usd = sum(float(r.get("usd") or 0) for r in errors)
    pct = (100.0 * error_usd / workload) if workload else 0.0
    not_evaluated = [r for r in recs if r.get("code") in NOT_EVALUATED_CODES]

    by_code = {}
    for r in errors + warns:
        k = r.get("code", "?")
        e = by_code.setdefault(k, {"level": r.get("level"), "count": 0,
                                   "rows": 0, "usd": 0.0, "sources": []})
        e["count"] += 1
        e["rows"] += int(r.get("rows") or 0)
        e["usd"] = round(e["usd"] + float(r.get("usd") or 0), 2)
        if r.get("source") and r["source"] not in e["sources"]:
            e["sources"].append(r["source"])

    summary = {
        "workload_usd": round(workload, 2),
        "error_usd": round(error_usd, 2),
        "error_pct_of_workload": round(pct, 2),
        "error_count": len(errors),
        "warn_count": len(warns),
        "checks_not_evaluated": len(not_evaluated),
        "by_code": by_code,
    }
    try:
        with open(os.path.join(job_dir, "run_diagnostics.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    except Exception as e:
        print("run_diag_summary: could not write run_diagnostics.json (%s)" % e)

    print("run diagnostics: %d error, %d warn, %d check(s) not evaluated; "
          "$%s of $%s workload affected (%.2f%%)" % (
              len(errors), len(warns), len(not_evaluated),
              format(error_usd, ",.2f"), format(workload, ",.2f"), pct))
    for code, e in sorted(by_code.items(), key=lambda kv: -kv[1]["usd"]):
        print("  %-28s %-5s rows=%-6d usd=%-14s [%s]" % (
            code, e["level"], e["rows"], format(e["usd"], ",.2f"),
            ", ".join(e["sources"])))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("run_diag_summary: failed (%s) — continuing" % e)
        sys.exit(0)
```

Wire it as the **last** `PostLLMScripts` entry of Phase 6 in `orchestrate.go`,
soft. Phase 6 currently has `PreLLMScripts` and `Script` but no
`PostLLMScripts`; add the field and change nothing else in that phase spec:

```go
			PostLLMScripts: []string{"?scripts/run_diag_summary.py $JOBDIR"},
```

---

### STEP 7 — Tests

**7a.** Go — add to `internal/jobs/watcher_test.go` (or a new
`watcher_validation_test.go`, matching the package's existing style):

1. `TestRunValidatorGate_MissingScript` — point `skill.ResolveDir()` at an empty
   temp dir; assert the outcome is `validatorNotRun`, **not** `validatorClean`.
2. `TestWriteValidationVerdict` — call with each outcome; assert
   `validation_verdict.txt` exists and its first line is
   `clean` / `violations` / `not_run`.
3. `TestValidatorOutcomeNeverFailsJob` — assert the watcher call path still
   reaches `UpdateJobDone` for every outcome. (If that is awkward to test
   directly in this package, instead assert by inspection in your summary that
   no `UpdateJobFailed` call was added to the validator path, and say so.)

**7b.** Python — new `tests/test_diagnostics.py`:

4. `test_diag_writes_and_reads` — `diag(...)` then `read_all()` round-trips the
   record with the right level, code, rows and usd.
5. `test_diag_never_raises` — call `diag()` with an unwritable `job_dir` and with
   junk arguments; assert no exception.

**7c.** Python — new `tests/test_run_diag_summary.py`:

6. `test_summary_totals` — write a synthetic `diagnostics.jsonl` with two
   `error` and one `warn` record; assert `run_diagnostics.json` has
   `error_count == 2`, `warn_count == 1` and the correct `error_usd`.
7. `test_summary_always_exits_zero` — run against a job dir with no diagnostics
   file and against a corrupt one; assert exit code 0 both times.

**7d.** Python — new `tests/test_orchestrator_template.py`:

8. `test_ensure_full_coverage_invoked_once` — read `internal/jobs/orchestrate.go`
   as text; assert `"?scripts/ensure_full_coverage.py $DB"` appears **exactly
   once**. This is the regression test that stops the backstop creeping back in
   front of the gate.
9. `test_template_has_no_unescaped_format_verbs` — extract the
   `orchestratorTemplate` raw-string body; assert no `%` is followed by `d`, `s`
   or `(` unless doubled. Catches the `%%` mistake from §3.

Locate the repo root with `pathlib`, not a hardcoded absolute path.

---

## 5. Verification — run all of these, paste the output

```bash
go build ./... && go vet ./internal/... && go test ./internal/jobs/
```

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
```

Nothing that passed before may fail now.

**Prove the orchestrator template still renders.** Highest-risk edit here — a bad
`%` produces Python that only breaks at job runtime. Add a small exported test
shim in the `jobs` package if `renderOrchestrator` is unexported (do not change
its visibility), render the template to a file, then:

```bash
python -m py_compile /tmp/run_all_rendered.py && echo "TEMPLATE COMPILES"
grep -c "ensure_full_coverage" /tmp/run_all_rendered.py   # must print 1
```

**Prove the customer-facing output is untouched.** Diff the generated
`report.html` for an existing job before and after:

```bash
git stash && <regenerate report> && cp report.html /tmp/before.html
git stash pop && <regenerate report> && diff /tmp/before.html report.html && echo "REPORT IDENTICAL"
```

```bash
grep -rn "diagnostics\|run_diagnostics\|validation_verdict" skill/aws-gcp-cost-projection/scripts/render_report.py
```

That grep must return **nothing**.

**Prove dollars did not move.** On any existing job DB, before and after:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute('SELECT round(SUM(aws_amortized_cost),2), round(SUM(gcp_projected_cost),2) FROM gcp_projection').fetchone())" <path-to-projection.duckdb>
```

Both numbers must be identical.

**Prove the channel works end to end.** Run a real job, then:

```bash
cat <jobdir>/run_diagnostics.json
cat <jobdir>/validation_verdict.txt
grep "\[diag\]" <jobdir>/agy.log
```

---

## 6. Do NOT do any of the following

- Do **not** make anything newly fatal. No new `failure.txt` writes, no new
  `UpdateJobFailed` calls, no `BLOCK_ON_*` flag.
- Do **not** change `CRITICAL_PHASES`.
- Do **not** modify `render_report.py` or anything else the customer sees. The
  HTML report must be byte-identical.
- Do **not** add diagnostics to job status, email or Slack notifications.
- Do **not** use verdict labels (`GREEN`/`AMBER`/`RED`), ALL-CAPS banners, or the
  words `unverified`, `untrustworthy`, `weakness`, `broken`, `failure` in any
  log line, file or comment this work order produces. Factual and neutral only.
- Do **not** make `run_diag_summary.py` or `diagnostics.py` exit non-zero, ever.
- Do **not** emit one diagnostic per row in a loop. Accumulate, emit once.
- Do **not** write a second `pythonBin()`. Use the one in `orchestrate.go`.
- Do **not** change what any gate *checks* — only how its result is recorded.
- Do **not** remove, reorder or re-word any other `PreLLMScripts` /
  `PostLLMScripts` entry.
- Do **not** "fix" anything else you notice. List it at the bottom of your
  summary instead.

---

## 7. Done criteria

- [ ] No literal `"python3"` remains in `internal/jobs/watcher.go`; preflight no
      longer warns when only `python` is installed.
- [ ] `runValidatorGate` returns three distinct outcomes; "could not run" is
      never reported as clean.
- [ ] Every finished job writes `validation_verdict.txt` whose first line is
      `clean`, `violations` or `not_run`.
- [ ] The job is still marked `done` in every outcome — no new failure path.
- [ ] `scripts/diagnostics.py` exists; 6 sites emit records; none can raise.
- [ ] A check that cannot be evaluated is logged and recorded as
      `check_not_evaluated`, and the run continues.
- [ ] A failed soft script records `soft_script_failed`.
- [ ] `ensure_full_coverage.py` is invoked from exactly **one** place in
      `orchestrate.go`, after the LLM retry, and records one line.
- [ ] Every job writes `run_diagnostics.json` and prints a compact summary with
      per-code row counts and dollars.
- [ ] `report.html` is byte-identical for an unchanged job; `render_report.py`
      references none of the new artifacts.
- [ ] The rendered orchestrator compiles under `py_compile`.
- [ ] 9 new tests pass; both suites pass with no new failures.
- [ ] AWS and GCP totals on a real job DB are unchanged to the cent.
