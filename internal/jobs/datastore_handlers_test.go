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

	diskContent, err := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	require.NoError(t, err)
	assert.Contains(t, string(diskContent), `"catalog_refresh_after_days": 21`)

	backupDir := filepath.Join(dataDir, "_backups")
	entries, err := os.ReadDir(backupDir)
	require.NoError(t, err)
	assert.NotEmpty(t, entries)
	assert.Contains(t, entries[0].Name(), "review_config.")

	audit, err := database.ListAuditLog(10, 0, "admin@facets.cloud", "datastore_edit", nil, nil)
	require.NoError(t, err)
	assert.Len(t, audit, 1)
	assert.Equal(t, "review_config", audit[0].TargetID)
}

func TestAdminDataStore_SavePreservesKeyOrderAndFormatting(t *testing.T) {
	handler, database, _, dataDir := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Get("/api/admin/datastores/{key}", handler.AdminGetDataStore)
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)

	// Seed custom indentation and key order: zeta before alpha, with comments
	customJSON := "{\n    \"zeta\": 1,\n    \"_comment_a\": \"x\",\n    \"alpha\": 2\n}\n"
	require.NoError(t, os.WriteFile(filepath.Join(dataDir, "review-config.json"), []byte(customJSON), 0644))

	// 1. GET returns exact raw file
	reqGet := httptest.NewRequest("GET", "/api/admin/datastores/review_config", nil)
	reqGet = reqGet.WithContext(ctxWithSession(reqGet.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wGet := httptest.NewRecorder()
	r.ServeHTTP(wGet, reqGet)
	assert.Equal(t, http.StatusOK, wGet.Code)

	var detail DataStoreDetailDTO
	require.NoError(t, json.Unmarshal(wGet.Body.Bytes(), &detail))
	assert.Equal(t, customJSON, detail.Content)

	// 2. PUT with zeta changed to 9 — sent raw
	updatedJSON := "{\n    \"zeta\": 9,\n    \"_comment_a\": \"x\",\n    \"alpha\": 2\n}\n"
	reqPut := httptest.NewRequest("PUT", "/api/admin/datastores/review_config", bytes.NewBufferString(updatedJSON))
	reqPut = reqPut.WithContext(ctxWithSession(reqPut.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wPut := httptest.NewRecorder()
	r.ServeHTTP(wPut, reqPut)
	assert.Equal(t, http.StatusOK, wPut.Code)

	// Assert on-disk content matches byte-for-byte, preserving zeta before alpha
	diskBytes, err := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	require.NoError(t, err)
	assert.Equal(t, updatedJSON, string(diskBytes))
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
	assert.Contains(t, w.Body.String(), "isn't valid JSON")

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
	assert.Contains(t, w.Body.String(), "This file is read-only here.")

	// Path traversal key
	req = httptest.NewRequest("PUT", "/api/admin/datastores/..%2Fetc%2Fpasswd", bytes.NewBufferString(`{}`))
	req = req.WithContext(ctxWithSession(req.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	w = httptest.NewRecorder()
	r.ServeHTTP(w, req)
	assert.Equal(t, http.StatusBadRequest, w.Code)
}

func TestAdminDataStore_BackupsListAndRestore(t *testing.T) {
	handler, database, _, dataDir := setupDataStoreTest(t)
	defer database.Close()

	r := chi.NewRouter()
	r.Put("/api/admin/datastores/{key}", handler.AdminPutDataStore)
	r.Get("/api/admin/datastores/{key}/backups", handler.AdminListDataStoreBackups)
	r.Post("/api/admin/datastores/{key}/restore", handler.AdminRestoreDataStoreBackup)

	// Step 1: Write an edit (generates backup of original 14 days)
	v2 := "{\n  \"catalog_refresh_after_days\": 45\n}\n"
	reqPut := httptest.NewRequest("PUT", "/api/admin/datastores/review_config", bytes.NewBufferString(v2))
	reqPut = reqPut.WithContext(ctxWithSession(reqPut.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wPut := httptest.NewRecorder()
	r.ServeHTTP(wPut, reqPut)
	require.Equal(t, http.StatusOK, wPut.Code)

	// Step 2: List backups
	reqList := httptest.NewRequest("GET", "/api/admin/datastores/review_config/backups", nil)
	reqList = reqList.WithContext(ctxWithSession(reqList.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wList := httptest.NewRecorder()
	r.ServeHTTP(wList, reqList)
	assert.Equal(t, http.StatusOK, wList.Code)

	var bList struct {
		Backups []BackupInfo `json:"backups"`
	}
	require.NoError(t, json.Unmarshal(wList.Body.Bytes(), &bList))
	require.NotEmpty(t, bList.Backups)
	backupName := bList.Backups[0].Name
	assert.Contains(t, backupName, "review_config.")
	assert.Equal(t, "before-edit", bList.Backups[0].Kind)

	// Step 3: Reject path traversal in restore
	badReq := httptest.NewRequest("POST", "/api/admin/datastores/review_config/restore", bytes.NewBufferString(`{"name":"../secret.json"}`))
	badReq = badReq.WithContext(ctxWithSession(badReq.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wBad := httptest.NewRecorder()
	r.ServeHTTP(wBad, badReq)
	assert.Equal(t, http.StatusBadRequest, wBad.Code)

	// Step 4: Reject restore with a backup name for another key
	otherKeyReq := httptest.NewRequest("POST", "/api/admin/datastores/review_config/restore", bytes.NewBufferString(`{"name":"service_map.2026.json"}`))
	otherKeyReq = otherKeyReq.WithContext(ctxWithSession(otherKeyReq.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wOther := httptest.NewRecorder()
	r.ServeHTTP(wOther, otherKeyReq)
	assert.Equal(t, http.StatusBadRequest, wOther.Code)

	// Step 5: Restore valid backup
	bodyRestore, _ := json.Marshal(map[string]string{"name": backupName})
	reqRestore := httptest.NewRequest("POST", "/api/admin/datastores/review_config/restore", bytes.NewReader(bodyRestore))
	reqRestore = reqRestore.WithContext(ctxWithSession(reqRestore.Context(), &auth.Session{Email: "admin@facets.cloud", Role: "admin"}))
	wRestore := httptest.NewRecorder()
	r.ServeHTTP(wRestore, reqRestore)
	assert.Equal(t, http.StatusOK, wRestore.Code)

	// Confirm restored back to original v1 (14 days)
	diskRestored, _ := os.ReadFile(filepath.Join(dataDir, "review-config.json"))
	assert.Contains(t, string(diskRestored), `"catalog_refresh_after_days": 14`)

	// Assert audit log for restore
	audit, err := database.ListAuditLog(10, 0, "admin@facets.cloud", "datastore_restore", nil, nil)
	require.NoError(t, err)
	assert.Len(t, audit, 1)
	assert.Equal(t, "review_config", audit[0].TargetID)
	assert.Equal(t, backupName, audit[0].Details)
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
