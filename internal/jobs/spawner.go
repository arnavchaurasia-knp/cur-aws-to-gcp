package jobs

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

type SpawnerConfig struct {
	GeminiPath  string
	GeminiModel string // `gemini --model` alias; defaults to "pro" if empty.
	// GEMINI_API_KEY is intentionally not part of SpawnerConfig. The spawned
	// gemini process inherits it from os.Environ(); set it on the host
	// (VM-wide on prod, shell on dev). No duplication, no per-process plumbing.
}

type SpawnResult struct {
	AWSSpend float64
	Err      error
}

type Spawner struct{ cfg SpawnerConfig }

func NewSpawner(cfg SpawnerConfig) *Spawner { return &Spawner{cfg: cfg} }

func (s *Spawner) Run(jobDir, inputExt string) SpawnResult {
	return s.RunWithSession(jobDir, inputExt, "")
}

// RunWithSession runs gemini synchronously, starting the session with the
// given id (via --session-id) so it matches the job's DB session_id and can be
// resumed later for refinement. Empty sessionID lets gemini assign its own.
// Synchronous path is test-only; production uses Start + the watcher.
func (s *Spawner) RunWithSession(jobDir, inputExt, sessionID string) SpawnResult {
	prompt := buildProjectionPrompt(inputExt)
	geminiBin := s.geminiBin()
	args := append(s.baseFlags(), sessionFlag(sessionID)...)
	args = append(args, "-p", prompt)
	cmd := exec.Command(geminiBin, args...)
	cmd.Dir = jobDir
	cmd.Env = s.skillEnv()

	logPath := filepath.Join(jobDir, "gemini.log")
	logFile, err := os.Create(logPath)
	if err != nil {
		return SpawnResult{Err: fmt.Errorf("create log: %w", err)}
	}
	defer logFile.Close()
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if err := cmd.Run(); err != nil {
		tail := tailLog(logPath, 500)
		return SpawnResult{Err: fmt.Errorf("gemini failed: %s", tail)}
	}

	reportPath := filepath.Join(jobDir, "report.html")
	if _, err := os.Stat(reportPath); err != nil {
		tail := tailLog(logPath, 500)
		return SpawnResult{Err: fmt.Errorf("report.html not found: %s", tail)}
	}

	content, _ := os.ReadFile(reportPath)
	// Synchronous Run path is only used by tests; the production async path
	// (Start + Watcher.finalize) reads aws_spend from projection.duckdb.
	spend := extractAWSSpendFromHTML(string(content))
	return SpawnResult{AWSSpend: spend}
}

// Start spawns gemini detached for a fresh projection run. sessionID is passed
// as --session-id so the gemini session is created with the job's known UUID
// (matching the DB session_id); a later refine resumes it. Empty sessionID
// lets gemini assign its own.
func (s *Spawner) Start(jobDir, inputExt, sessionID string) (int, error) {
	args := append(s.baseFlags(), sessionFlag(sessionID)...)
	args = append(args, "-p", buildProjectionPrompt(inputExt))
	return s.startDetached(jobDir, args)
}

// StartRefine resumes the job's prior gemini session (--resume latest) so the
// agent keeps the full conversational context of the original run, then feeds
// it the user's refinement instruction. Each job dir is its own gemini
// "project" with one session per run (plus one per retry), so "latest"
// resolves unambiguously to this job's most recent run. (gemini --resume takes
// "latest"/index rather than a UUID, so we can't target the session_id
// directly — per-job project isolation is what makes "latest" correct.) The
// DuckDB state also persists on disk, so the prompt names it as a fallback
// pointer in case the resumed context is thin.
func (s *Spawner) StartRefine(jobDir, _, instruction string) (int, error) {
	prompt := "Refine an existing AWS-to-GCP cost projection. " +
		"The projection state is in projection-audit/projection.duckdb in this directory. " +
		"Refinement instruction from the user: " + instruction + ". " +
		"Using the aws-gcp-cost-projection skill: update aws_li_to_gcp_li for affected " +
		"rows, recompute gcp_projection, then re-run Phase 6 to produce a new versioned " +
		"report. Keep all unrelated mappings unchanged. " +
		"IMPORTANT: run_results column meaning is fixed — gcp_od is always GCP On-Demand, " +
		"gcp_1yr_cud is always 1-year CUD, gcp_3yr_cud is always 3-year CUD. Display " +
		"instructions (e.g. 'use 3yr CUD as primary') change report layout only; they " +
		"NEVER permute run_results schema columns. Always use the named-column " +
		"INSERT (col_list) SELECT ... AS alias form per Phase 6. Report when done."
	args := append(s.baseFlags(), "--resume", "latest", "-p", prompt)
	return s.startDetached(jobDir, args)
}

