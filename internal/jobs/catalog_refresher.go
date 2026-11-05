package jobs

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/skill"
)

// CatalogRefreshRun represents the live and recorded execution status of a GCP catalog refresh.
type CatalogRefreshRun struct {
	State           string   `json:"state"`                      // idle | running | success | failed | skipped
	StartedAt       string   `json:"started_at,omitempty"`
	FinishedAt      string   `json:"finished_at,omitempty"`
	ExitCode        int      `json:"exit_code"`
	Trigger         string   `json:"trigger,omitempty"`          // manual | auto
	Actor           string   `json:"actor,omitempty"`
	Reason          string   `json:"reason,omitempty"`           // populated on skipped/failed (e.g. image-baked)
	LogTail         []string `json:"log_tail,omitempty"`         // last ~40 lines of combined stdout/stderr
	DurationSeconds int      `json:"duration_seconds,omitempty"`
}

var refreshRunMu sync.Mutex

func refreshStatusPath() string {
	return filepath.Join(skill.ResolveDir(), "data", "catalog_refresh_status.json")
}

func readRefreshRunLocked(path string) CatalogRefreshRun {
	fallback := CatalogRefreshRun{State: "idle"}
	data, err := os.ReadFile(path)
	if err != nil {
		return fallback
	}
	var run CatalogRefreshRun
	if err := json.Unmarshal(data, &run); err != nil || run.State == "" {
		return fallback
	}
	return run
}

func readRefreshRun() CatalogRefreshRun {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	return readRefreshRunLocked(refreshStatusPath())
}

func writeRefreshRunLocked(path string, run CatalogRefreshRun) {
	data, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return
	}
	dir := filepath.Dir(path)
	_ = os.MkdirAll(dir, 0755)

	tmp, err := os.CreateTemp(dir, "catalog_refresh_status_*.tmp")
	if err != nil {
		_ = os.WriteFile(path, data, 0644)
		return
	}
	tmpName := tmp.Name()
	_, _ = tmp.Write(data)
	_ = tmp.Close()

	// On Windows, os.Rename fails if target already exists; remove first
	_ = os.Remove(path)
	if err := os.Rename(tmpName, path); err != nil {
		_ = os.WriteFile(path, data, 0644)
		_ = os.Remove(tmpName)
	}
}

func writeRefreshRun(run CatalogRefreshRun) {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	writeRefreshRunLocked(refreshStatusPath(), run)
	if run.State == "success" || run.State == "failed" || run.State == "skipped" {
		appendRefreshHistoryLocked(run)
	}
}

// ── Refresh history ─────────────────────────────────────────────────────
// Every finished run is kept (newest first, capped) in
// data/catalog_refresh_history.json so System Health can show a track record.

const refreshHistoryMax = 15

func refreshHistoryPath() string {
	return filepath.Join(skill.ResolveDir(), "data", "catalog_refresh_history.json")
}

func readRefreshHistoryLocked() []CatalogRefreshRun {
	data, err := os.ReadFile(refreshHistoryPath())
	if err != nil {
		return nil
	}
	var runs []CatalogRefreshRun
	if json.Unmarshal(data, &runs) != nil {
		return nil
	}
	return runs
}

func appendRefreshHistoryLocked(run CatalogRefreshRun) {
	if len(run.LogTail) > 15 {
		run.LogTail = run.LogTail[len(run.LogTail)-15:]
	}
	runs := append([]CatalogRefreshRun{run}, readRefreshHistoryLocked()...)
	if len(runs) > refreshHistoryMax {
		runs = runs[:refreshHistoryMax]
	}
	b, err := json.MarshalIndent(runs, "", "  ")
	if err != nil {
		return
	}
	_ = atomicWriteFile(refreshHistoryPath(), b)
}

// GetCatalogRefreshHistory returns finished refresh runs, newest first.
func GetCatalogRefreshHistory() []CatalogRefreshRun {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	return readRefreshHistoryLocked()
}

// GetCatalogRefreshStatus returns the latest persisted refresh run state.
func GetCatalogRefreshStatus() CatalogRefreshRun {
	return readRefreshRun()
}

