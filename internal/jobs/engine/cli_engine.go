package engine

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/config"
)

// CLIEngineConfig holds configuration for the CLI-based (agy) engine.
type CLIEngineConfig struct {
	AGYPath     string
	AGYModel    string
	Concurrency int
}

// CLIEngine implements Engine using the local agy CLI binary.
// It uses an in-process semaphore to restrict concurrency on a single machine/identity.
type CLIEngine struct {
	cfg  CLIEngineConfig
	sem  chan struct{}
	once sync.Once
}

// NewCLIEngine creates a new CLIEngine. If concurrency <= 0, it inspects
// CUR_WEB_CLI_CONCURRENCY (default 2).
func NewCLIEngine(cfg CLIEngineConfig) *CLIEngine {
	conc := cfg.Concurrency
	if conc <= 0 {
		if val := strings.TrimSpace(os.Getenv("CUR_WEB_CLI_CONCURRENCY")); val != "" {
			if n, err := strconv.Atoi(val); err == nil && n > 0 {
				conc = n
			}
		}
	}
	if conc <= 0 {
		conc = 2
	}
	cfg.Concurrency = conc
	return &CLIEngine{
		cfg: cfg,
		sem: make(chan struct{}, conc),
	}
}

// Version returns the agy binary version string (or fallback if unable to determine).
func (e *CLIEngine) Version() string {
	bin := e.agyBin()
	out, err := exec.Command(bin, "--version").Output()
	if err != nil {
		return "agy-unknown"
	}
	ver := strings.TrimSpace(string(out))
	if ver == "" {
		return "agy"
	}
	return ver
}

// Admit acquires a slot in the local concurrency semaphore or blocks until ctx is done.
func (e *CLIEngine) Admit(ctx context.Context, tenant TenantID) (func(), error) {
	select {
	case e.sem <- struct{}{}:
		var released bool
		var mu sync.Mutex
		return func() {
			mu.Lock()
			defer mu.Unlock()
			if !released {
				released = true
				<-e.sem
			}
		}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// RunPhase executes a phase prompt using the agy CLI tool.
func (e *CLIEngine) RunPhase(ctx context.Context, req PhaseRequest) (PhaseResult, error) {
	start := time.Now()
	if req.Phase.Prompt == "" {
		return PhaseResult{
			DurationSeconds: int(time.Since(start).Seconds()),
		}, nil
	}

	release, err := e.Admit(ctx, req.Tenant)
	if err != nil {
		return PhaseResult{Err: err}, err
	}
	defer release()

	// Build CLI invocation
	args := append(e.baseFlags(), "--add-dir", req.JobDir, "-p", req.Phase.Prompt)
	cmd := exec.CommandContext(ctx, e.agyBin(), args...)
	cmd.Dir = req.JobDir
	cmd.Env = os.Environ()

	logFile, err := os.OpenFile(
		filepath.Join(req.JobDir, "agy.log"),
		os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644,
	)
	if err != nil {
		res := PhaseResult{
			DurationSeconds: int(time.Since(start).Seconds()),
			Err:             fmt.Errorf("open log: %w", err),
		}
		return res, res.Err
	}
	defer logFile.Close()

	cmd.Stdout = logFile
	cmd.Stderr = logFile

	runErr := cmd.Run()
	dur := int(time.Since(start).Seconds())
	return PhaseResult{
		DurationSeconds: dur,
		Err:             runErr,
	}, runErr
}

func (e *CLIEngine) agyBin() string {
	if p := strings.TrimSpace(os.Getenv("AGY_PATH")); p != "" {
		return p
	}
	if e.cfg.AGYPath != "" {
		return e.cfg.AGYPath
	}
	return "agy"
}

func (e *CLIEngine) baseFlags() []string {
	model := e.cfg.AGYModel
	if model == "" {
		model = config.DefaultAGYModel
	}
	return []string{
		"--dangerously-skip-permissions",
		"--model", model,
		"--print-timeout", config.AGYPrintTimeout(),
	}
}

