package jobs

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/config"
)

// ─────────────────────────────────────────────────────────────────────────
// AI engine health
//
// Two levels of checking, on purpose:
//   - InspectEngine (cheap, runs on every System Health load): is the agy CLI
//     installed, what version, is the API key present. No model call.
//   - ProbeEngine(…, true) (the "Test AI engine" button): sends a one-word
//     prompt through agy to the model and measures the round trip. Uses a tiny
//     amount of quota, so it only runs when an admin asks for it.
//
// Every live test is saved (engine_tests.json in the data folder) so the page
// can show "last tested 2h ago: OK" after a reload or restart.
// ─────────────────────────────────────────────────────────────────────────

// EngineProbe is the result of inspecting or live-testing the AI engine.
type EngineProbe struct {
	State       string `json:"state"` // ok | warn | fail | unknown
	Model       string `json:"model"`
	BinaryFound bool   `json:"binary_found"`
	BinaryPath  string `json:"binary_path"`
	Version     string `json:"version"`
	AuthPresent bool   `json:"auth_present"` // ANTIGRAVITY_API_KEY set — the value is never returned
	RoundTrip   bool   `json:"round_trip"`   // a live prompt returned output
	LatencyMS   int64  `json:"latency_ms"`
	CheckedAt   string `json:"checked_at"`
	Detail      string `json:"detail"`
}

// EngineSlowThreshold: a successful round trip slower than this is "warn".
const EngineSlowThreshold = 15 * time.Second

// engineProbeTimeout bounds the live round trip.
const engineProbeTimeout = 30 * time.Second

var (
	versionCacheMu   sync.Mutex
	versionCachePath string
	versionCacheVal  string
	versionCacheAt   time.Time
)

// InvalidateEngineProbeCache clears cached CLI version info (call after the
// agy path changes).
func InvalidateEngineProbeCache() {
	versionCacheMu.Lock()
	versionCachePath, versionCacheVal, versionCacheAt = "", "", time.Time{}
	versionCacheMu.Unlock()
}

// activeModel resolves the model: Report Settings default engine, else
// AGY_MODEL, else the built-in default. modelSource says which one won.
func (s *Spawner) activeModel() (model, source string) {
	if s.db != nil {
		if choice, _, err := s.db.GetEngineSettings(); err == nil && choice != "" {
			return choice, "report_settings"
		}
	}
	if v := strings.TrimSpace(os.Getenv("AGY_MODEL")); v != "" {
		return v, "env"
	}
	if s.cfg.AGYModel != "" {
		return s.cfg.AGYModel, "env"
	}
	return config.DefaultAGYModel, "default"
}

// cliVersion runs `agy --version`, cached for 5 minutes per path.
func cliVersion(ctx context.Context, path string) string {
	versionCacheMu.Lock()
	if versionCachePath == path && time.Since(versionCacheAt) < 5*time.Minute {
		v := versionCacheVal
		versionCacheMu.Unlock()
		return v
	}
	versionCacheMu.Unlock()

	vCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	v := "unknown"
	if out, err := exec.CommandContext(vCtx, path, "--version").Output(); err == nil {
		if s := strings.TrimSpace(string(out)); s != "" {
			v = s
		}
	}
	versionCacheMu.Lock()
	versionCachePath, versionCacheVal, versionCacheAt = path, v, time.Now()
	versionCacheMu.Unlock()
	return v
}

// InspectEngine checks the CLI and API key without calling the model.
// State is "fail" when the CLI or key is missing, otherwise "unknown" (a live
// test or a recent successful job is needed to say "ok").
func (s *Spawner) InspectEngine(ctx context.Context) EngineProbe {
	model, _ := s.activeModel()
	p := EngineProbe{Model: model, CheckedAt: time.Now().UTC().Format(time.RFC3339), State: "unknown"}

	bin := s.agyBin()
	resolved, err := exec.LookPath(bin)
	if err != nil {
		p.State = "fail"
		p.BinaryPath = bin
		p.Detail = fmt.Sprintf("agy tool not found (looked for %q). Install it or set AGY_PATH.", bin)
		return p
	}
	p.BinaryFound = true
	p.BinaryPath = resolved
	p.Version = cliVersion(ctx, resolved)

	p.AuthPresent = os.Getenv("ANTIGRAVITY_API_KEY") != ""
	if !p.AuthPresent {
		p.State = "fail"
		p.Detail = "ANTIGRAVITY_API_KEY is not set, so the AI engine can't sign in."
		return p
	}
	p.Detail = "agy is installed and an API key is present. Run a test to confirm the model answers."
	return p
}