// catalogStaleDays reads catalog_refresh_after_days from data/review-config.json,
// falling back to 7 if missing or unreadable.
func catalogStaleDays() int {
	skillDir := skill.ResolveDir()
	configPath := filepath.Join(skillDir, "data", "review-config.json")
	data, err := os.ReadFile(configPath)
	if err != nil {
		return 7
	}
	var cfg struct {
		CatalogRefreshAfterDays int `json:"catalog_refresh_after_days"`
	}
	if err := json.Unmarshal(data, &cfg); err != nil || cfg.CatalogRefreshAfterDays <= 0 {
		return 7
	}
	return cfg.CatalogRefreshAfterDays
}

// StartCatalogRefresher launches a background goroutine that keeps the GCP
// SKU catalog fresh. It checks catalog age on startup and then every week;
// if the catalog is older than catalogStaleDays it runs
// scripts/catalog_manager.py refresh inside the skill directory (fetch +
// integrity-gate + rebuild catalog.duckdb in one step).
//
// Requires Python 3 on PATH and GCP credentials (GOOGLE_CLOUD_API_KEY env var
// or `gcloud auth login`). If credentials are absent the refresh is a no-op
// that logs the gap without failing the server.
func StartCatalogRefresher(ctx context.Context) {
	go func() {
		// Run once immediately at startup, then on a weekly ticker.
		runCatalogRefreshIfStale()

		ticker := time.NewTicker(24 * time.Hour)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				runCatalogRefreshIfStale()
			}
		}
	}()
}

// catalogAge returns how many days ago the catalog was last fetched.
// Returns a large number if CATALOG_META.json is missing or unreadable.
func catalogAge() int {
	skillDir := skill.ResolveDir()
	metaPath := filepath.Join(skillDir, "data", "CATALOG_META.json")

	data, err := os.ReadFile(metaPath)
	if err != nil {
		return 9999
	}
	var meta struct {
		FetchedAt string `json:"fetched_at"`
	}
	if err := json.Unmarshal(data, &meta); err != nil || meta.FetchedAt == "" {
		return 9999
	}
	ts, err := time.Parse(time.RFC3339, meta.FetchedAt)
	if err != nil {
		return 9999
	}
	return int(time.Since(ts).Hours() / 24)
}

// isImageBaked returns true if the skill directory is located inside the container image layer (under /app).
func isImageBaked(skillDir string) bool {
	clean := filepath.ToSlash(filepath.Clean(skillDir))
	return clean == "/app" || strings.HasPrefix(clean, "/app/")
}

func runCatalogRefreshIfStale() {
	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		slog.Info("GCP catalog is image-baked and refreshed at build time, skipping runtime refresh", "skill_dir", skillDir)
		return
	}
	age := catalogAge()
	if age < catalogStaleDays() {
		slog.Debug("catalog is fresh, skipping refresh", "age_days", age)
		return
	}
	runCatalogRefreshNow("auto", "system")
}

// catalogRefreshTimeout bounds runCatalogRefreshNow's subprocess.
const catalogRefreshTimeout = 2 * time.Minute

