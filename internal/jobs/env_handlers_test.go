package jobs

import (
	"bytes"
	"encoding/base64"
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

func setupEnvTest(t *testing.T) (*Handler, *db.DB, *chi.Mux) {
	t.Helper()
	envmgr.ResetForTest()
	validKey := base64.StdEncoding.EncodeToString(make([]byte, 32))
	t.Setenv("SETTINGS_ENCRYPTION_KEY", validKey)
	envmgr.CaptureBaseline()

	database, err := db.Open(":memory:")
	require.NoError(t, err)

	tempDir := t.TempDir()
	jobsDir := filepath.Join(tempDir, "jobs")
	_ = os.MkdirAll(jobsDir, 0755)

	spawner := NewSpawner(SpawnerConfig{})
	spawner.SetDB(database)
	handler := NewHandler(database, jobsDir, spawner, nil, NotifyConfig{}, []string{"admin@facets.cloud"})

	r := chi.NewRouter()
	r.Get("/api/admin/env", handler.AdminListEnv)
	r.Put("/api/admin/env/{key}", handler.AdminSetEnv)
	r.Delete("/api/admin/env/{key}", handler.AdminClearEnv)
	r.Post("/api/admin/env/{key}/test", handler.AdminTestEnv)

	return handler, database, r
}

func TestAdminListEnv_NeverLeaksSecrets(t *testing.T) {
	secretVal := "my-ultra-secret-key-12345"
	t.Setenv("ANTIGRAVITY_API_KEY", secretVal)
	t.Setenv("AGY_API_KEY", "some-ignored-key-val")

	_, database, router := setupEnvTest(t)
	defer database.Close()

	req := httptest.NewRequest("GET", "/api/admin/env", nil)
	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)

	body := rec.Body.String()
	assert.NotContains(t, body, secretVal)
	assert.Contains(t, body, "••••2345")

	var resp envListResponse
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &resp))
	assert.True(t, resp.Encryption.Available)

	foundIgnored := false
	for _, iv := range resp.IgnoredPresent {
		if iv.Key == "AGY_API_KEY" {
			foundIgnored = true
			assert.Equal(t, "ANTIGRAVITY_API_KEY", iv.UseInstead)
		}
	}
	assert.True(t, foundIgnored, "AGY_API_KEY should appear in ignored_present")
}

func TestAdminSetEnv_LogLevel(t *testing.T) {
	_, database, router := setupEnvTest(t)
	defer database.Close()

	bodyBytes, _ := json.Marshal(map[string]string{"value": "debug"})
	req := httptest.NewRequest("PUT", "/api/admin/env/LOG_LEVEL", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "debug", os.Getenv("LOG_LEVEL"))

	// Check audit log: env_set with empty details
	entries, err := database.ListAuditLog(10, 0, "", "", nil, nil)
	require.NoError(t, err)
	require.NotEmpty(t, entries)
	assert.Equal(t, "env_set", entries[0].Action)
	assert.Equal(t, "LOG_LEVEL", entries[0].TargetID)
	assert.Empty(t, entries[0].Details)
}

func TestAdminSetEnv_DeploymentAndUnknown(t *testing.T) {
	_, database, router := setupEnvTest(t)
	defer database.Close()

	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}

	// 1. Deployment var (SESSION_SECRET) -> 400
	bodyBytes, _ := json.Marshal(map[string]string{"value": "something"})
	req := httptest.NewRequest("PUT", "/api/admin/env/SESSION_SECRET", bytes.NewReader(bodyBytes))
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusBadRequest, rec.Code)

	// 2. Unknown var (PATH) -> 404
	req = httptest.NewRequest("PUT", "/api/admin/env/PATH", bytes.NewReader(bodyBytes))
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusNotFound, rec.Code)
}

func TestAdminSetEnv_NoEncryptionKey(t *testing.T) {
	t.Setenv("SETTINGS_ENCRYPTION_KEY", "")
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
	r.Put("/api/admin/env/{key}", handler.AdminSetEnv)

	bodyBytes, _ := json.Marshal(map[string]string{"value": "valid-secret-key-12345"})
	req := httptest.NewRequest("PUT", "/api/admin/env/ANTIGRAVITY_API_KEY", bytes.NewReader(bodyBytes))
	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()

	r.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusConflict, rec.Code)
	assert.Contains(t, rec.Body.String(), "Secrets can't be saved yet")
}

func TestAdminClearEnv_RestoresStartupValue(t *testing.T) {
	t.Setenv("LOG_LEVEL", "info")
	_, database, router := setupEnvTest(t)
	defer database.Close()

	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}

	// Override to debug
	bodyBytes, _ := json.Marshal(map[string]string{"value": "debug"})
	req := httptest.NewRequest("PUT", "/api/admin/env/LOG_LEVEL", bytes.NewReader(bodyBytes))
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "debug", os.Getenv("LOG_LEVEL"))

	// Clear override
	req = httptest.NewRequest("DELETE", "/api/admin/env/LOG_LEVEL", nil)
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, "info", os.Getenv("LOG_LEVEL"))
}

func TestAdminTestEnv_GoogleCloudAPIKey(t *testing.T) {
	var receivedKey string
	var receivedURL string

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedKey = r.Header.Get("X-Goog-Api-Key")
		receivedURL = r.URL.String()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"services":[]}`))
	}))
	defer ts.Close()

	origURL := cloudBillingBaseURL
	cloudBillingBaseURL = ts.URL
	defer func() { cloudBillingBaseURL = origURL }()

	testKey := "AIzaSyTestKey1234567890"
	t.Setenv("GOOGLE_CLOUD_API_KEY", testKey)

	_, database, router := setupEnvTest(t)
	defer database.Close()

	sess := &auth.Session{Email: "admin@facets.cloud", Name: "Admin"}
	req := httptest.NewRequest("POST", "/api/admin/env/GOOGLE_CLOUD_API_KEY/test", nil)
	req = req.WithContext(ctxWithSession(req.Context(), sess))
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	assert.Equal(t, testKey, receivedKey)
	assert.NotContains(t, receivedURL, testKey)

	var res map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &res))
	assert.Equal(t, true, res["ok"])
	assert.Equal(t, "ok", res["status"])
}
