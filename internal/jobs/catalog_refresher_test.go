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
