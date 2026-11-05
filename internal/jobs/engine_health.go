package jobs

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/config"
)

// EngineProbe represents the health and round-trip liveness of the agy engine.
type EngineProbe struct {
	State       string `json:"state"`        // ok | warn | fail | unknown
	Model       string `json:"model"`
	BinaryFound bool   `json:"binary_found"`
	BinaryPath  string `json:"binary_path"`
	Version     string `json:"version"`
	AuthPresent bool   `json:"auth_present"` // ANTIGRAVITY_API_KEY set (non-empty) — DO NOT return the key
	RoundTrip   bool   `json:"round_trip"`   // a --print call returned output with no error
	LatencyMS   int64  `json:"latency_ms"`
	CheckedAt   string `json:"checked_at"`
	Detail      string `json:"detail"`       // human reason, esp. on warn/fail
}

var (
	engineProbeMu      sync.Mutex
	cachedEngineProbe  *EngineProbe
	cachedEngineProbeAt time.Time
)

// InvalidateEngineProbeCache clears the cached probe result.
func InvalidateEngineProbeCache() {
	engineProbeMu.Lock()
	cachedEngineProbe = nil
	cachedEngineProbeAt = time.Time{}
	engineProbeMu.Unlock()
}

// activeModel resolves the active model from DB engine settings or config default.
func (s *Spawner) activeModel() string {
	if s.db != nil {
		if choice, _, err := s.db.GetEngineSettings(); err == nil && choice != "" {
			return choice
		}
	}
	if s.cfg.AGYModel != "" {
		return s.cfg.AGYModel
	}
	return config.DefaultAGYModel
}

// ProbeEngine inspects the engine binary, authentication credentials, and optionally
// performs an end-to-end round trip call to verify the model API is functional.
func (s *Spawner) ProbeEngine(ctx context.Context, forceLive bool) EngineProbe {
	engineProbeMu.Lock()
	if !forceLive && cachedEngineProbe != nil && time.Since(cachedEngineProbeAt) < 60*time.Second {
		res := *cachedEngineProbe
		engineProbeMu.Unlock()
		return res
	}
	engineProbeMu.Unlock()

	active := s.activeModel()
	bin := s.agyBin()

	probe := EngineProbe{
		Model:     active,
		CheckedAt: time.Now().UTC().Format(time.RFC3339),
		State:     "unknown",
	}

	// 1. Binary check
	resolvedPath, err := exec.LookPath(bin)
	if err != nil {
		probe.BinaryFound = false
		probe.State = "fail"
		probe.Detail = fmt.Sprintf("agy binary not found on PATH (set AGY_PATH; looked for %q)", bin)
		return probe
	}
	probe.BinaryFound = true
	probe.BinaryPath = resolvedPath

	// 2. Version check
	vCtx, vCancel := context.WithTimeout(ctx, 5*time.Second)
	defer vCancel()
	vCmd := exec.CommandContext(vCtx, resolvedPath, "--version")
	if vOut, vErr := vCmd.Output(); vErr == nil {
		probe.Version = strings.TrimSpace(string(vOut))
	} else {
		probe.Version = "unknown"
	}

	// 3. Auth check - NEVER log or return key contents
	probe.AuthPresent = os.Getenv("ANTIGRAVITY_API_KEY") != ""
	if !probe.AuthPresent {
		probe.State = "fail"
		probe.Detail = "ANTIGRAVITY_API_KEY not set; spawns will fail auth"
		return probe
	}

	// If kill switch active
	if os.Getenv("AGY_HEALTH_PROBE_DISABLED") == "1" {
		probe.State = "unknown"
		probe.Detail = "live probe disabled (AGY_HEALTH_PROBE_DISABLED=1)"
		return probe
	}

	// If not forceLive and no valid cache, avoid blocking health dashboard
	if !forceLive {
		probe.State = "unknown"
		probe.Detail = "probe not yet run (use Test Engine)"
		return probe
	}

	// 4. Live RoundTrip Probe
	tempDir, err := os.MkdirTemp("", "agy_probe_*")
	if err != nil {
		probe.State = "fail"
		probe.Detail = fmt.Sprintf("temp dir creation failed: %v", err)
		return probe
	}
	defer os.RemoveAll(tempDir)

	probeCtx, probeCancel := context.WithTimeout(ctx, 30*time.Second)
	defer probeCancel()

	// agy --model <active> --print "Reply with the single word: OK" --dangerously-skip-permissions
	args := []string{"--model", active, "--dangerously-skip-permissions", "--print", "Reply with the single word: OK"}
	cmd := exec.CommandContext(probeCtx, resolvedPath, args...)
	cmd.Dir = tempDir
	cmd.Env = s.skillEnv()

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	runErr := cmd.Run()
	latency := time.Since(start).Milliseconds()
	probe.LatencyMS = latency

	outStr := strings.TrimSpace(stdout.String())
	errTail := strings.TrimSpace(stderr.String())
	if len(errTail) > 400 {
		errTail = errTail[len(errTail)-400:]
	}

	if runErr != nil {
		probe.RoundTrip = false
		probe.State = "fail"
		if probeCtx.Err() == context.DeadlineExceeded {
			probe.Detail = fmt.Sprintf("probe timed out after 30s (engine unresponsive)")
		} else if errTail != "" {
			probe.Detail = fmt.Sprintf("probe failed (%v): %s", runErr, errTail)
		} else if outStr != "" {
			probe.Detail = fmt.Sprintf("probe failed (%v): %s", runErr, outStr)
		} else {
			probe.Detail = fmt.Sprintf("probe execution failed: %v", runErr)
		}
	} else if outStr == "" {
		probe.RoundTrip = false
		probe.State = "fail"
		probe.Detail = "probe returned exit code 0 but empty response"
	} else {
		probe.RoundTrip = true
		if latency > 15000 {
			probe.State = "warn"
			probe.Detail = fmt.Sprintf("engine slow (latency %dms)", latency)
		} else {
			probe.State = "ok"
			probe.Detail = fmt.Sprintf("round-trip ok (%dms)", latency)
		}
	}

	// Cache successful or evaluated live probe
	engineProbeMu.Lock()
	cachedEngineProbe = &probe
	cachedEngineProbeAt = time.Now()
	engineProbeMu.Unlock()

	return probe
}
