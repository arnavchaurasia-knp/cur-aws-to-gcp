package jobs

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

type SpawnerConfig struct {
	ClaudePath string
	// ANTHROPIC_API_KEY is intentionally not part of SpawnerConfig. The
	// spawned claude inherits it from os.Environ(); set it on the host
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

// RunWithSession spawns claude with a fixed session ID so the transcript is
// at a deterministic path: ~/.claude/projects/<encoded-cwd>/<sessionID>.jsonl
// If sessionID is empty, claude generates one (transcript path then unknown).
func (s *Spawner) RunWithSession(jobDir, inputExt, sessionID string) SpawnResult {
	prompt := fmt.Sprintf(
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
	claudeBin := s.cfg.ClaudePath
	if claudeBin == "" {
		claudeBin = "claude"
	}
	args := []string{"--dangerously-skip-permissions", "--model", "sonnet"}
	if sessionID != "" {
		args = append(args, "--session-id", sessionID)
	}
	args = append(args, "-p", prompt)
	cmd := exec.Command(claudeBin, args...)
	cmd.Dir = jobDir
	cmd.Env = os.Environ() // claude inherits ANTHROPIC_API_KEY from here.

	logPath := filepath.Join(jobDir, "claude.log")
	logFile, err := os.Create(logPath)
	if err != nil {
		return SpawnResult{Err: fmt.Errorf("create log: %w", err)}
	}
	defer logFile.Close()
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if err := cmd.Run(); err != nil {
		tail := tailLog(logPath, 500)
		return SpawnResult{Err: fmt.Errorf("claude failed: %s", tail)}
	}

	reportPath := filepath.Join(jobDir, "report.html")
	if _, err := os.Stat(reportPath); err != nil {
		tail := tailLog(logPath, 500)
		return SpawnResult{Err: fmt.Errorf("report.html not found: %s", tail)}
	}

	content, _ := os.ReadFile(reportPath)
	// Synchronous Spawner.Run path is only used by tests; the production
	// async path (Start + Watcher.finalize) reads aws_spend from the
	// projection.duckdb instead. Keep the regex here just so the test
	// fixture (which writes a tiny HTML) still asserts AWSSpend.
	spend := extractAWSSpendFromHTML(string(content))
	return SpawnResult{AWSSpend: spend}
}

// Start spawns claude detached for a fresh projection run.
func (s *Spawner) Start(jobDir, inputExt, sessionID string) (int, error) {
	prompt := fmt.Sprintf(
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
	args := []string{"--dangerously-skip-permissions", "--model", "sonnet"}
	if sessionID != "" {
		args = append(args, "--session-id", sessionID)
	}
	args = append(args, "-p", prompt)
	return s.startDetached(jobDir, args)
}

// StartRefine resumes an existing claude session and asks it to refine the
// projection per a free-form user instruction. The skill is already loaded in
// the resumed session, so the refinement turn has full context (mappings,
// duckdb state, prior report).
func (s *Spawner) StartRefine(jobDir, sessionID, instruction string) (int, error) {
	if sessionID == "" {
		return 0, fmt.Errorf("StartRefine: sessionID required")
	}
	prompt := "Refinement request from the user: " + instruction +
		". Update aws_li_to_gcp_li, recompute gcp_projection, and rewrite both " +
		"projection-audit/report.md and projection-audit/report.html to reflect the change. " +
		"Keep all unrelated mappings unchanged. Use only the aws-gcp-cost-projection skill. " +
		"IMPORTANT: run_results column meaning is fixed — gcp_od is always the GCP " +
		"On-Demand math, gcp_1yr_cud is always 1-year CUD, gcp_3yr_cud is always 3-year " +
		"CUD. Display-preference instructions (e.g. 'use 3yr CUD as primary') change " +
		"report layout only; they NEVER permute run_results schema columns. Always use " +
		"the named-column INSERT (col_list) SELECT ... AS alias form per Phase 6. " +
		"Report when done."
	args := []string{
		"--dangerously-skip-permissions", "--model", "sonnet",
		"--resume", sessionID,
		"-p", prompt,
	}
	return s.startDetached(jobDir, args)
}

// startDetached is the shared spawn machinery: Setsid for true detachment,
// stdout/stderr → claude.log (append, not truncate, so refinement output
// follows the original run's log), Process.Release so we never block on Wait.
func (s *Spawner) startDetached(jobDir string, args []string) (int, error) {
	claudeBin := s.cfg.ClaudePath
	if claudeBin == "" {
		claudeBin = "claude"
	}
	cmd := exec.Command(claudeBin, args...)
	cmd.Dir = jobDir
	cmd.Env = os.Environ() // claude inherits ANTHROPIC_API_KEY from here.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	logFile, err := os.OpenFile(
		filepath.Join(jobDir, "claude.log"),
		os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644,
	)
	if err != nil {
		return 0, fmt.Errorf("open log: %w", err)
	}
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return 0, fmt.Errorf("start claude: %w", err)
	}
	pid := cmd.Process.Pid
	cmd.Process.Release()
	return pid, nil
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
