package jobs

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

	issues := buildSettingsIssues(sh, "http://localhost:8080", "false", "env", "", true)
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
