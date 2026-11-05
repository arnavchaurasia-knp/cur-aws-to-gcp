package jobs

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestCatalogStaleDays_ReadsConfig(t *testing.T) {
	// Verify reading from real skill directory
	days := catalogStaleDays()
	if days != 7 {
		t.Fatalf("expected 7 days from review-config.json, got %d", days)
	}

	// Verify fallback when config is missing
	origSkillDir := os.Getenv("SKILL_DIR")
	defer os.Setenv("SKILL_DIR", origSkillDir)

	tempDir := t.TempDir()
	os.Setenv("SKILL_DIR", tempDir)
	if fallback := catalogStaleDays(); fallback != 7 {
		t.Fatalf("expected fallback 7 when config is missing, got %d", fallback)
	}

	// Verify custom threshold from review-config.json
	dataDir := filepath.Join(tempDir, "data")
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		t.Fatal(err)
	}
	cfgData := map[string]any{"catalog_refresh_after_days": 21}
	b, _ := json.Marshal(cfgData)
	if err := os.WriteFile(filepath.Join(dataDir, "review-config.json"), b, 0644); err != nil {
		t.Fatal(err)
	}
	if custom := catalogStaleDays(); custom != 21 {
		t.Fatalf("expected custom 21 from review-config.json, got %d", custom)
	}
}

func TestCatalogRefreshRun_RoundTrip(t *testing.T) {
	tempDir := t.TempDir()
	statusFile := filepath.Join(tempDir, "catalog_refresh_status.json")

	// Missing file returns idle state
	idle := readRefreshRunLocked(statusFile)
	if idle.State != "idle" {
		t.Fatalf("expected state idle, got %s", idle.State)
	}

	// Write running state
	run := CatalogRefreshRun{
		State:     "running",
		StartedAt: "2026-11-05T12:00:00Z",
		Trigger:   "manual",
		Actor:     "admin@test.com",
	}
	writeRefreshRunLocked(statusFile, run)

	readBack := readRefreshRunLocked(statusFile)
	if readBack.State != "running" || readBack.Actor != "admin@test.com" || readBack.Trigger != "manual" {
		t.Fatalf("unexpected read back: %+v", readBack)
	}

	// Write success state with log tail
	run.State = "success"
	run.FinishedAt = "2026-11-05T12:01:30Z"
	run.ExitCode = 0
	run.DurationSeconds = 90
	run.LogTail = []string{"line 1", "line 2", "GCP catalog refresh complete"}
	writeRefreshRunLocked(statusFile, run)

	readBack2 := readRefreshRunLocked(statusFile)
	if readBack2.State != "success" || readBack2.DurationSeconds != 90 || len(readBack2.LogTail) != 3 {
		t.Fatalf("unexpected read back 2: %+v", readBack2)
	}
}

func TestTriggerCatalogRefresh_ImageBaked(t *testing.T) {
	tempDir := t.TempDir()
	dataDir := filepath.Join(tempDir, "data")
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		t.Fatal(err)
	}

	origSkillDir := os.Getenv("SKILL_DIR")
	defer os.Setenv("SKILL_DIR", origSkillDir)

	// Set image-baked path
	os.Setenv("SKILL_DIR", "/app/skill")

	started, reason := TriggerCatalogRefresh("admin@example.com")
	if started {
		t.Fatalf("expected started to be false for image-baked, got true")
	}
	if reason == "" {
		t.Fatalf("expected non-empty reason for refusal")
	}
}

func TestCheckJSONStore_Scenarios(t *testing.T) {
	tempDir := t.TempDir()

	// 1. Missing file
	missingRes := checkJSONStore(filepath.Join(tempDir, "nonexistent.json"), "test", "nonexistent.json", false, 1)
	if missingRes.Status != "fail" {
		t.Fatalf("expected fail for missing file, got %s", missingRes.Status)
	}

	// 1b. Missing file with warnIfMissing=true
	warnMissing := checkJSONStore(filepath.Join(tempDir, "optional.json"), "opt", "optional.json", true, 0)
	if warnMissing.Status != "warn" {
		t.Fatalf("expected warn for optional missing file, got %s", warnMissing.Status)
	}

	// 2. Corrupt file
	corruptPath := filepath.Join(tempDir, "corrupt.json")
	if err := os.WriteFile(corruptPath, []byte("{invalid-json"), 0644); err != nil {
		t.Fatal(err)
	}
	corruptRes := checkJSONStore(corruptPath, "corrupt", "corrupt.json", false, 1)
	if corruptRes.Status != "fail" {
		t.Fatalf("expected fail for corrupt file, got %s", corruptRes.Status)
	}

	// 3. Empty JSON container with minCountFloor > 0
	emptyPath := filepath.Join(tempDir, "empty.json")
	if err := os.WriteFile(emptyPath, []byte("{}"), 0644); err != nil {
		t.Fatal(err)
	}
	emptyRes := checkJSONStore(emptyPath, "empty", "empty.json", false, 1)
	if emptyRes.Status != "fail" {
		t.Fatalf("expected fail for empty map, got %s", emptyRes.Status)
	}

	// 4. Valid file
	validPath := filepath.Join(tempDir, "valid.json")
	if err := os.WriteFile(validPath, []byte(`{"a": 1, "b": 2}`), 0644); err != nil {
		t.Fatal(err)
	}
	validRes := checkJSONStore(validPath, "valid", "valid.json", false, 1)
	if validRes.Status != "ok" || validRes.Count != 2 {
		t.Fatalf("expected ok with count 2, got status=%s count=%d", validRes.Status, validRes.Count)
	}
}
