package jobs

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"

	"github.com/facets/cur-web/internal/config"
)

// Phase-by-phase orchestration.
//
// Instead of handing agy ONE prompt and letting the skill self-orchestrate all
// six phases inside a single long-lived agent context (which Gemini executes
// unreliably — it improvises, mis-picks SKUs, drops CUD coverage, over-projects),
// we drive the phases from a small Python orchestrator (run_all.py). It invokes
// `agy` once per phase with a tight, single-purpose prompt — a FRESH agy
// conversation each time, so the model only ever holds one phase's worth of
// context. Between phases a deterministic DuckDB gate runs; if it fails, the
// phase is re-run once with the offending rows named in the prompt.
//
// Phase 2 still launches the skill's 4–5 parallel mapping sub-agents — that
// fan-out happens INSIDE the single Phase-2 agy call and is unchanged.
//
// The watcher tracks the orchestrator process as one PID and waits for the
// report (run_all.py is already one of its liveness signals).

type phaseSpec struct {
	Num       int    `json:"num"`
	Name      string `json:"name"`
	Activity  string `json:"activity"`
	Prompt    string `json:"prompt,omitempty"`
	Script    string `json:"script,omitempty"`
	PreScript string `json:"pre_script,omitempty"`
	// PreLLMScripts / PostLLMScripts: deterministic scripts the orchestrator runs
	// BEFORE / AFTER spawning agy for this phase. Each entry is a script path
	// relative to SKILL_DIR; args are space-separated after the path. "$DB" is
	// substituted with the DB path. These scripts run as direct Python subprocesses
	// (no agy, no LLM tokens).
	PreLLMScripts  []string `json:"pre_llm_scripts,omitempty"`
	PostLLMScripts []string `json:"post_llm_scripts,omitempty"`
	// CheckName/CheckSQL: a deterministic gate run AFTER the phase's agy call.
	// CheckSQL must return a single integer that is 0 when healthy (>0 = number
	// of violations). Empty CheckSQL skips the gate.
	CheckName string `json:"check_name,omitempty"`
	CheckSQL  string `json:"check_sql,omitempty"`
}

