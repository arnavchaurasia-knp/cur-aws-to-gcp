package jobs

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func ctxWithSession(ctx context.Context, sess *auth.Session) context.Context {
	return context.WithValue(ctx, auth.ExportedCtxKey{}, sess)
}

func setupDataStoreTest(t *testing.T) (*Handler, *db.DB, string, string) {
	t.Helper()
	database, err := db.Open(":memory:")
	require.NoError(t, err)

	tempDir := t.TempDir()
	jobsDir := filepath.Join(tempDir, "jobs")
	_ = os.MkdirAll(jobsDir, 0755)

	skillDir := filepath.Join(tempDir, "skill")
	dataDir := filepath.Join(skillDir, "data")
	_ = os.MkdirAll(dataDir, 0755)

	// Set SKILL_DIR for the test
	t.Setenv("SKILL_DIR", skillDir)

	// Seed an editable datastore
	initialReviewConfig := `{"catalog_refresh_after_days": 14, "outlier_threshold": 3.0}`
	require.NoError(t, os.WriteFile(filepath.Join(dataDir, "review-config.json"), []byte(initialReviewConfig), 0644))

	spawner := NewSpawner(SpawnerConfig{})
	spawner.SetDB(database)
	handler := NewHandler(database, jobsDir, spawner, nil, NotifyConfig{}, []string{"admin@facets.cloud"})

	return handler, database, skillDir, dataDir
}

func TestAdminDataStore_ReadAndList(t *testing.T) {
	handler, database, _, _ := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Get("/api/admin/datastores", handler.AdminListDataStores)
	r.Get("/api/admin/datastores/{key}", handler.AdminGetDataStore)

	// 1. List
	req := httptest.NewRequest("GET", "/api/admin/datastores", nil)
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	var listRes struct {
		Stores     []StoreHealth `json:"stores"`
		DataStatus string        `json:"data_status"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &listRes))
	assert.NotEmpty(t, listRes.Stores)

	// Verify review_config is marked editable
	foundReviewConfig := false
	for _, s := range listRes.Stores {
		if s.Key == "review_config" {
			foundReviewConfig = true
			assert.True(t, s.Editable)
		}
	}
	assert.True(t, foundReviewConfig)

	// 2. Get specific store
	req = httptest.NewRequest("GET", "/api/admin/datastores/review_config", nil)
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w = httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	var detail DataStoreDetailDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &detail))
	assert.Equal(t, "review_config", detail.Key)
	assert.True(t, detail.Editable)
	assert.Contains(t, detail.Content, "catalog_refresh_after_days")
}

func TestAdminDataStore_WriteValidWithBackupAndAudit(t *testing.T) {
	handler, database, _, dataDir := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)

	newContent := `{"catalog_refresh_after_days": 21, "outlier_threshold": 4.5}`
	body := bytes.NewBufferString(newContent)
	req := httptest.NewRequest("PUT", "/api/admin/datastores/review_config", body)
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	// Assert updated content on disk
	diskContent, err := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	require.NoError(t, err)
	assert.Contains(t, string(diskContent), `"catalog_refresh_after_days": 21`)

	// Assert backup was created in dataDir/_backups
	backupDir := filepath.Join(dataDir, "_backups")
	entries, err := os.ReadDir(backupDir)
	require.NoError(t, err)
	assert.NotEmpty(t, entries)
	assert.Contains(t, entries[0].Name(), "review_config.")

	// Assert audit log was recorded
	audit, err := database.ListAuditLog(10, 0, "admin@facets.cloud", "datastore_edit", nil, nil)
	require.NoError(t, err)
	assert.Len(t, audit, 1)
	assert.Equal(t, "review_config", audit[0].TargetID)
}

func TestAdminDataStore_WriteInvalidJSON_Rejected(t *testing.T) {
	handler, database, _, dataDir := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)

	originalBytes, err := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	require.NoError(t, err)

	invalidContent := `{not valid json: true`
	req := httptest.NewRequest("PUT", "/api/admin/datastores/review_config", bytes.NewBufferString(invalidContent))
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	assert.Equal(t, http.StatusBadRequest, w.Code)
	assert.Contains(t, w.Body.String(), "invalid JSON")

	// Ensure file on disk was untouched
	afterBytes, err := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	require.NoError(t, err)
	assert.Equal(t, originalBytes, afterBytes)
}

func TestAdminDataStore_RejectNonEditableAndPathTraversal(t *testing.T) {
	handler, database, _, _ := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)

	// Non-editable store
	req := httptest.NewRequest("PUT", "/api/admin/datastores/catalog_artifact", bytes.NewBufferString(`{}`))
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusBadRequest, w.Code)
	assert.Contains(t, w.Body.String(), "read-only or unrecognized")

	// Path traversal key
	req = httptest.NewRequest("PUT", "/api/admin/datastores/..%2Fetc%2Fpasswd", bytes.NewBufferString(`{}`))
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w = httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusBadRequest, w.Code)
}

func TestAdminDataStore_Revert(t *testing.T) {
	handler, database, _, dataDir := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)
	r.Post("/api/admin/datastores/{key}/revert", handler.AdminRevertDataStore)

	// Step 1: Write an edit (which generates a backup of original)
	v2 := `{"catalog_refresh_after_days": 30, "outlier_threshold": 5.0}`
	req := httptest.NewRequest("PUT", "/api/admin/datastores/review_config", bytes.NewBufferString(v2))
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)

	// Confirm v2 on disk
	diskV2, _ := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	assert.Contains(t, string(diskV2), `"catalog_refresh_after_days": 30`)

	// Step 2: Revert to backup
	req = httptest.NewRequest("POST", "/api/admin/datastores/review_config/revert", nil)
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w = httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	// Confirm restored back to original v1 (14 days)
	diskRestored, _ := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	assert.Contains(t, string(diskRestored), `"catalog_refresh_after_days": 14`)
}
