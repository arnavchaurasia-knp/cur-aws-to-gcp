package jobs

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/envmgr"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSystemHealth_KeyMissing_CriticalAndFail(t *testing.T) {
	eng := EngineHealth{Status: "fail"}
	p := EngineProbe{BinaryFound: true, AuthPresent: false, State: "fail", Detail: "No key"}
	issues := buildEngineIssues(eng, p)

	require.NotEmpty(t, issues)
	assert.Equal(t, "engine.key_missing", issues[0].ID)
	assert.Equal(t, "critical", issues[0].Severity)

	status, summary := deriveOverallHealth(issues)
	assert.Equal(t, "fail", status)
	assert.Equal(t, issues[0].Title, summary)
}

func TestSystemHealth_IgnoredKey_InfoWhenTargetSet(t *testing.T) {
	envmgr.ResetForTest()
	t.Setenv("ANTIGRAVITY_API_KEY", "real-api-key-test-12345")
	envmgr.CaptureBaseline()

	sh := SettingsHealth{
		Status:          "ok",
		IgnoredPresent: []envmgr.IgnoredVar{
			{Key: "AGY_API_KEY", UseInstead: "ANTIGRAVITY_API_KEY"},
		},
		NotificationsOn: true,
	}

	issues := buildSettingsIssues(sh, "http://localhost:8080", "false", "", "env", "", true)
	require.NotEmpty(t, issues)

	var found *HealthIssue
	for i := range issues {
		if issues[i].ID == "settings.ignored.AGY_API_KEY" {
			found = &issues[i]
			break
		}
	}
	require.NotNil(t, found)
	assert.Equal(t, "info", found.Severity, "Should be info when ANTIGRAVITY_API_KEY is already set")
}

func TestSystemHealth_StaleCatalog_ActionPointsToAPIKeyWhenMissing(t *testing.T) {
	cat := CatalogHealth{
		Status:         "warn",
		AgeDays:        20,
		StaleAfterDays: 7,
		Stale:          true,
		ArtifactOK:     true,
		APIKeyPresent:  false,
	}

	issues := buildCatalogIssues(cat)
	require.NotEmpty(t, issues)

	var staleIssue *HealthIssue
	for i := range issues {
		if issues[i].ID == "catalog.stale" {
			staleIssue = &issues[i]
			break
		}
	}
	require.NotNil(t, staleIssue)
	require.NotNil(t, staleIssue.Action)
	assert.Equal(t, "edit_env", staleIssue.Action.Kind)
	assert.Equal(t, "GOOGLE_CLOUD_API_KEY", staleIssue.Action.Target)
	assert.Equal(t, "Add pricing API key", staleIssue.Action.Label)
}

func TestSystemHealth_NoIssues_ReturnsOkAndWorking(t *testing.T) {
	status, summary := deriveOverallHealth([]HealthIssue{})
	assert.Equal(t, "ok", status)
	assert.Equal(t, "Everything is working.", summary)
}

func TestSystemHealth_ResponseSchemaVersion(t *testing.T) {
	envmgr.ResetForTest()
	envmgr.CaptureBaseline()

	database, err := db.Open(":memory:")
	require.NoError(t, err)
	defer database.Close()

	tempDir := t.TempDir()
	jobsDir := filepath.Join(tempDir, "jobs")
	_ = os.MkdirAll(jobsDir, 0755)

	spawner := NewSpawner(SpawnerConfig{})
	spawner.SetDB(database)
	handler := NewHandler(database, jobsDir, spawner, nil, NotifyConfig{}, []string{"admin@facets.cloud"})

	r := chi.NewRouter()
	r.Get("/api/admin/health", handler.AdminHealthSummary)

	req := httptest.NewRequest("GET", "/api/admin/health", nil)
	sess := &auth.Session{Email: "admin@facets.cloud", Role: "admin"}
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	var resp map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.Equal(t, float64(3), resp["schema_version"])
	assert.NotEmpty(t, resp["status"])
	assert.NotEmpty(t, resp["summary"])
	assert.Contains(t, resp, "issues")
	assert.Contains(t, resp, "engine")
	assert.Contains(t, resp, "catalog")
	assert.Contains(t, resp, "jobs")
	assert.Contains(t, resp, "data")
	assert.Contains(t, resp, "settings")
	assert.Contains(t, resp, "runtime")
	assert.Contains(t, resp, "server")
}

func TestComponentStatus(t *testing.T) {
	tests := []struct {
		name     string
		base     string
		issues   []HealthIssue
		expected string
	}{
		{
			name:     "no issues + base ok",
			base:     "ok",
			issues:   nil,
			expected: "ok",
		},
		{
			name:     "no issues + base unknown",
			base:     "unknown",
			issues:   nil,
			expected: "unknown",
		},
		{
			name:     "one info + base ok",
			base:     "ok",
			issues:   []HealthIssue{{Severity: "info"}},
			expected: "ok",
		},
		{
			name:     "one warning + base ok",
			base:     "ok",
			issues:   []HealthIssue{{Severity: "warning"}},
			expected: "warn",
		},
		{
			name:     "warning + critical + base ok",
			base:     "ok",
			issues:   []HealthIssue{{Severity: "warning"}, {Severity: "critical"}},
			expected: "fail",
		},
		{
			name:     "info + base unknown",
			base:     "unknown",
			issues:   []HealthIssue{{Severity: "info"}},
			expected: "unknown",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := componentStatus(tt.base, tt.issues)
			assert.Equal(t, tt.expected, got)
		})
	}
}

