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

func TestSecurityHeadersCSP(t *testing.T) {
	dummy := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := securityHeaders(dummy)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	handler.ServeHTTP(rec, req)

	csp := rec.Header().Get("Content-Security-Policy")
	if csp == "" {
		t.Fatal("Content-Security-Policy header is missing")
	}

	wantStyleOrigin := "https://fonts.googleapis.com"
	if !containsDirectiveToken(csp, "style-src", wantStyleOrigin) {
		t.Errorf("CSP style-src does not contain %s; got CSP: %s", wantStyleOrigin, csp)
	}

	wantFontOrigin := "https://fonts.gstatic.com"
	if !containsDirectiveToken(csp, "font-src", wantFontOrigin) {
		t.Errorf("CSP font-src does not contain %s; got CSP: %s", wantFontOrigin, csp)
	}
}

func containsDirectiveToken(csp, directive, token string) bool {
	for _, part := range splitSemicolons(csp) {
		trimmed := trimSpace(part)
		if len(trimmed) >= len(directive) && trimmed[:len(directive)] == directive {
			for _, word := range splitSpaces(trimmed[len(directive):]) {
				if word == token {
					return true
				}
			}
		}
	}
	return false
}

func splitSemicolons(s string) []string {
	var parts []string
	curr := ""
	for i := 0; i < len(s); i++ {
		if s[i] == ';' {
			parts = append(parts, curr)
			curr = ""
		} else {
			curr += string(s[i])
		}
	}
	if curr != "" {
		parts = append(parts, curr)
	}
	return parts
}

func splitSpaces(s string) []string {
	var words []string
	curr := ""
	for i := 0; i < len(s); i++ {
		if s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r' {
			if curr != "" {
				words = append(words, curr)
				curr = ""
			}
		} else {
			curr += string(s[i])
		}
	}
	if curr != "" {
		words = append(words, curr)
	}
	return words
}

func trimSpace(s string) string {
	start := 0
	for start < len(s) && (s[start] == ' ' || s[start] == '\t' || s[start] == '\n' || s[start] == '\r') {
		start++
	}
	end := len(s)
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t' || s[end-1] == '\n' || s[end-1] == '\r') {
		end--
	}
	return s[start:end]
}

// TestSecurityHeadersHSTSBehindProxy pins the production case, not the local
// one. Render terminates TLS at its edge and forwards plain HTTP, so r.TLS is
// nil on every live request; gating HSTS on r.TLS alone meant the header was
// never actually sent on the deployed site, while every local check passed.
func TestSecurityHeadersHSTSBehindProxy(t *testing.T) {
	dummy := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := securityHeaders(dummy)

	cases := []struct {
		name         string
		forwardedPro string
		wantHSTS     bool
	}{
		{"plain http, no proxy header", "", false},
		{"behind TLS-terminating proxy", "https", true},
		{"proxy reports plain http", "http", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "/", nil)
			if tc.forwardedPro != "" {
				req.Header.Set("X-Forwarded-Proto", tc.forwardedPro)
			}
			handler.ServeHTTP(rec, req)

			got := rec.Header().Get("Strict-Transport-Security")
			if tc.wantHSTS && got == "" {
				t.Errorf("expected HSTS to be set, but it was absent")
			}
			if !tc.wantHSTS && got != "" {
				t.Errorf("expected no HSTS over plain HTTP, got %q", got)
			}
		})
	}
}

func TestVersionHandler(t *testing.T) {
	handler := versionHandler("2026-10-23T16:16:20Z", 0)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/version", nil)
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status: got %d, want 200", rec.Code)
	}
	var res map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &res); err != nil {
		t.Fatalf("json unmarshal: %v", err)
	}
	if res["build_sha"] == "" {
		t.Errorf("expected non-empty build_sha, got %v", res["build_sha"])
	}
	if res["build_time"] == "" {
		t.Errorf("expected non-empty build_time, got %v", res["build_time"])
	}
	if res["catalog_fetched_at"] != "2026-10-23T16:16:20Z" {
		t.Errorf("expected catalog_fetched_at to match, got %v", res["catalog_fetched_at"])
	}
}

// TestResolveBuildInfo covers the ways build metadata arrives wrong. The
// placeholder case is the point: "${RENDER_GIT_COMMIT}" is not "dev", so a
// naive check accepts it and /version then presents a literal placeholder as
// the deployed commit — which reads as a real answer and is not one.
func TestResolveBuildInfo(t *testing.T) {
	noEnv := func(string) string { return "" }

	cases := []struct {
		name     string
		sha      string
		ts       string
		env      map[string]string
		wantSHA  string
		wantTime string
	}{
		{
			name: "ldflags injected properly", sha: "abc1234", ts: "2026-10-24",
			env: nil, wantSHA: "abc1234", wantTime: "2026-10-24",
		},
		{
			name: "unsubstituted build arg falls back to env",
			sha:  "${RENDER_GIT_COMMIT}", ts: "${BUILD_TIME}",
			env:     map[string]string{"RENDER_GIT_COMMIT": "deadbee"},
			wantSHA: "deadbee", wantTime: "unknown",
		},
		{
			name: "unsubstituted with no env never reports the placeholder",
			sha:  "${RENDER_GIT_COMMIT}", ts: "${BUILD_TIME}",
			env:     nil,
			wantSHA: "dev", wantTime: "unknown",
		},
		{
			name: "no ldflags, render env present",
			sha:  "dev", ts: "unknown",
			env:     map[string]string{"RENDER_GIT_COMMIT": "cafe123"},
			wantSHA: "cafe123", wantTime: "unknown",
		},
		{
			name: "env itself unsubstituted is rejected",
			sha:  "dev", ts: "unknown",
			env:     map[string]string{"BUILD_SHA": "${RENDER_GIT_COMMIT}"},
			wantSHA: "dev", wantTime: "unknown",
		},
		{
			name: "RENDER_GIT_COMMIT wins over BUILD_SHA",
			sha:  "dev", ts: "unknown",
			env:     map[string]string{"RENDER_GIT_COMMIT": "aaa", "BUILD_SHA": "bbb"},
			wantSHA: "aaa", wantTime: "unknown",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			lookup := noEnv
			if tc.env != nil {
				lookup = func(k string) string { return tc.env[k] }
			}
			gotSHA, gotTime := resolveBuildInfo(tc.sha, tc.ts, lookup)
			if gotSHA != tc.wantSHA {
				t.Errorf("sha: got %q, want %q", gotSHA, tc.wantSHA)
			}
			if gotTime != tc.wantTime {
				t.Errorf("time: got %q, want %q", gotTime, tc.wantTime)
			}
		})
	}
}