// phaseSpecs returns the six per-phase prompts + their deterministic gates.
func phaseSpecs(inputExt string) []phaseSpec {
	return []phaseSpec{
		{
			Num: 1, Name: "Ingestion", Activity: "Loading bill into DuckDB",
			Script: "scripts/ingest.py",
			CheckName: "ingestion_nonempty",
			CheckSQL:  "SELECT (count(*)=0)::int FROM aws_li_catalog",
		},
		{
			Num: 2, Name: "Mapping", Activity: "Mapping AWS line items to GCP",
			// Deterministic scripts run by the orchestrator BEFORE agy starts.
			// classify_mechanics.py stamps mechanic_group + writes phase2_manifest.json.
			// apply_commitment_ignores.py + apply_static_mappings.py write their
			// mapping files. agy is spawned ONLY for the three LLM groups.
			PreLLMScripts: []string{
				"scripts/classify_mechanics.py $DB",
				"?scripts/apply_commitment_ignores.py $DB",
				"?scripts/apply_static_mappings.py $DB",
				"?scripts/family_mapper.py $DB",
			},
			// Post-merge deterministic passes (zero LLM tokens):
			//   1. merge_mappings.py  — bulk-INSERT all group files into aws_li_to_gcp_li
			//   2. calibrate_confidence.py — apply service-specific confidence ceilings
			//      (OpenSearch 70%, MSK 70%, RDS 72%, Windows 75%) and add architecture notes
			//   3. reconcile_capacity.py — upsize any break_down rows where GCP vCPU/RAM
			//      < AWS spec to enforce the never-underprovision guarantee
			PostLLMScripts: []string{
				"scripts/merge_mappings.py $DB projection-audit/mappings",
				// Service Classification Engine: force the canonical GCP target per
				// data/service_map.json so mappings are deterministic, not per-row
				// LLM guesses (CloudTrail->Cloud Logging, EMR->Dataproc, etc.).
				"?scripts/service_classifier.py $DB",
				// Generic backstop: reroute any OTHER non-object-storage service the
				// LLM dropped onto Cloud Storage to passthrough + manual-review flag.
				"?scripts/fix_storage_misroute.py $DB",
				"?scripts/calibrate_confidence.py $DB",
				"?scripts/reconcile_capacity.py $DB",
				"?scripts/verify_golden_mappings.py $DB",
			},
			Prompt: "Phase 2 — LLM mapping only. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator — DO NOT re-run):\n" +
				"  • classify_mechanics.py ran → projection-audit/phase2_manifest.json exists\n" +
				"  • apply_commitment_ignores.py ran → commitment_discount_mappings.json exists\n" +
				"  • apply_static_mappings.py ran → flat_hourly/object_storage/per_request mappings exist\n\n" +
				"YOUR ONLY JOB: map the 3 LLM groups by reading the manifest.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read projection-audit/phase2_manifest.json.\n" +
				"   The manifest contains a \"_meta\" key with {\"output_dir\": \"projection-audit/mappings\"}.\n" +
				"   USE THAT output_dir value exactly as-is — it is a relative path from your working directory.\n" +
				"   DO NOT query projection.duckdb directly. The manifest is the single source of truth.\n" +
				"2. For each group in [compute_breakdown, managed_db, misc]:\n" +
				"   a. Read that group's rows from the manifest (manifest[group][\"rows\"]).\n" +
				"   b. Map ALL rows to GCP. Use scripts/find-sku.sh only to look up SKU IDs — no inline DuckDB queries.\n" +
				"   c. Build a JSON array of mapping objects (schema: aws_li_key, gcp_service, gcp_sku_id,\n" +
				"      gcp_sku_name, component, strategy, unit_multiplier, gcp_region, projection_note,\n" +
				"      mapping_confidence, is_workload, break_down).\n" +
				"   d. Write the array to {output_dir}/<group>_mappings.json — use the RELATIVE output_dir from _meta.\n" +
				"      NEVER construct an absolute path. NEVER write outside the current working directory.\n" +
				"   e. One file per group, one write per file.\n" +
				"3. Write projection-audit/mapping-notes.md with a brief summary of decisions.\n\n" +
				"RULES:\n" +
				"  • Never-passthrough: EC2/RDS/Aurora/ElastiCache/EBS/DataTransfer/ELB/S3 must be mapped.\n" +
				"  • Total passthrough must stay under 5% of AWS cost.\n" +
				"  • PASSTHROUGH = ONE ROW ONLY: if a service has no GCP equivalent, emit EXACTLY ONE\n" +
				"    row with strategy='passthrough' and break_down=false. NEVER split a passthrough\n" +
				"    into core+ram or any components — each component independently returns the full\n" +
				"    AWS cost, so N components = N× cost multiplication. This is always a bug.\n" +
				"  • break_down=true is ONLY valid when strategy='map' or 'break_down' with a real\n" +
				"    gcp_sku_id. break_down=true combined with strategy='passthrough' is always wrong.\n" +
				"  • DO NOT run merge_mappings.py — the orchestrator runs it after you finish.\n" +
				"  • DO NOT query projection.duckdb with python3 -c or run_command. Read manifest only.\n" +
				"  • STOP after writing the 3 _mappings.json files and mapping-notes.md. Nothing else.",
			CheckName: "mapping_coverage",
			CheckSQL: "SELECT count(*) FROM aws_li_catalog c WHERE NOT EXISTS " +
				"(SELECT 1 FROM aws_li_to_gcp_li m WHERE m.aws_li_key = c.aws_li_key)",
		},
		{
			Num: 3, Name: "Review", Activity: "Verifying mappings",
			// auto_review.py is a pure suggestion engine: detects illegal passthroughs
			// and spec violations, pre-computes candidate fixes, writes review_flags.md
			// (for LLM) and review_candidates.json (for apply_review_fixes.py).
			// It NEVER modifies the database. Soft (?) so a crash here skips review
			// but still allows the report to be generated.
			PreLLMScripts: []string{"?scripts/auto_review.py"},
			// apply_review_fixes.py reads review_fixes.json from the LLM and
			// review_candidates.json from auto_review.py, then applies confirm/override/veto
			// decisions with schema validation. It is the ONLY script that writes to the DB.
			PostLLMScripts: []string{"?scripts/apply_review_fixes.py"},
			Prompt: "Phase 3 — Review. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator):\n" +
				"  • auto_review.py ran → review_flags.md lists every flagged row with a pre-computed\n" +
				"    candidate fix and a confidence label (HIGH / LOW / NONE).\n\n" +
				"YOUR ONLY JOB: read review_flags.md and write review_fixes.json.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read review_flags.md. This is the ONLY input you need.\n" +
				"   DO NOT query the database. DO NOT re-scan mappings. DO NOT run SQL.\n" +
				"2. For each flagged aws_li_key, decide:\n" +
				"   • HIGH confidence candidate: confirm unless something is visibly wrong.\n" +
				"   • LOW confidence candidate: verify it makes sense; override if it looks wrong.\n" +
				"   • NONE (no candidate): reason from product/usage_type, supply gcp_sku_id + gcp_sku_name.\n" +
				"3. Write review_fixes.json — a JSON array, one object per flagged row:\n" +
				"   [{\"aws_li_key\": \"...\", \"decision\": \"confirm|override|veto\",\n" +
				"     \"gcp_sku_id\": \"...\", \"gcp_sku_name\": \"...\",\n" +
				"     \"unit_multiplier\": 4.0, \"component\": \"core\", \"reason\": \"...\"}]\n" +
				"   confirm  → apply the pre-computed candidate as-is\n" +
				"   override → supply your own values (include gcp_sku_id+gcp_sku_name and/or unit_multiplier)\n" +
				"   veto     → leave unchanged (document why in reason)\n\n" +
				"RULES:\n" +
				"  • DO NOT run any SQL or touch the database — apply_review_fixes.py handles all writes.\n" +
				"  • DO NOT write mapping files or call any other scripts.\n" +
				"  • unit_multiplier means QUANTITY CONVERSION ONLY (vCPU count, RAM GiB, etc.).\n" +
				"    Never set it to aws_rate/gcp_rate to force cost parity — that is always wrong.\n" +
				"    For storage rows unit_multiplier must be 1.0. If GCP costs more, that is correct.\n" +
				"  • STOP after writing review_fixes.json. Nothing else.",
			CheckName: "no_illegal_passthroughs",
			// Only flag services with clear, direct GCP equivalents as illegal passthroughs.
			// Excluded (legitimately passthrough): CloudWatch (pricing model incompatible),
			// Lambda (memory_size_mb missing from CUR), Data Transfer (multi-directional),
			// NAT Gateway (multi-component, maps to Cloud NAT passthrough).
			CheckSQL: "SELECT count(*) FROM aws_li_catalog c " +
				"JOIN aws_li_to_gcp_li m USING(aws_li_key) " +
				"WHERE m.strategy = 'passthrough' " +
				"AND (c.product ILIKE '%Elastic Compute Cloud%' " +
				"OR c.product ILIKE '%Elastic Block Store%' " +
				"OR c.product ILIKE '%Relational Database%' " +
				"OR c.product ILIKE '%Aurora%' " +
				"OR c.product ILIKE '%ElastiCache%' " +
				"OR c.product ILIKE '%Simple Storage%' " +
				"OR c.product ILIKE '%Load Balanc%') " +
				"AND c.product NOT ILIKE '%NatGateway%' " +
				"AND c.product NOT ILIKE '%Nat:%'",
		},
		{
			Num: 4, Name: "Rate-Card Fill", Activity: "Fetching GCP rates",
			// ensure_catalog_coverage.py is soft: if the catalog fetch fails, apply_rates.py
			// falls back to cached resolved_skus.json entries (99% hit rate after warmup).
			// A crash here should not prevent report generation.
			PreLLMScripts: []string{"?scripts/ensure_catalog_coverage.py"},
			Script: "?scripts/apply_rates.py",
			// After rates load, run the deterministic autofixer (regional-SKU
			// repair, CUD synthesis, per-N clamping, illegal-passthrough repair)
			// BEFORE the gate evaluates. Previously this only ran read-only in
			// the watcher, so its repairs never applied in-pipeline (D4).
			PostLLMScripts: []string{"?scripts/validate_fix.py $JOBDIR"},
			CheckName: "no_null_projected_cost",
			CheckSQL: "SELECT count(*) FROM gcp_projection " +
				"WHERE strategy IN ('map','break_down') " +
				"AND gcp_projected_cost IS NULL " +
				"AND aws_amortized_cost > 1",
		},
		{
			Num: 5, Name: "Outlier Triage", Activity: "Running outlier queries",
			// detect_outliers.py: splits output into structural_outliers.md + pricing_outliers.md,
			// writes outliers_data.json, and fails hard if total rows > 20 (systematic mapper bug).
			// auto_triage.py: pure suggestion engine — reads outliers_data.json, computes
			// candidates for structural rows (D/E/G/B/C/H/I), enriches pricing rows (A1/A2/F)
			// with context only (no candidate — word-overlap re-resolution caused Glacier 120x).
			// Writes triage_suggestions.md (for LLM) and triage_candidates.json (for apply script).
			// incremental_rerate.py before LLM: ensures LLM reasons over fresh costs.
			PreLLMScripts: []string{
				"?scripts/ensure_catalog_coverage.py",
				"?scripts/detect_outliers.py",
				"?scripts/auto_triage.py",
				"?scripts/incremental_rerate.py",
			},
			// apply_outlier_fixes.py: single application point — reads outlier_fixes.json
			// (LLM output) + triage_candidates.json, applies confirm/override/veto with
			// schema validation. LLM never touches the DB directly.
			// incremental_rerate.py after LLM: fills rates for new SKU IDs introduced by fixes.
			// outlier_gate.py: hard gate unchanged.
			PostLLMScripts: []string{
				"?scripts/apply_outlier_fixes.py",
				"?scripts/incremental_rerate.py",
				"?scripts/validate_fix.py $JOBDIR",
				"?scripts/outlier_gate.py $DB",
			},
			Prompt: "Phase 5 — Outlier Triage. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator):\n" +
				"  • detect_outliers.py ran → structural_outliers.md + pricing_outliers.md\n" +
				"  • auto_triage.py ran → triage_suggestions.md with pre-computed candidates\n\n" +
				"YOUR ONLY JOB: read triage_suggestions.md and write outlier_fixes.json.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read triage_suggestions.md. This is the ONLY input you need.\n" +
				"   DO NOT query the database. DO NOT run SQL. DO NOT re-run outlier queries.\n" +
				"2. For each aws_li_key in triage_suggestions.md, decide:\n" +
				"   STRUCTURAL rows (D/E/G/B/C/H/I):\n" +
				"   • HIGH confidence candidate: confirm unless something is visibly wrong.\n" +
				"   • LOW confidence candidate: validate carefully; override if it looks wrong.\n" +
				"   • NONE (no candidate): reason from the context provided, supply fix fields.\n" +
				"   PRICING rows (A1/A2/F — no candidate provided):\n" +
				"   • Use the current gcp_sku_name, ratio, and context to reason about the fix.\n" +
				"   • If unit_multiplier is wrong: provide the correct value.\n" +
				"   • If SKU is wrong tier: supply correct gcp_sku_id + gcp_sku_name.\n" +
				"   • If you cannot determine the fix: veto and document as rate gap.\n" +
				"   ABSOLUTE RULE: strategy='passthrough' means GCP has no equivalent service.\n" +
				"   Never use passthrough to resolve a missing rate or NULL projected cost.\n" +
				"   CRITICAL — unit_multiplier rules:\n" +
				"   • unit_multiplier means QUANTITY CONVERSION ONLY (e.g. vCPU count, RAM GiB).\n" +
				"     It is NEVER a cost-adjustment knob. Do NOT set it to aws_rate/gcp_rate to\n" +
				"     force cost parity — that is always wrong.\n" +
				"   • For storage rows (block_storage, EBS, EFS, S3): unit_multiplier MUST be 1.0.\n" +
				"     If GCP storage costs more than AWS, that is a legitimate price difference — veto.\n" +
				"   • For compute rows: unit_multiplier = vCPU count or RAM GiB from the instance spec.\n" +
				"     Never adjust it to match a target cost.\n" +
				"   • If a ratio looks wrong but unit_multiplier is already 1.0 and the SKU is correct,\n" +
				"     the answer is veto (rate gap), not override with a fractional multiplier.\n" +
				"3. Write outlier_fixes.json — a JSON array, one object per row:\n" +
				"   [{\"aws_li_key\": \"...\", \"decision\": \"confirm|override|veto\",\n" +
				"     \"gcp_sku_id\": \"...\", \"gcp_sku_name\": \"...\",\n" +
				"     \"unit_multiplier\": 4.0, \"component\": \"core\",\n" +
				"     \"gcp_service\": \"...\", \"gcp_region\": \"...\", \"reason\": \"...\"}]\n" +
				"   confirm  → apply the pre-computed candidate as-is\n" +
				"   override → supply your own values (include every field you want changed)\n" +
				"   veto     → leave unchanged, reason goes to rate-gaps section of mapping-notes.md\n\n" +
				"RULES:\n" +
				"  • DO NOT run any SQL or touch the database — apply_outlier_fixes.py handles all writes.\n" +
				"  • DO NOT write to mapping-notes.md — vetoed rows are logged by apply_outlier_fixes.py.\n" +
				"  • STOP after writing outlier_fixes.json. Nothing else.",
			CheckName: "over_and_under_projection",
			// Under-projection zero-check uses a $10 materiality floor: a small
			// row can legitimately project to $0 on GCP (usage within a free
			// tier, e.g. Cloud Monitoring's first 150 MiB), so only a MATERIAL
			// mapped row at exactly $0 signals a real mapping/rate failure.
			CheckSQL: "SELECT (SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy IN ('map','break_down') " +
				"AND aws_amortized_cost > 20 AND gcp_projected_cost > aws_amortized_cost * 3) + " +
				"(SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy IN ('map','break_down') " +
				"AND aws_amortized_cost > 10 AND gcp_projected_cost IS NOT NULL AND gcp_projected_cost = 0)",
		},
		{
			Num: 6, Name: "Reporting", Activity: "Generating HTML report",
			Script: "scripts/render_report.py",
		},
	}
}