// ProbeEngine inspects the engine and, when forceLive is true, sends a tiny
// prompt to the model. forceLive=false is equivalent to InspectEngine.
func (s *Spawner) ProbeEngine(ctx context.Context, forceLive bool) EngineProbe {
	p := s.InspectEngine(ctx)
	if p.State == "fail" || !forceLive {
		return p
	}
	if os.Getenv("AGY_HEALTH_PROBE_DISABLED") == "1" {
		p.State = "unknown"
		p.Detail = "Live test is turned off (AGY_HEALTH_PROBE_DISABLED=1)."
		return p
	}

	tempDir, err := os.MkdirTemp("", "agy_probe_*")
	if err != nil {
		p.State = "fail"
		p.Detail = fmt.Sprintf("could not create a temp folder for the test: %v", err)
		return p
	}
	defer os.RemoveAll(tempDir)

	probeCtx, cancel := context.WithTimeout(ctx, engineProbeTimeout)
	defer cancel()

	args := []string{"--model", p.Model, "--dangerously-skip-permissions", "--print", "Reply with the single word: OK"}
	cmd := exec.CommandContext(probeCtx, p.BinaryPath, args...)
	cmd.Dir = tempDir
	cmd.Env = s.skillEnv()
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr

	start := time.Now()
	runErr := cmd.Run()
	p.LatencyMS = time.Since(start).Milliseconds()
	p.CheckedAt = time.Now().UTC().Format(time.RFC3339)

	out := strings.TrimSpace(stdout.String())
	errTail := tail(strings.TrimSpace(stderr.String()), 400)

	switch {
	case runErr != nil && probeCtx.Err() == context.DeadlineExceeded:
		p.State = "fail"
		p.Detail = fmt.Sprintf("No answer within %s — the model or network is unresponsive.", engineProbeTimeout)
	case runErr != nil:
		p.State = "fail"
		msg := errTail
		if msg == "" {
			msg = tail(out, 400)
		}
		if msg == "" {
			msg = runErr.Error()
		}
		p.Detail = "The model call failed: " + msg
	case out == "":
		p.State = "fail"
		p.Detail = "agy exited normally but returned an empty answer."
	default:
		p.RoundTrip = true
		if time.Duration(p.LatencyMS)*time.Millisecond > EngineSlowThreshold {
			p.State = "warn"
			p.Detail = fmt.Sprintf("The model answered, but slowly (%.1fs). Jobs will take longer than usual.", float64(p.LatencyMS)/1000)
		} else {
			p.State = "ok"
			p.Detail = fmt.Sprintf("The model answered in %.1fs.", float64(p.LatencyMS)/1000)
		}
	}
	return p
}

func tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return "…" + s[len(s)-n:]
}

// ── Test history ────────────────────────────────────────────────────────

// EngineTestRecord is one saved live test.
type EngineTestRecord struct {
	EngineProbe
	Actor string `json:"actor,omitempty"`
}

const engineHistoryMax = 20

var engineHistoryMu sync.Mutex

func engineHistoryPath(dataDir string) string {
	return filepath.Join(dataDir, "health", "engine_tests.json")
}

// loadEngineHistory returns saved tests, newest first.
func loadEngineHistory(dataDir string) []EngineTestRecord {
	engineHistoryMu.Lock()
	defer engineHistoryMu.Unlock()
	return readEngineHistoryLocked(dataDir)
}

func readEngineHistoryLocked(dataDir string) []EngineTestRecord {
	b, err := os.ReadFile(engineHistoryPath(dataDir))
	if err != nil {
		return nil
	}
	var recs []EngineTestRecord
	if json.Unmarshal(b, &recs) != nil {
		return nil
	}
	return recs
}

// appendEngineHistory saves a test result (newest first, capped).
func appendEngineHistory(dataDir string, rec EngineTestRecord) {
	engineHistoryMu.Lock()
	defer engineHistoryMu.Unlock()
	recs := append([]EngineTestRecord{rec}, readEngineHistoryLocked(dataDir)...)
	if len(recs) > engineHistoryMax {
		recs = recs[:engineHistoryMax]
	}
	b, err := json.MarshalIndent(recs, "", "  ")
	if err != nil {
		return
	}
	_ = atomicWriteFile(engineHistoryPath(dataDir), b)
}

// atomicWriteFile writes data via a temp file + rename in the same folder, so a
// reader never sees a half-written file. Windows-safe (removes the target
// before renaming, falling back to a direct write).
func atomicWriteFile(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return os.WriteFile(path, data, 0o644)
	}
	name := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := os.Rename(name, path); err != nil {
		_ = os.Remove(path)
		if err2 := os.Rename(name, path); err2 != nil {
			os.Remove(name)
			return os.WriteFile(path, data, 0o644)
		}
	}
	return nil
}
