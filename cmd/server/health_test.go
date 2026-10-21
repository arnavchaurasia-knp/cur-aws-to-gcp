package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// writeMeta builds a throwaway skill dir containing CATALOG_META.json with the
// given body, and returns its path.
func writeMeta(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	dataDir := filepath.Join(dir, "data")
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dataDir, "CATALOG_META.json"), []byte(body), 0o644); err != nil {
		t.Fatalf("write meta: %v", err)
	}
	return dir
}

// NOTE: these assertions deliberately check SHAPE, never a specific date.
// An earlier version pinned fetched_at to the committed value and required an
// age over 80 days — which meant refreshing the catalog broke the test. Since
// the whole point of the Dockerfile freshness gate is to force that refresh,
// the test punished the action it exists to encourage.
func TestLoadCatalogVintage(t *testing.T) {
	t.Run("fresh catalog", func(t *testing.T) {
		stamp := time.Now().UTC().Add(-2 * time.Hour).Format(time.RFC3339)
		dir := writeMeta(t, `{"fetched_at":"`+stamp+`"}`)

		fetchedAt, ageDays := loadCatalogVintage(dir)
		if fetchedAt != stamp {
			t.Errorf("fetched_at: got %v, want %v", fetchedAt, stamp)
		}
		days, ok := ageDays.(int)
		if !ok {
			t.Fatalf("age_days: got %T, want int", ageDays)
		}
		if days != 0 {
			t.Errorf("age_days for a 2-hour-old catalog: got %d, want 0", days)
		}
	})

	t.Run("old catalog", func(t *testing.T) {
		stamp := time.Now().UTC().AddDate(0, 0, -90).Format(time.RFC3339)
		dir := writeMeta(t, `{"fetched_at":"`+stamp+`"}`)

		_, ageDays := loadCatalogVintage(dir)
		days, ok := ageDays.(int)
		if !ok {
			t.Fatalf("age_days: got %T, want int", ageDays)
		}
		// Allow a day of slack for clock/rounding rather than pinning exactly.
		if days < 89 || days > 91 {
			t.Errorf("age_days for a 90-day-old catalog: got %d, want ~90", days)
		}
	})

	t.Run("missing metadata degrades to nil", func(t *testing.T) {
		fetchedAt, ageDays := loadCatalogVintage(t.TempDir())
		if fetchedAt != nil || ageDays != nil {
			t.Errorf("got (%v, %v), want (nil, nil)", fetchedAt, ageDays)
		}
	})

	t.Run("corrupt metadata degrades to nil", func(t *testing.T) {
		dir := writeMeta(t, "{invalid json")
		fetchedAt, ageDays := loadCatalogVintage(dir)
		if fetchedAt != nil || ageDays != nil {
			t.Errorf("got (%v, %v), want (nil, nil)", fetchedAt, ageDays)
		}
	})

	t.Run("unparseable timestamp keeps the string, drops the age", func(t *testing.T) {
		dir := writeMeta(t, `{"fetched_at":"not-a-date"}`)
		fetchedAt, ageDays := loadCatalogVintage(dir)
		if fetchedAt != "not-a-date" {
			t.Errorf("fetched_at: got %v, want the raw string", fetchedAt)
		}
		if ageDays != nil {
			t.Errorf("age_days: got %v, want nil", ageDays)
		}
	})
}

// Exercises the REAL handler from main.go. An earlier version of this test
// defined its own inline handler returning hardcoded JSON and asserted that
// JSON came back — it would have passed with the real handler deleted.
func TestHealthHandler(t *testing.T) {
	t.Run("reports the catalog vintage", func(t *testing.T) {
		rec := httptest.NewRecorder()
		healthHandler("2026-07-27T06:50:51Z", 86).
			ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))

		if rec.Code != http.StatusOK {
			t.Fatalf("status: got %d, want 200", rec.Code)
		}
		if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
			t.Errorf("content-type: got %q", ct)
		}
		var res map[string]any
		if err := json.Unmarshal(rec.Body.Bytes(), &res); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if res["status"] != "ok" {
			t.Errorf("status: got %v, want ok", res["status"])
		}
		// Safe to pin: this value is injected by the test, not read from the
		// committed catalog, so refreshing the catalog cannot break it.
		if res["catalog_fetched_at"] != "2026-07-27T06:50:51Z" {
			t.Errorf("catalog_fetched_at: got %v", res["catalog_fetched_at"])
		}
		if res["catalog_age_days"] != float64(86) {
			t.Errorf("catalog_age_days: got %v, want 86", res["catalog_age_days"])
		}
	})

	// A liveness probe must not fail because a metadata file is unreadable.
	t.Run("stays 200 with a null vintage", func(t *testing.T) {
		rec := httptest.NewRecorder()
		healthHandler(nil, nil).
			ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))

		if rec.Code != http.StatusOK {
			t.Fatalf("status: got %d, want 200", rec.Code)
		}
		var res map[string]any
		if err := json.Unmarshal(rec.Body.Bytes(), &res); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if res["status"] != "ok" {
			t.Errorf("status: got %v, want ok", res["status"])
		}
		if res["catalog_age_days"] != nil {
			t.Errorf("catalog_age_days: got %v, want null", res["catalog_age_days"])
		}
	})
}