func TestSettingsHealth_IgnoredVarOnlyIsOK(t *testing.T) {
	envmgr.ResetForTest()
	t.Setenv("AGY_API_KEY", "x")
	t.Setenv("ANTIGRAVITY_API_KEY", "y")
	envmgr.CaptureBaseline()

	sh := SettingsHealth{
		Status: "ok",
		IgnoredPresent: []envmgr.IgnoredVar{
			{Key: "AGY_API_KEY", UseInstead: "ANTIGRAVITY_API_KEY"},
		},
		NotificationsOn: true,
	}

	issues := buildSettingsIssues(sh, "http://localhost:8080", "false", "", "env", "", true)
	status := componentStatus(sh.Status, issues)
	assert.Equal(t, "ok", status)

	var found *HealthIssue
	for i := range issues {
		if issues[i].ID == "settings.ignored.AGY_API_KEY" {
			found = &issues[i]
			break
		}
	}
	require.NotNil(t, found)
	assert.Equal(t, "info", found.Severity)
}

func TestLastMeaningfulLine(t *testing.T) {
	lines := []string{
		"==================================================================",
		"Starting catalog refresh",
		"------------------------------------------------------------------",
		"No GCP credentials (set GOOGLE_CLOUD_API_KEY). Nothing fetched.",
		"==================================================================",
		"   ",
		"",
	}
	got := lastMeaningfulLine(lines)
	assert.Equal(t, "No GCP credentials (set GOOGLE_CLOUD_API_KEY). Nothing fetched.", got)

	// Test long line trimmed to 200 chars + …
	longLine := "This is an extremely long log message that goes on and on and contains a lot of details about why the operation failed or what was going on in the backend script that could exceed the maximum limit of two hundred characters allowed for a single line summary in the health console."
	gotLong := lastMeaningfulLine([]string{longLine})
	assert.True(t, strings.HasSuffix(gotLong, "…"))
	assert.Equal(t, 201, len([]rune(gotLong))) // 200 chars + '…'
}

func TestCatalogRefreshFailed_PlainEnglishNoExitStatus(t *testing.T) {
	cat := CatalogHealth{
		Status:        "warn",
		APIKeyPresent: false,
		LastRun: CatalogRefreshRun{
			State:    "failed",
			ExitCode: 1,
			Reason:   "The refresh script stopped with an error (exit code 1). Last message: No GCP credentials (set GOOGLE_CLOUD_API_KEY). Nothing fetched.",
		},
	}

	issues := buildCatalogIssues(cat)
	require.NotEmpty(t, issues)

	var failIssue *HealthIssue
	for i := range issues {
		if issues[i].ID == "catalog.refresh_failed" {
			failIssue = &issues[i]
			break
		}
	}
	require.NotNil(t, failIssue)
	assert.Contains(t, failIssue.Detail, "no Google Cloud pricing API key")
	assert.NotContains(t, failIssue.Detail, "exit status")
}

func TestDeriveOverallHealth_TipsAndSeverity(t *testing.T) {
	// Only info issues -> status ok, summary starts with "Everything is working."
	infoIssues := []HealthIssue{
		{ID: "tip.1", Severity: "info", Title: "Tip one"},
		{ID: "tip.2", Severity: "info", Title: "Tip two"},
	}
	st, sm := deriveOverallHealth(infoIssues)
	assert.Equal(t, "ok", st)
	assert.Equal(t, "Everything is working. 2 tip(s) below.", sm)

	// One warning + one info -> summary equals warning's title without "(and N more)"
	mixed := []HealthIssue{
		{ID: "warn.1", Severity: "warning", Title: "Catalog is stale"},
		{ID: "tip.1", Severity: "info", Title: "Tip one"},
	}
	st2, sm2 := deriveOverallHealth(mixed)
	assert.Equal(t, "warn", st2)
	assert.Equal(t, "Catalog is stale", sm2)
}

func TestBuildSettingsIssues_AnyDomain(t *testing.T) {
	tests := []struct {
		name           string
		allowedDomains string
		appBaseURL     string
		expectIssue    bool
		expectSeverity string
	}{
		{
			name:           "wildcard on localhost is info",
			allowedDomains: "*",
			appBaseURL:     "http://localhost:8080",
			expectIssue:    true,
			expectSeverity: "info",
		},
		{
			name:           "wildcard on 127.0.0.1 is info",
			allowedDomains: "*",
			appBaseURL:     "http://127.0.0.1:8080",
			expectIssue:    true,
			expectSeverity: "info",
		},
		{
			name:           "wildcard on non-local is critical",
			allowedDomains: "*",
			appBaseURL:     "https://app.example.com",
			expectIssue:    true,
			expectSeverity: "critical",
		},
		{
			name:           "specific domain has no any_domain issue",
			allowedDomains: "facets.cloud",
			appBaseURL:     "https://app.example.com",
			expectIssue:    false,
		},
		{
			name:           "list with wildcard on non-local is critical",
			allowedDomains: "facets.cloud, *",
			appBaseURL:     "https://app.example.com",
			expectIssue:    true,
			expectSeverity: "critical",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sh := SettingsHealth{NotificationsOn: true}
			issues := buildSettingsIssues(sh, tt.appBaseURL, "false", tt.allowedDomains, "env", "", true)
			var anyDomIssue *HealthIssue
			for i := range issues {
				if issues[i].ID == "settings.any_domain" {
					anyDomIssue = &issues[i]
					break
				}
			}
			if tt.expectIssue {
				require.NotNil(t, anyDomIssue)
				assert.Equal(t, tt.expectSeverity, anyDomIssue.Severity)
			} else {
				assert.Nil(t, anyDomIssue)
			}
		})
	}
}