// renderOrchestrator returns the run_all.py source with the phase specs baked
// in (base64-encoded JSON to dodge all shell/quoting hazards).
func renderOrchestrator(inputExt string) (string, error) {
	specs, err := json.Marshal(phaseSpecs(inputExt))
	if err != nil {
		return "", err
	}
	enc := base64.StdEncoding.EncodeToString(specs)
	return fmt.Sprintf(orchestratorTemplate, enc), nil
}

// StartOrchestrated writes run_all.py into jobDir and spawns it detached. The
// orchestrator drives the six phases; the watcher polls the returned PID and
// finalizes when the report appears (same contract as the old single-agy Start).
func (s *Spawner) StartOrchestrated(jobDir, inputExt string) (int, error) {
	script, err := renderOrchestrator(inputExt)
	if err != nil {
		return 0, fmt.Errorf("render orchestrator: %w", err)
	}
	if err := os.WriteFile(filepath.Join(filepath.Clean(jobDir), "run_all.py"), []byte(script), 0644); err != nil {
		return 0, fmt.Errorf("write run_all.py: %w", err)
	}

	cmd := exec.Command("python3", "run_all.py")
	cmd.Dir = jobDir
	// The orchestrator reads the agy binary, model, duckdb binary, and skill dir
	// from the env. DUCKDB_BIN is resolved to an absolute path so the gates never
	// silently skip just because the detached process has a thin PATH.
	env := append(s.skillEnv(),
		"AGY_BIN="+s.agyBin(),
		"AGY_MODEL="+s.geminiModel(),
		"DUCKDB_BIN="+duckdbBin(),
		"PRINT_TIMEOUT="+config.AGYPrintTimeout(),
		"TOTAL_PHASES="+strconv.Itoa(config.TotalPhases),
	)
	cmd.Env = env
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	logFile, err := os.OpenFile(filepath.Join(filepath.Clean(jobDir), "agy.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return 0, fmt.Errorf("open log: %w", err)
	}
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return 0, fmt.Errorf("start orchestrator: %w", err)
	}
	pid := cmd.Process.Pid
	cmd.Process.Release()
	logFile.Close() // child inherited the fd; close parent's copy to avoid leak
	return pid, nil
}