// runCatalogRefreshNow runs the health-check/refresh script unconditionally,
// ignoring catalogStaleDays. Used by the forced "refresh now" admin action.
func runCatalogRefreshNow(trigger, actor string) {
	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		reason := "GCP catalog is image-baked and refreshed at build time, skipping runtime refresh"
		slog.Info(reason, "skill_dir", skillDir)
		writeRefreshRun(CatalogRefreshRun{
			State:      "skipped",
			Reason:     reason,
			Trigger:    trigger,
			Actor:      actor,
			FinishedAt: time.Now().UTC().Format(time.RFC3339),
		})
		return
	}
	script := filepath.Join(skillDir, "scripts", "catalog_manager.py")
	if _, err := os.Stat(script); err != nil {
		slog.Warn("catalog_manager.py not found, skipping refresh", "path", script)
		writeRefreshRun(CatalogRefreshRun{
			State:      "failed",
			Reason:     "catalog_manager.py not found",
			Trigger:    trigger,
			Actor:      actor,
			FinishedAt: time.Now().UTC().Format(time.RFC3339),
		})
		return
	}

	slog.Info("refreshing GCP catalog", "age_days", catalogAge(), "trigger", trigger, "actor", actor)
	startTime := time.Now().UTC()
	writeRefreshRun(CatalogRefreshRun{
		State:     "running",
		StartedAt: startTime.Format(time.RFC3339),
		Trigger:   trigger,
		Actor:     actor,
	})

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	ctx, cancel := context.WithTimeout(context.Background(), catalogRefreshTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, python, script, "refresh", "--skill-dir", skillDir)
	cmd.Dir = skillDir

	var buf bytes.Buffer
	mw := io.MultiWriter(os.Stdout, &buf)
	cmd.Stdout = mw
	cmd.Stderr = mw

	err := cmd.Run()
	finishedTime := time.Now().UTC()
	durationSec := int(finishedTime.Sub(startTime).Seconds())

	rawLines := strings.Split(buf.String(), "\n")
	var logTail []string
	for _, l := range rawLines {
		l = strings.TrimRight(l, "\r")
		if strings.TrimSpace(l) != "" {
			logTail = append(logTail, l)
		}
	}
	if len(logTail) > 40 {
		logTail = logTail[len(logTail)-40:]
	}

	if err != nil {
		exitCode := 1
		reason := err.Error()
		if ctx.Err() == context.DeadlineExceeded {
			reason = "timed out after 2m"
			slog.Warn("catalog refresh timed out, killed subprocess", "timeout", catalogRefreshTimeout)
		} else {
			if exitErr, ok := err.(*exec.ExitError); ok {
				exitCode = exitErr.ExitCode()
			}
			slog.Warn("catalog refresh finished with error", "err", err, "exit_code", exitCode)
		}
		writeRefreshRun(CatalogRefreshRun{
			State:           "failed",
			StartedAt:       startTime.Format(time.RFC3339),
			FinishedAt:      finishedTime.Format(time.RFC3339),
			ExitCode:        exitCode,
			Trigger:         trigger,
			Actor:           actor,
			Reason:          reason,
			LogTail:         logTail,
			DurationSeconds: durationSec,
		})
	} else {
		slog.Info("GCP catalog refresh complete", "duration_seconds", durationSec)
		writeRefreshRun(CatalogRefreshRun{
			State:           "success",
			StartedAt:       startTime.Format(time.RFC3339),
			FinishedAt:      finishedTime.Format(time.RFC3339),
			ExitCode:        0,
			Trigger:         trigger,
			Actor:           actor,
			LogTail:         logTail,
			DurationSeconds: durationSec,
		})
	}
}

// CatalogStatus is the admin-facing view of GCP catalog freshness.
type CatalogStatus struct {
	AgeDays int  `json:"age_days"`
	Stale   bool `json:"stale"`
}

// GetCatalogStatus reports the current catalog age and staleness. Exported
// so the admin catalog-status handler can surface it without duplicating
// the CATALOG_META.json read.
func GetCatalogStatus() CatalogStatus {
	age := catalogAge()
	return CatalogStatus{AgeDays: age, Stale: age >= catalogStaleDays()}
}

// TriggerCatalogRefresh runs a forced catalog refresh in the background
// (ignoring the staleness threshold) and returns immediately. Used by the
// admin "refresh now" action.
//
// Returns (started, reason). When the skill directory is image-baked the
// refresh cannot persist — it would write into a container layer discarded on
// the next deploy — so nothing is started. The caller MUST surface that to the
// admin: a button that reports "refresh started" while doing nothing is worse
// than one that declines, because the operator stops looking.
func TriggerCatalogRefresh(actor string) (bool, string) {
	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		reason := "catalog is baked into the container image and refreshed at build time; " +
			"a runtime refresh would be discarded on the next deploy"
		writeRefreshRun(CatalogRefreshRun{
			State:      "skipped",
			Reason:     reason,
			Trigger:    "manual",
			Actor:      actor,
			FinishedAt: time.Now().UTC().Format(time.RFC3339),
		})
		return false, reason
	}

	curr := readRefreshRun()
	if curr.State == "running" && curr.StartedAt != "" {
		if t, err := time.Parse(time.RFC3339, curr.StartedAt); err == nil {
			if time.Since(t) < catalogRefreshTimeout+30*time.Second {
				return false, "a refresh is already running"
			}
		}
	}

	go runCatalogRefreshNow("manual", actor)
	return true, ""
}