// baseFlags are the gemini CLI flags common to every spawn:
//   --approval-mode=yolo : auto-approve all tool calls (no interactive prompts)
//   --skip-trust         : trust the workspace folder for this session. Without
//                          this, gemini downgrades yolo→default in untrusted dirs
//                          (job dirs under DATA_DIR are never pre-trusted), which
//                          would hang the headless run on the first tool approval.
//   --model <alias>      : from SpawnerConfig.GeminiModel (default "pro").
func (s *Spawner) baseFlags() []string {
	model := s.cfg.GeminiModel
	if model == "" {
		model = "pro"
	}
	return []string{"--approval-mode=yolo", "--skip-trust", "--model", model}
}

// sessionFlag returns the --session-id flag pair when id is non-empty, so the
// spawned gemini session is created with the job's known UUID (matching the DB
// session_id). Empty id → no flag, and gemini assigns its own session id.
func sessionFlag(id string) []string {
	if id == "" {
		return nil
	}
	return []string{"--session-id", id}
}

// startDetached is the shared spawn machinery: Setsid for true detachment,
// stdout/stderr → gemini.log (append, not truncate, so refinement output
// follows the original run's log), Process.Release so we never block on Wait.
func (s *Spawner) startDetached(jobDir string, args []string) (int, error) {
	cmd := exec.Command(s.geminiBin(), args...)
	cmd.Dir = jobDir
	cmd.Env = s.skillEnv()
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	logFile, err := os.OpenFile(
		filepath.Join(jobDir, "gemini.log"),
		os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644,
	)
	if err != nil {
		return 0, fmt.Errorf("open log: %w", err)
	}
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return 0, fmt.Errorf("start gemini: %w", err)
	}
	pid := cmd.Process.Pid
	cmd.Process.Release()
	return pid, nil
}

func (s *Spawner) geminiBin() string {
	if s.cfg.GeminiPath != "" {
		return s.cfg.GeminiPath
	}
	return "gemini"
}

// skillEnv returns os.Environ() plus SKILL_DIR pointing to the installed skill.
// The skill's bash scripts (preflight.sh, scripts/find-sku.sh) use $SKILL_DIR
// to resolve their own paths, since the agent's CWD is jobDir, not the skill dir.
func (s *Spawner) skillEnv() []string {
	home, err := os.UserHomeDir()
	if err != nil {
		return os.Environ()
	}
	skillDir := filepath.Join(home, ".gemini", "skills", "aws-gcp-cost-projection")
	return append(os.Environ(), "SKILL_DIR="+skillDir)
}

func buildProjectionPrompt(inputExt string) string {
	return fmt.Sprintf(
		"Use the aws-gcp-cost-projection skill to project costs from ./input%s. "+
			"Do not invoke any other skills — only aws-gcp-cost-projection. "+
			"If a skill other than aws-gcp-cost-projection seems relevant, ignore it. "+
			"If the input cannot be processed for a structural reason (wrong cloud — "+
			"e.g. Azure or GCP bill rather than AWS — corrupted file, unknown shape "+
			"that even the skill's PDF/CUR/CSV branches can't make sense of), write "+
			"a single short paragraph explaining why to ./failure.txt and exit. "+
			"Do not retry on a structural failure.",
		inputExt,
	)
}

func tailLog(path string, n int) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	if len(b) > n {
		b = b[len(b)-n:]
	}
	return string(b)
}