// geminiModel resolves the model alias with the same default as baseFlags.
func (s *Spawner) geminiModel() string {
	if s.cfg.AGYModel == "" {
		return config.DefaultAGYModel
	}
	return s.cfg.AGYModel
}

// duckdbBin resolves an absolute path to the duckdb CLI so the orchestrator's
// gates work even when the detached process inherits a thin PATH. Falls back to
// the common ~/.local/bin install location, then to the bare name.
func duckdbBin() string {
	if p, err := exec.LookPath("duckdb"); err == nil {
		return p
	}
	if home, err := os.UserHomeDir(); err == nil {
		p := filepath.Join(home, ".local", "bin", "duckdb")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "duckdb"
}

// orchestratorTemplate is the run_all.py source. %s is the base64 JSON phase
// list. It runs agy once per phase (fresh conversation), writes progress.json
// and a heartbeat so the watcher sees liveness, runs each phase's deterministic
// DuckDB gate, and retries a phase once (with offending rows named) if its gate
// fails. agy stdout/stderr append to agy.log; agy internals go to agy-internal.log.
const orchestratorTemplate = `#!/usr/bin/env python3
import base64, json, os, subprocess, sys, threading, time, re, glob, hashlib, shutil, traceback
from collections import defaultdict

JOB_DIR   = os.getcwd()
JOB_ID    = os.path.basename(JOB_DIR)
AGY       = os.environ.get("AGY_BIN", "agy")
MODEL     = os.environ.get("AGY_MODEL", "gemini-3.5-flash")
DUCKDB    = os.environ.get("DUCKDB_BIN", "duckdb")
# PRINT_TIMEOUT and TOTAL_PHASES are injected by the Go spawner from the
# single-source config constants; the literals here are dead fallbacks.
PRINT_TIMEOUT = os.environ.get("PRINT_TIMEOUT", "45m")
TOTAL_PHASES  = int(os.environ.get("TOTAL_PHASES", "6"))
DB        = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
AGY_LOG   = os.path.join(JOB_DIR, "agy-internal.log")
PHASES    = json.loads(base64.b64decode("%s").decode())
END_PHASE = int(os.environ.get("END_PHASE", str(TOTAL_PHASES)))
SKILL_DIR = os.environ.get("SKILL_DIR", "")

# Resume from checkpoint if a prior partial run wrote one; fall back to env/default.
_ckpt_path = os.path.join(JOB_DIR, "phase_checkpoint.json")
try:
    _ckpt = json.load(open(_ckpt_path))
    START_PHASE = int(_ckpt.get("last_completed", 0)) + 1
    print("[orchestrator] Resuming from checkpoint: last_completed=%%d, starting at phase %%d" %%
          (_ckpt.get("last_completed", 0), START_PHASE), flush=True)
except Exception:
    START_PHASE = int(os.environ.get("START_PHASE", "1"))

# Permanent quota / unrecoverable-auth markers. RESOURCE_EXHAUSTED is
# intentionally excluded — it also fires on transient RPM/TPM rate limits
# (spiky phases like Phase 2 with multiple sub-agents). Treating a transient
# rate limit as permanent quota would write failure.txt and skip retry.
# Only markers that unambiguously mean "every future call will also fail" are
# listed here: individual daily-quota exhaustion, auth failures, and model
# unavailability. A transient RESOURCE_EXHAUSTED falls through to the normal
# phase retry path, giving the job another chance once the rate window resets.
QUOTA_MARKERS = ("Individual quota reached", "quota exhausted",
                 "model unreachable", "PERMISSION_DENIED", "UNAUTHENTICATED")

def quota_blocked(start_pos=0):
    # Only scan bytes written after start_pos so retries never see old errors.
    try:
        with open(AGY_LOG, "rb") as f:
            f.seek(start_pos)
            tail = f.read().decode("utf-8", "ignore")
    except Exception:
        return None
    for m in QUOTA_MARKERS:
        if m in tail:
            return m
    return None

def log(msg):
    with open(os.path.join(JOB_DIR, "agy.log"), "a") as f:
        f.write("[orchestrator] " + msg + "\n")
        f.flush()

def write_progress(num, name, activity):
    with open(os.path.join(JOB_DIR, "progress.json"), "w") as f:
        json.dump({"phase": num, "phase_name": name, "last_activity": activity}, f)

def write_phase_checkpoint(num):
    with open(os.path.join(JOB_DIR, "phase_checkpoint.json"), "w") as f:
        json.dump({"last_completed": num, "ts": time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S")}, f)

def run_script(script_cmd):
    """Run a skill script directly (no LLM). script_cmd may contain $DB / $JOBDIR.

    A leading '?' marks the script as SOFT: a non-zero exit is logged but does
    NOT fail the job. Use it for repair/validator passes (e.g. validate_fix.py
    autofix) whose success is judged by the phase gate that follows, not by
    their own exit code — a remaining-violations exit(1) there is expected.
    """
    soft = script_cmd.startswith("?")
    if soft:
        script_cmd = script_cmd[1:]
    cmd = script_cmd.replace("$DB", DB).replace("$JOBDIR", JOB_DIR)
    parts = cmd.split()
    script_path = os.path.join(SKILL_DIR, parts[0])
    full = ["python3", script_path] + parts[1:]
    log("[SCRIPT] " + ("(soft) " if soft else "") + " ".join(full))
    result = subprocess.run(full, cwd=JOB_DIR)
    if result.returncode != 0:
        if soft:
            log("[SCRIPT] soft script exited %%d (non-fatal): %%s" %% (result.returncode, " ".join(full)))
            return
        msg = "Script failed (exit %%d): %%s" %% (result.returncode, " ".join(full))
        log("[SCRIPT] FATAL: " + msg)
        with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
            _f.write(msg)
        sys.exit(1)

def run_agy(prompt, phase_label=""):
    # agy resolves the agent's RELATIVE file writes (e.g. the mapping agent
    # writing "projection-audit/mappings/<group>_mappings.json") into ITS OWN
    # scratch dir, not this job's cwd. So we point scratch/projection-audit and
    # scratch/scripts at THIS job's real directories — that is what makes the
    # agent's writes land where merge_mappings.py and the other scripts read
    # them. The links MUST sit at the flat scratch/<name> path because that is
    # exactly where agy writes; a per-job scratch/<JOB_ID>/<name> subdir is
    # never consulted by agy and leaves the agent's files orphaned.
    #
    # Shared (not per-job) path: run only one job's agy phases at a time. The
    # links are refreshed at the start of every run_agy call, so a sequential
    # retry always repoints them at the current job before agy runs.
    base_scratch = os.path.expanduser("~/.gemini/antigravity-cli/scratch")
    if os.path.exists(base_scratch):
        for name, target in [("projection-audit", os.path.join(JOB_DIR, "projection-audit")),
                              ("scripts", os.path.join(JOB_DIR, "scripts"))]:
            link_path = os.path.join(base_scratch, name)
            try:
                # Clear whatever is there first: a stale symlink from a previous
                # job, or a REAL dir agy created on a run where the link was
                # missing (its contents are orphaned copies of this job's files).
                if os.path.islink(link_path):
                    os.unlink(link_path)
                elif os.path.isdir(link_path):
                    shutil.rmtree(link_path)
                elif os.path.exists(link_path):
                    os.unlink(link_path)
                os.symlink(target, link_path)
            except Exception as e:
                log(f"Failed to point scratch/{name} at job dir: {e}")

    t0 = time.time()
    ts_start = time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S")
    log("[LLM:START] phase=%%s ts=%%s" %% (phase_label, ts_start))
    # Heartbeat keeps agy.log mtime fresh while the model is "thinking" (quiet
    # on stdout) so the watcher's stale-timeout never kills a working phase.
    stop = threading.Event()
    def beat():
        while not stop.wait(60):
            log("heartbeat")
    hb = threading.Thread(target=beat, daemon=True); hb.start()
    args = [AGY, "--dangerously-skip-permissions", "--model", MODEL,
            "--print-timeout", PRINT_TIMEOUT,
            "--log-file", os.path.join(JOB_DIR, "agy-internal.log"),
            "--add-dir", JOB_DIR, "-p", prompt]
    try:
        with open(os.path.join(JOB_DIR, "agy.log"), "a") as lf:
            rc = subprocess.run(args, cwd=JOB_DIR, stdout=lf, stderr=lf).returncode
    finally:
        stop.set()
    elapsed = int(time.time() - t0)
    log("[LLM:END] phase=%%s elapsed=%%ds exit_code=%%d ts=%%s" %% (
        phase_label, elapsed, rc, time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S")))
    return rc

def gate(sql):
    # Returns int violations, or None if the gate could not be evaluated.
    try:
        out = subprocess.check_output([DUCKDB, DB, "-noheader", "-list", sql],
                                      text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None
    try:
        return int(out.splitlines()[0])
    except (ValueError, IndexError):
        return None

def failed_structurally():
    p = os.path.join(JOB_DIR, "failure.txt")
    return os.path.exists(p) and os.path.getsize(p) > 0

def get_new_convs(log_path, start_pos):
    # Conversation UUIDs appear in agy-internal.log (AGY's internal trace),
    # not in agy.log (which only carries skill output). Scan internal log
    # from start_pos; fall back to agy.log if internal log is absent.
    convs = []
    internal_log = os.path.join(JOB_DIR, "agy-internal.log")
    sources = [internal_log, log_path]
    job_id = JOB_DIR.split("/")[-1].lower()
    for src in sources:
        if not os.path.exists(src):
            continue
        try:
            with open(src, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(start_pos if src == log_path else 0)
                content = f.read()
                matches = re.findall(
                    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                    content, re.IGNORECASE)
                for m in matches:
                    m = m.lower()
                    if m != job_id and m not in convs:
                        brain_path = os.path.join(
                            os.path.expanduser("~/.gemini/antigravity-cli/brain"),
                            m, ".system_generated", "logs", "transcript_full.jsonl")
                        if os.path.exists(brain_path):
                            convs.append(m)
        except Exception as e:
            log("Error reading new convs from " + src + ": " + str(e))
        if convs:
            break
    return convs

def measure_metrics(runs):
    import csv
    log("Starting metrics aggregation for " + str(len(runs)) + " phases...")
    
    # 1. Read customer name
    customer_name = "unknown"
    cust_txt = os.path.join(JOB_DIR, "customer_name.txt")
    if os.path.exists(cust_txt):
        try:
            with open(cust_txt, "r") as cf:
                customer_name = cf.read().strip().replace(" ", "_").replace("/", "_")
        except Exception:
            pass
            
    brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
    
    # Fallback to time-based if runs are empty
    if not runs:
        job_start_time = os.path.getctime(JOB_DIR) if os.path.exists(JOB_DIR) else time.time() - 7200
        all_convs = set()
        if os.path.exists(brain_dir):
            transcripts = glob.glob(os.path.join(brain_dir, "*/.system_generated/logs/transcript_full.jsonl"))
            for path in transcripts:
                if os.path.getmtime(path) >= job_start_time - 120:
                    all_convs.add(path.split("/")[-4].lower())
        runs = [{"num": 0, "name": "General/Unknown", "convs": list(all_convs)}]
        
    csv_rows_tokens = []
    csv_rows_tools = []
    
    def extract_tool_info(step):
        tool_calls = step.get("tool_calls", []) or []
        if not tool_calls:
            return None
        tc = tool_calls[0]
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        details = ""
        if name == "view_file":
            details = args.get("AbsolutePath", "")
        elif name == "run_command":
            details = args.get("CommandLine", "")
        elif name == "grep_search":
            details = "Query='{}' Path='{}'".format(args.get("Query", ""), args.get("SearchPath", ""))
        elif name == "list_dir":
            details = args.get("DirectoryPath", "")
        else:
            details = json.dumps(args)
        return name, details

    def get_step_details(step):
        step_type = step.get("type", "UNKNOWN")
        tool_calls = step.get("tool_calls", []) or []
        if tool_calls:
            tc = tool_calls[0]
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            if name == "view_file":
                return "view_file: {}".format(args.get("AbsolutePath", ""))
            elif name == "run_command":
                return "run_command: {}".format(args.get("CommandLine", ""))
            elif name == "grep_search":
                return "grep_search: Query='{}' Path='{}'".format(args.get("Query", ""), args.get("SearchPath", ""))
            elif name == "list_dir":
                return "list_dir: {}".format(args.get("DirectoryPath", ""))
            return "tool_call: {}".format(name)
            
        content = step.get("content", "") or ""
        thinking = step.get("thinking", "") or ""
        if step_type == "USER_INPUT":
            req = content.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
            req_line = req.split("\n")[0] if req else ""
            return "user_request: {}".format(req_line[:120])
        elif step_type == "PLANNER_RESPONSE" and thinking:
            think_line = thinking.replace("\n", " ").strip()
            return "model_thinking: {}...".format(think_line[:120])
        clean_content = content.replace("\n", " ").strip()
        return clean_content[:120]

    for run in runs:
        phase_num = run["num"]
        phase_name = run["name"]
        
        for cid in run["convs"]:
            path = os.path.join(brain_dir, cid, ".system_generated/logs/transcript_full.jsonl")
            if not os.path.exists(path):
                continue
                
            conv_tool_counts = defaultdict(int)
            step_idx = 0
            
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            step = json.loads(line)
                        except Exception:
                            continue
                        
                        step_idx += 1
                        content = step.get("content", "") or ""
                        thinking = step.get("thinking", "") or ""
                        chars = len(content) + len(thinking)
                        
                        tool_calls = step.get("tool_calls", []) or []
                        if tool_calls:
                            chars += len(json.dumps(tool_calls))
                            
                        step_type = step.get("type", "UNKNOWN")
                        details = get_step_details(step)
                        
                        csv_rows_tokens.append({
                            "job_id": JOB_DIR.split("/")[-1],
                            "phase_number": phase_num,
                            "phase_name": phase_name,
                            "conversation_id": cid,
                            "step_index": step.get("step_index", step_idx - 1),
                            "step_type": step_type,
                            "details": details,
                            "characters": chars,
                            "estimated_tokens": chars // 4
                        })
                        
                        tool_info = extract_tool_info(step)
                        if tool_info:
                            name, details = tool_info
                            conv_tool_counts[(name, details)] += 1
            except Exception as e:
                log("Error reading transcript: " + str(e))
                continue
                
            for (name, details), count in conv_tool_counts.items():
                csv_rows_tools.append({
                    "job_id": JOB_DIR.split("/")[-1],
                    "phase_number": phase_num,
                    "phase_name": phase_name,
                    "conversation_id": cid,
                    "tool_name": name,
                    "details": details,
                    "call_count": count
                })

    try:
        # Write Token CSV
        token_csv = os.path.join(JOB_DIR, "token_usage_breakdown_{}.csv".format(customer_name))
        fields_tokens = ["job_id", "phase_number", "phase_name", "conversation_id", "step_index", "step_type", "details", "characters", "estimated_tokens"]
        with open(token_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields_tokens)
            writer.writeheader()
            writer.writerows(csv_rows_tokens)
            
        # Write Tool Call CSV
        tool_csv = os.path.join(JOB_DIR, "tool_calls_frequency_{}.csv".format(customer_name))
        fields_tools = ["job_id", "phase_number", "phase_name", "conversation_id", "tool_name", "details", "call_count"]
        with open(tool_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields_tools)
            writer.writeheader()
            writer.writerows(csv_rows_tools)
            
        log("Saved token usage breakdown to: " + token_csv)
        log("Saved tool calls frequency to: " + tool_csv)

        # Phase-level summary: wall-clock timing + aggregated token estimates per phase
        phase_summary_csv = os.path.join(JOB_DIR, "phase_metrics_summary_{}.csv".format(customer_name))
        phase_token_totals = {}
        for row in csv_rows_tokens:
            pn = row["phase_number"]
            if pn not in phase_token_totals:
                phase_token_totals[pn] = {"phase_name": row["phase_name"], "chars": 0, "tokens": 0}
            phase_token_totals[pn]["chars"] += row["characters"]
            phase_token_totals[pn]["tokens"] += row["estimated_tokens"]
        fields_summary = ["phase_num", "phase_name", "start_time", "end_time", "duration_seconds",
                          "total_chars", "total_tokens_est"]
        summary_rows = []
        seen_phases = set()
        for run in runs:
            pn = run["num"]
            seen_phases.add(pn)
            agg = phase_token_totals.get(pn, {"chars": 0, "tokens": 0})
            summary_rows.append({
                "phase_num": pn,
                "phase_name": run["name"],
                "start_time": run.get("start_time", ""),
                "end_time": run.get("end_time", ""),
                "duration_seconds": run.get("duration_seconds", ""),
                "total_chars": agg["chars"],
                "total_tokens_est": agg["tokens"],
            })
        for pn, agg in sorted(phase_token_totals.items()):
            if pn not in seen_phases:
                summary_rows.append({
                    "phase_num": pn, "phase_name": agg["phase_name"],
                    "start_time": "", "end_time": "", "duration_seconds": "",
                    "total_chars": agg["chars"], "total_tokens_est": agg["tokens"],
                })
        summary_rows.sort(key=lambda r: r["phase_num"])
        with open(phase_summary_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields_summary)
            writer.writeheader()
            writer.writerows(summary_rows)
        log("Saved phase metrics summary to: " + phase_summary_csv)
    except Exception as e:
        log("Error writing CSV files: " + str(e))

phase_runs = []

# CRITICAL_PHASES are load-bearing: without ingested + mapped data a report is
# meaningless, so an unexpected crash there is fatal. Every later phase only
# refines the projection, so a crash there is logged and skipped — the report
# is still generated from whatever is in the DB (affected rows fall back to
# their passthrough / best-effort values). See the driver loop below.
CRITICAL_PHASES = {1, 2}

def run_phase(ph):
    if ph["num"] < START_PHASE or ph["num"] > END_PHASE:
        log("Skipping Phase %%d (%%s)" %% (ph["num"], ph["name"]))
        return

    write_progress(ph["num"], ph["name"], ph["activity"])
    log("=== Phase %%d (%%s) ===" %% (ph["num"], ph["name"]))
    _phase_start_t = time.time()
    _phase_start_ts = time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S")

    start_pos = 0
    if os.path.exists(AGY_LOG):
        start_pos = os.path.getsize(AGY_LOG)
        
    pre_script = ph.get("pre_script")
    if pre_script:
        log("Running pre_script: " + pre_script)
        try:
            subprocess.run(["python3", os.path.join(SKILL_DIR, pre_script)], cwd=JOB_DIR, check=True)
        except subprocess.CalledProcessError as e:
            msg = ("pre_script " + pre_script + " exited with code " + str(e.returncode)
                   + " during Phase " + str(ph["num"]) + " (" + ph["name"] + "). Check agy.log for details.")
            log("ERROR: " + msg)
            if ph["num"] in CRITICAL_PHASES:
                with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
                    _f.write(msg)
                measure_metrics(phase_runs)
                sys.exit(0)
            else:
                log("WARNING: pre_script failed on non-critical Phase %%d — continuing to report" %% ph["num"])
                return

    # Deterministic pre-LLM scripts always run (zero LLM tokens).
    for scmd in ph.get("pre_llm_scripts") or []:
        run_script(scmd)

    script = ph.get("script")
    if script:
        # Deterministic-only phase: run the single script in place of agy.
        # A leading '?' marks the script as soft — failure is logged but the
        # pipeline continues so the report is always generated.
        soft_script = script.startswith("?")
        script_path = script[1:] if soft_script else script
        log("Running script: " + ("(soft) " if soft_script else "") + script_path)
        try:
            subprocess.run(["python3", os.path.join(SKILL_DIR, script_path)], cwd=JOB_DIR, check=True)
        except subprocess.CalledProcessError as e:
            msg = ("script " + script_path + " exited with code " + str(e.returncode)
                   + " during Phase " + str(ph["num"]) + " (" + ph["name"] + "). Check agy.log for details.")
            log("ERROR: " + msg)
            if soft_script:
                log("soft script failure — continuing to next phase")
            else:
                with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
                    _f.write(msg)
                measure_metrics(phase_runs)
                sys.exit(0)
    else:
        # Spawn agy only for the LLM portion of this phase.
        run_agy(ph.get("prompt", ""), phase_label=ph.get("name", ""))

    # If the LLM phase hit a quota/auth wall it produced NOTHING usable — stop
    # cleanly with the real reason BEFORE post-LLM scripts run. Otherwise a
    # post-script like merge_mappings.py fails with a misleading "missing mapping
    # file" error that masks the true cause (quota exhausted).
    qm_early = quota_blocked(start_pos)
    if qm_early:
        log("quota/auth block (%%s) during Phase %%d — stopping before post-LLM scripts" %% (qm_early, ph["num"]))
        phase_runs.append({"num": ph["num"], "name": ph["name"],
                           "convs": get_new_convs(AGY_LOG, start_pos),
                           "start_time": _phase_start_ts,
                           "end_time": time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S"),
                           "duration_seconds": int(time.time() - _phase_start_t)})
        if ph["num"] in CRITICAL_PHASES:
            with open(os.path.join(JOB_DIR, "failure.txt"), "w") as f:
                f.write("Gemini quota/auth limit reached (" + qm_early + ") during Phase "
                        + str(ph["num"]) + " (" + ph["name"] + "). The model API returned a "
                        "quota error, so the projection could not be completed. Re-run once the "
                        "quota resets or switch to an account with available quota.")
            measure_metrics(phase_runs)
            sys.exit(0)
        else:
            log("WARNING: quota/auth block on non-critical Phase %%d — skipping phase, report will still be generated" %% ph["num"])
            return

    # Deterministic post-LLM scripts always run (zero LLM tokens). For a script
    # phase these are the recompute/repair steps that must follow it (D4/O3):
    # rates are refilled and the autofixer repairs any deterministic violation
    # BEFORE the phase gate evaluates.
    for scmd in ph.get("post_llm_scripts") or []:
        run_script(scmd)

    phase_convs = get_new_convs(AGY_LOG, start_pos)
    phase_entry = {
        "num": ph["num"],
        "name": ph["name"],
        "convs": phase_convs,
        "start_time": _phase_start_ts,
        "end_time": time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S"),
        "duration_seconds": int(time.time() - _phase_start_t)
    }
    phase_runs.append(phase_entry)

    if failed_structurally():
        log("failure.txt present after Phase %%d — structural failure, stopping" %% ph["num"])
        measure_metrics(phase_runs)
        sys.exit(0)
    qm = quota_blocked(start_pos)
    if qm:
        log("quota/auth block detected (%%s) after Phase %%d — stopping, no retry" %% (qm, ph["num"]))
        if ph["num"] in CRITICAL_PHASES:
            with open(os.path.join(JOB_DIR, "failure.txt"), "w") as f:
                f.write("Gemini quota/auth limit reached (" + qm + ") during Phase "
                        + str(ph["num"]) + " (" + ph["name"] + "). The model API returned a "
                        "quota error, so the projection could not be completed. Re-run once the "
                        "quota resets or switch to an account with available quota.")
            measure_metrics(phase_runs)
            sys.exit(0)
        else:
            log("WARNING: quota/auth block on non-critical Phase %%d — continuing to report" %% ph["num"])
            return
    sql = ph.get("check_sql")
    if sql:
        v = gate(sql)
        if v is None:
            log("gate %%s: could not evaluate (skipping)" %% ph.get("check_name", "?"))
        elif v > 0:
            log("gate %%s FAILED: %%d violation(s) — re-running phase once" %% (ph.get("check_name","?"), v))

            start_pos_retry = 0
            if os.path.exists(AGY_LOG):
                start_pos_retry = os.path.getsize(AGY_LOG)

            run_agy(ph.get("prompt", "") + " IMPORTANT: a deterministic validation gate ('"
                    + ph.get("check_name","") + "') still reports " + str(v)
                    + " violation(s). Find and fix exactly those rows in projection-audit/projection.duckdb. DO NOT DELETE, DROP, OR OVERWRITE any existing data or tables. Only address the missing/violating rows; do not stop until the gate passes.",
                    phase_label=ph.get("name","") + "-retry")

            retry_convs = get_new_convs(AGY_LOG, start_pos_retry)
            phase_entry["convs"].extend(retry_convs)

            # Check quota BEFORE post-LLM scripts: if the retry LLM call hit quota,
            # post-scripts would run on stale/unchanged data and the gate would fail
            # with a misleading error instead of surfacing the real cause.
            qm_retry = quota_blocked(start_pos_retry)
            if qm_retry:
                log("quota/auth block (" + qm_retry + ") during retry of Phase " + str(ph["num"]) + " — stopping")
                if ph["num"] in CRITICAL_PHASES:
                    with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
                        _f.write("Gemini quota/auth limit reached (" + qm_retry + ") during retry of Phase "
                                 + str(ph["num"]) + " (" + ph["name"] + "). The model API returned a "
                                 "quota error, so the projection could not be completed. Re-run once the "
                                 "quota resets or switch to an account with available quota.")
                    measure_metrics(phase_runs)
                    sys.exit(0)
                else:
                    log("WARNING: quota/auth block on non-critical Phase %%d retry — continuing to report" %% ph["num"])
                    return

            # Re-run post-LLM recompute/repair before re-checking, so the
            # retry's edits are reflected in the projection (mirrors the normal
            # post-LLM step).
            for scmd in ph.get("post_llm_scripts") or []:
                run_script(scmd)

            v2 = gate(sql)
            log("gate %%s after retry: %%s" %% (ph.get("check_name","?"), "PASS" if v2 == 0 else str(v2) + " remaining"))
            if v2 and v2 > 0:
                msg = ("Phase %%d (%%s) gate '%%s' still reports %%d violation(s) after retry "
                       "and deterministic repair. This is a data/coverage gap the pipeline "
                       "cannot auto-resolve — inspect projection.duckdb for the offending rows."
                       %% (ph["num"], ph["name"], ph.get("check_name","?"), v2))
                if ph["num"] in CRITICAL_PHASES:
                    # Critical phases (ingestion, mapping) — report cannot be
                    # generated without correct data. Stop the pipeline.
                    log("gate FATAL: " + msg)
                    with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
                        _f.write(msg)
                    measure_metrics(phase_runs)
                    sys.exit(0)
                else:
                    # Non-critical phases (review, outlier triage) — log the
                    # violation count and continue to generate the report.
                    log("gate WARNING (non-critical phase, continuing): " + msg)
        else:
            log("gate %%s PASS" %% ph.get("check_name","?"))

    # Record this phase as successfully completed so a retry can resume here.
    write_phase_checkpoint(ph["num"])


for ph in PHASES:
    try:
        run_phase(ph)
    except SystemExit:
        # Intentional graceful stop (quota exhausted, unresolved gate, structural
        # failure). Preserve it — these are the pipeline's quality gates.
        raise
    except Exception as e:
        # Any OTHER exception is an unexpected bug in this phase. Never let it
        # abort the whole run with a raw traceback and no report.
        tb = traceback.format_exc()
        if ph["num"] in CRITICAL_PHASES:
            msg = ("Phase %%d (%%s) crashed: %%s. This phase is required for a "
                   "meaningful projection, so the run cannot continue." %% (ph["num"], ph["name"], e))
            log("FATAL: " + msg)
            log(tb)
            with open(os.path.join(JOB_DIR, "failure.txt"), "w") as _f:
                _f.write(msg)
            measure_metrics(phase_runs)
            sys.exit(0)
        log("WARNING: Phase %%d (%%s) crashed but is non-critical: %%s — skipping it so "
            "the report is still generated." %% (ph["num"], ph["name"], e))
        log(tb)

try:
    measure_metrics(phase_runs)
except Exception as e:
    log("Failed to measure final metrics: " + str(e))

log("orchestrator done")
`
