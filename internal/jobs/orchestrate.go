package jobs

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
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
				"scripts/prefetch_skus.py $DB",
				"scripts/classify_mechanics.py $DB",
				"scripts/apply_commitment_ignores.py $DB",
				"scripts/apply_static_mappings.py $DB",
			},
			// merge_mappings.py merges all group files (LLM + static) into aws_li_to_gcp_li.
			PostLLMScripts: []string{
				"scripts/merge_mappings.py $DB projection-audit/mappings",
			},
			Prompt: "Phase 2 — LLM mapping only. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator — DO NOT re-run):\n" +
				"  • classify_mechanics.py ran → projection-audit/phase2_manifest.json exists\n" +
				"  • apply_commitment_ignores.py ran → commitment_discount_mappings.json exists\n" +
				"  • apply_static_mappings.py ran → flat_hourly/object_storage/per_request mappings exist\n\n" +
				"YOUR ONLY JOB: map the 3 LLM groups by reading the manifest.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read projection-audit/phase2_manifest.json — this has every row you need with all fields.\n" +
				"   DO NOT query projection.duckdb directly. The manifest is the single source of truth.\n" +
				"2. For each group in [compute_breakdown, managed_db, misc]:\n" +
				"   a. Read that group's rows from the manifest (manifest[group][\"rows\"]).\n" +
				"   b. Map ALL rows to GCP. Use scripts/find-sku.sh only to look up SKU IDs — no inline DuckDB queries.\n" +
				"   c. Build a JSON array of mapping objects (schema: aws_li_key, gcp_service, gcp_sku_id,\n" +
				"      gcp_sku_name, component, strategy, unit_multiplier, gcp_region, projection_note,\n" +
				"      mapping_confidence, is_workload, break_down).\n" +
				"   d. Write the array to projection-audit/mappings/<group>_mappings.json in ONE write_to_file call.\n" +
				"   e. Do NOT write rows one at a time. One file per group, one write per file.\n" +
				"3. Write projection-audit/mapping-notes.md with a brief summary of decisions.\n\n" +
				"RULES:\n" +
				"  • Never-passthrough: EC2/RDS/Aurora/ElastiCache/EBS/DataTransfer/ELB/S3 must be mapped.\n" +
				"  • Total passthrough must stay under 5% of AWS cost.\n" +
				"  • DO NOT run merge_mappings.py — the orchestrator runs it after you finish.\n" +
				"  • DO NOT query projection.duckdb with python3 -c or run_command. Read manifest only.\n" +
				"  • STOP after writing the 3 _mappings.json files and mapping-notes.md. Nothing else.",
			CheckName: "mapping_coverage",
			CheckSQL: "SELECT count(*) FROM aws_li_catalog c WHERE NOT EXISTS " +
				"(SELECT 1 FROM aws_li_to_gcp_li m WHERE m.aws_li_key = c.aws_li_key)",
		},
		{
			Num: 3, Name: "Review", Activity: "Verifying mappings",
			PreScript: "scripts/auto_review.py",
			Prompt: "Phase 3 — Review. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator):\n" +
				"  • auto_review.py ran → review_flags.md lists every detected issue with aws_li_key and reason.\n\n" +
				"YOUR ONLY JOB: fix the rows listed in review_flags.md.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read review_flags.md. This is the ONLY input you need.\n" +
				"   DO NOT re-scan all mappings. DO NOT query aws_li_catalog to find new issues.\n" +
				"2. For each flagged aws_li_key in review_flags.md:\n" +
				"   a. Look up its current mapping: run ONE query — SELECT * FROM aws_li_to_gcp_li WHERE aws_li_key='...'.\n" +
				"   b. Decide the correct fix (wrong SKU, illegal passthrough, wrong strategy).\n" +
				"   c. Apply fix: run ONE UPDATE on aws_li_to_gcp_li for that row.\n" +
				"   d. Move to the next flagged row. Do not re-query the same row twice.\n" +
				"3. After all flags are resolved, append a brief summary to mapping-notes.md.\n\n" +
				"RULES:\n" +
				"  • Phase 3 never re-enters Phase 2. All fixes are direct SQL UPDATEs on aws_li_to_gcp_li.\n" +
				"  • DO NOT run a broad SELECT on all mappings — only SELECT the specific aws_li_key being fixed.\n" +
				"  • DO NOT write new mapping files. DO NOT call merge_mappings.py or classify_mechanics.py.\n" +
				"  • STOP after processing every row in review_flags.md. Nothing else.",
		},
		{
			Num: 4, Name: "Rate-Card Fill", Activity: "Fetching GCP rates",
			Script: "scripts/apply_rates.py",
			CheckName: "cud_coverage_compute",
			CheckSQL: "SELECT count(DISTINCT m.gcp_sku_id) FROM aws_li_to_gcp_li m WHERE " +
				"m.gcp_service IN ('Compute Engine','Cloud SQL','Cloud Memorystore'," +
				"'Cloud Memorystore for Redis','Cloud Memorystore for Memcached') " +
				"AND m.strategy IN ('map','break_down') AND m.component IN ('core','ram') " +
				"AND m.gcp_sku_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r " +
				"WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'Commit3Yr')",
		},
		{
			Num: 5, Name: "Outlier Triage", Activity: "Running outlier queries",
			PreScript: "scripts/detect_outliers.py",
			Prompt: "Phase 5 — Outlier Triage. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator):\n" +
				"  • detect_outliers.py ran → outliers.md lists every flagged row with aws_li_key, query ID, and values.\n\n" +
				"YOUR ONLY JOB: triage each row in outliers.md.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Read outliers.md. This is the ONLY input you need.\n" +
				"   DO NOT re-run the outlier queries yourself. DO NOT SELECT * from gcp_projection.\n" +
				"2. For each flagged aws_li_key in outliers.md:\n" +
				"   a. Check triage order: unit multiplier wrong → wrong SKU → spec mismatch → documented mechanism.\n" +
				"   b. Apply exactly ONE of:\n" +
				"      • UPDATE aws_li_to_gcp_li SET unit_multiplier=... WHERE aws_li_key='...'\n" +
				"      • UPDATE aws_li_to_gcp_li SET gcp_sku_id=..., gcp_sku_name=... WHERE aws_li_key='...'\n" +
				"      • UPDATE aws_li_to_gcp_li SET strategy='passthrough', unit_multiplier=0.3 WHERE aws_li_key='...' (cannot resolve)\n" +
				"   c. The NEW SKU description must literally match the AWS resource type — inter-zone egress → inter-zone SKU.\n" +
				"   d. Never create GCP On-Demand > 3× AWS cost without a visible bill explanation.\n" +
				"3. Append a one-line note per fixed row to mapping-notes.md.\n\n" +
				"RULES:\n" +
				"  • Phase 5 never re-enters Phase 2 or 3. Every fix is a direct SQL UPDATE.\n" +
				"  • DO NOT run broad SELECTs. One targeted SELECT per row if you need to verify current state.\n" +
				"  • STOP when all rows in outliers.md are resolved or documented.",
			CheckName: "over_and_under_projection",
			CheckSQL: "SELECT (SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy IN ('map','break_down') " +
				"AND aws_amortized_cost > 20 AND gcp_projected_cost > aws_amortized_cost * 3) + " +
				"(SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy IN ('map','break_down') " +
				"AND aws_amortized_cost > 1 AND COALESCE(gcp_projected_cost,0) = 0)",
		},
		{
			Num: 6, Name: "Reporting", Activity: "Generating HTML report",
			// render_report.py runs as a post_llm_script so the HTML is written
			// BEFORE the quota check fires. If the LLM narrative hits quota, the
			// report still exists and the watcher marks the job done.
			PostLLMScripts: []string{"scripts/render_report.py"},
			Prompt: "Phase 6 — Narrative only. Strict protocol, no deviations.\n\n" +
				"SETUP (already done by orchestrator):\n" +
				"  • render_report.py will run after you finish → it generates report.html and run_results row.\n\n" +
				"YOUR ONLY JOB: write the executive summary narrative.\n\n" +
				"MANDATORY STEPS — follow exactly in this order:\n" +
				"1. Run exactly these two queries and nothing else:\n" +
				"   SELECT SUM(aws_amortized_cost) FROM aws_li_catalog\n" +
				"   SELECT SUM(gcp_projected_cost), SUM(gcp_cost_1yr_cud), SUM(gcp_cost_3yr_cud) FROM gcp_projection WHERE is_workload\n" +
				"2. Read customer_name.txt for the customer name.\n" +
				"3. Write a 3-paragraph executive summary to projection-audit/summary-<YYYYMMDDTHHMMSSZ>.md:\n" +
				"   Para 1: AWS total, GCP On-Demand, GCP 1yr CUD, GCP 3yr CUD — just the numbers and % deltas.\n" +
				"   Para 2: Top 2–3 cost drivers and their GCP mappings.\n" +
				"   Para 3: One sentence on confidence and any caveats.\n" +
				"4. Insert one row into run_results:\n" +
				"   INSERT INTO run_results (run_id, aws_total, gcp_od, gcp_1yr_cud, gcp_3yr_cud, created_at)\n" +
				"   VALUES ('<run_id>', <aws_total>, <gcp_od>, <gcp_1yr>, <gcp_3yr>, '<timestamp>')\n" +
				"   Only if NOT EXISTS a row with that run_id.\n\n" +
				"RULES:\n" +
				"  • DO NOT generate report.html — the orchestrator's render_report.py does that.\n" +
				"  • DO NOT read gcp_projection row by row. Only the two aggregate SELECTs above.\n" +
				"  • STOP after writing the summary .md and the run_results INSERT. Nothing else.",
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
	if err := os.WriteFile(filepath.Join(jobDir, "run_all.py"), []byte(script), 0644); err != nil {
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
	)
	cmd.Env = env
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	logFile, err := os.OpenFile(filepath.Join(jobDir, "agy.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
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
	return pid, nil
}

// geminiModel resolves the model alias with the same default as baseFlags.
func (s *Spawner) geminiModel() string {
	if s.cfg.AGYModel == "" {
		return "gemini-3.5-flash"
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
import base64, json, os, subprocess, sys, threading, time, re, glob, hashlib, shutil
from collections import defaultdict

JOB_DIR   = os.getcwd()
AGY       = os.environ.get("AGY_BIN", "agy")
MODEL     = os.environ.get("AGY_MODEL", "gemini-3.5-flash")
DUCKDB    = os.environ.get("DUCKDB_BIN", "duckdb")
DB        = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
AGY_LOG   = os.path.join(JOB_DIR, "agy-internal.log")
PHASES    = json.loads(base64.b64decode("%s").decode())
END_PHASE = int(os.environ.get("END_PHASE", "6"))
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

# Quota / unrecoverable-auth markers. When agy hits these, every later call
# 429s too, so marching through the rest of the phases just produces an empty
# report. Detect and stop cleanly (failure.txt) instead — the watcher then
# fails the job WITHOUT the destructive clean-slate retry.
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "Individual quota reached",
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
    """Run a skill script directly (no LLM). script_cmd may contain $DB."""
    cmd = script_cmd.replace("$DB", DB)
    parts = cmd.split()
    script_path = os.path.join(SKILL_DIR, parts[0])
    full = ["python3", script_path] + parts[1:]
    log("[SCRIPT] " + " ".join(full))
    result = subprocess.run(full, cwd=JOB_DIR)
    if result.returncode != 0:
        log("[SCRIPT] WARNING: exited with code %%d: %%s" %% (result.returncode, " ".join(full)))

def run_agy(prompt, phase_label=""):
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
            "--print-timeout", "45m",
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
    convs = []
    if not os.path.exists(log_path):
        return convs
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(start_pos)
            content = f.read()
            matches = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', content, re.IGNORECASE)
            for m in matches:
                m = m.lower()
                if m not in convs:
                    convs.append(m)
    except Exception as e:
        log("Error reading new convs: " + str(e))
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
    except Exception as e:
        log("Error writing CSV files: " + str(e))

phase_runs = []

for ph in PHASES:
    if ph["num"] < START_PHASE or ph["num"] > END_PHASE:
        log("Skipping Phase %%d (%%s)" %% (ph["num"], ph["name"]))
        continue

    write_progress(ph["num"], ph["name"], ph["activity"])
    log("=== Phase %%d (%%s) ===" %% (ph["num"], ph["name"]))
    
    start_pos = 0
    if os.path.exists(AGY_LOG):
        start_pos = os.path.getsize(AGY_LOG)
        
    pre_script = ph.get("pre_script")
    if pre_script:
        log("Running pre_script: " + pre_script)
        subprocess.run(["python3", os.path.join(SKILL_DIR, pre_script)], cwd=JOB_DIR, check=True)

    script = ph.get("script")
    if script:
        log("Running script: " + script)
        subprocess.run(["python3", os.path.join(SKILL_DIR, script)], cwd=JOB_DIR, check=True)
    else:
        # Run deterministic pre-LLM scripts (zero LLM tokens)
        for scmd in ph.get("pre_llm_scripts") or []:
            run_script(scmd)

        # Spawn agy only for the LLM portion of this phase
        run_agy(ph.get("prompt", ""), phase_label=ph.get("name", ""))

        # Run deterministic post-LLM scripts (zero LLM tokens)
        for scmd in ph.get("post_llm_scripts") or []:
            run_script(scmd)

    phase_convs = get_new_convs(AGY_LOG, start_pos)
    phase_entry = {
        "num": ph["num"],
        "name": ph["name"],
        "convs": phase_convs
    }
    phase_runs.append(phase_entry)

    if failed_structurally():
        log("failure.txt present after Phase %%d — structural failure, stopping" %% ph["num"])
        measure_metrics(phase_runs)
        sys.exit(0)
    qm = quota_blocked(start_pos)
    if qm:
        log("quota/auth block detected (%%s) after Phase %%d — stopping, no retry" %% (qm, ph["num"]))
        with open(os.path.join(JOB_DIR, "failure.txt"), "w") as f:
            f.write("Gemini quota/auth limit reached (" + qm + ") during Phase "
                    + str(ph["num"]) + " (" + ph["name"] + "). The model API returned a "
                    "quota error, so the projection could not be completed. Re-run once the "
                    "quota resets or switch to an account with available quota.")
        measure_metrics(phase_runs)
        sys.exit(0)
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

            v2 = gate(sql)
            log("gate %%s after retry: %%s" %% (ph.get("check_name","?"), "PASS" if v2 == 0 else str(v2) + " remaining"))
        else:
            log("gate %%s PASS" %% ph.get("check_name","?"))

    # Record this phase as successfully completed so a retry can resume here.
    write_phase_checkpoint(ph["num"])

try:
    measure_metrics(phase_runs)
except Exception as e:
    log("Failed to measure final metrics: " + str(e))

log("orchestrator done")
`
