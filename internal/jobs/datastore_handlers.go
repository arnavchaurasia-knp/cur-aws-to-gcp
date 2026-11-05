package jobs

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/skill"
	"github.com/go-chi/chi/v5"
)

var editableDatastores = map[string]string{
	"service_map":         "service_map.json",
	"service_specs":       "service_specs.json",
	"pricing_matrix":      "pricing_matrix.json",
	"fx_rates":            "fx_rates.json",
	"review_config":       "review-config.json",
	"refresh_store":       "refresh_store.json",
	"assumption_defaults": "assumption-defaults.json",
}

// DataStoreDetailDTO is returned for GET /api/admin/datastores/{key}
type DataStoreDetailDTO struct {
	Key          string      `json:"key"`
	File         string      `json:"file"`
	Editable     bool        `json:"editable"`
	IsDBBacked   bool        `json:"is_db_backed"`
	ManageURL    string      `json:"manage_url,omitempty"`
	SizeBytes    int64       `json:"size_bytes"`
	AgeDays      int         `json:"age_days"`
	Status       string      `json:"status"`
	Detail       string      `json:"detail"`
	Count        int         `json:"count"`
	Content      string      `json:"content,omitempty"`
	LastBackupAt string      `json:"last_backup_at,omitempty"`
	BackupCount  int         `json:"backup_count"`
}

// pruneBackups keeps at most maxKeep backups for a given store key.
func pruneBackups(backupDir, key string, maxKeep int) {
	entries, err := os.ReadDir(backupDir)
	if err != nil {
		return
	}
	prefix := key + "."
	var matches []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), prefix) && strings.HasSuffix(e.Name(), ".json") {
			matches = append(matches, e.Name())
		}
	}
	if len(matches) <= maxKeep {
		return
	}
	sort.Strings(matches) // oldest first
	toRemove := len(matches) - maxKeep
	for i := 0; i < toRemove; i++ {
		_ = os.Remove(filepath.Join(backupDir, matches[i]))
	}
}

// latestBackupFor finds the most recent backup file for key.
func latestBackupFor(backupDir, key string) (string, time.Time, int) {
	entries, err := os.ReadDir(backupDir)
	if err != nil {
		return "", time.Time{}, 0
	}
	prefix := key + "."
	var matches []os.DirEntry
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), prefix) && strings.HasSuffix(e.Name(), ".json") {
			matches = append(matches, e)
		}
	}
	if len(matches) == 0 {
		return "", time.Time{}, 0
	}
	// Sort by mod time descending
	sort.Slice(matches, func(i, j int) bool {
		iInfo, _ := matches[i].Info()
		jInfo, _ := matches[j].Info()
		if iInfo == nil || jInfo == nil {
			return matches[i].Name() > matches[j].Name()
		}
		return iInfo.ModTime().After(jInfo.ModTime())
	})
	latest := matches[0]
	info, _ := latest.Info()
	var modTime time.Time
	if info != nil {
		modTime = info.ModTime()
	}
	return filepath.Join(backupDir, latest.Name()), modTime, len(matches)
}

// AdminListDataStores returns all managed file-backed and DB-backed datastores.
func (h *Handler) AdminListDataStores(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	skillDir := skill.ResolveDir()
	stores, dataStatus := CheckDataStores(skillDir)

	// Enrich with DB-backed config summaries
	var allStores []StoreHealth
	allStores = append(allStores, stores...)

	// 1. Assumptions (DB-backed)
	assumpCount := 0
	if assumptions, err := h.db.GetAssumptions(); err == nil {
		assumpCount = len(assumptions)
	}
	allStores = append(allStores, StoreHealth{
		Key:        "db_assumptions",
		File:       "SQLite: assumptions",
		Status:     "ok",
		Detail:     fmt.Sprintf("%d active baseline assumption entries", assumpCount),
		Count:      assumpCount,
		Editable:   false,
		IsDBBacked: true,
		ManageURL:  "/admin/assumptions",
	})

	// 2. Report Settings (DB-backed)
	reportSettingsCount := 0
	if settings, err := h.db.GetReportSettings(); err == nil {
		reportSettingsCount = len(settings)
	}
	allStores = append(allStores, StoreHealth{
		Key:        "db_report_settings",
		File:       "SQLite: report_settings",
		Status:     "ok",
		Detail:     fmt.Sprintf("%d system & quota parameters", reportSettingsCount),
		Count:      reportSettingsCount,
		Editable:   false,
		IsDBBacked: true,
		ManageURL:  "/admin/report-settings",
	})

	// 3. Integrations (DB-backed)
	allStores = append(allStores, StoreHealth{
		Key:        "db_integrations",
		File:       "SQLite: integration_settings",
		Status:     "ok",
		Detail:     "Outbound notification channels (Email & Slack)",
		Count:      2,
		Editable:   false,
		IsDBBacked: true,
		ManageURL:  "/admin/integrations",
	})

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"stores":      allStores,
		"data_status": dataStatus,
	})
}

// AdminGetDataStore retrieves the content and metadata of a specific datastore.
func (h *Handler) AdminGetDataStore(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	key := chi.URLParam(r, "key")
	skillDir := skill.ResolveDir()
	dataDir := filepath.Join(skillDir, "data")
	backupDir := filepath.Join(dataDir, "_backups")

	// Check DB-backed stores
	switch key {
	case "db_assumptions":
		count := 0
		if assump, err := h.db.GetAssumptions(); err == nil {
			count = len(assump)
		}
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(DataStoreDetailDTO{
			Key:        key,
			File:       "SQLite: assumptions",
			Editable:   false,
			IsDBBacked: true,
			ManageURL:  "/admin/assumptions",
			Status:     "ok",
			Detail:     "DB-backed assumptions table",
			Count:      count,
		})
		return
	case "db_report_settings":
		count := 0
		if settings, err := h.db.GetReportSettings(); err == nil {
			count = len(settings)
		}
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(DataStoreDetailDTO{
			Key:        key,
			File:       "SQLite: report_settings",
			Editable:   false,
			IsDBBacked: true,
			ManageURL:  "/admin/report-settings",
			Status:     "ok",
			Detail:     "DB-backed report and quota settings",
			Count:      count,
		})
		return
	case "db_integrations":
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(DataStoreDetailDTO{
			Key:        key,
			File:       "SQLite: integration_settings",
			Editable:   false,
			IsDBBacked: true,
			ManageURL:  "/admin/integrations",
			Status:     "ok",
			Detail:     "DB-backed integration settings",
			Count:      2,
		})
		return
	case "catalog_artifact":
		info, _ := os.Stat(filepath.Join(dataDir, "catalog.duckdb"))
		var size int64
		if info != nil {
			size = info.Size()
		}
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(DataStoreDetailDTO{
			Key:       key,
			File:      "catalog.duckdb",
			Editable:  false,
			SizeBytes: size,
			Status:    "ok",
			Detail:    "Binary DuckDB catalog artifact; managed via catalog refresh",
		})
		return
	case "catalog_source":
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(DataStoreDetailDTO{
			Key:      key,
			File:     "skus/*.json.gz",
			Editable: false,
			Status:   "ok",
			Detail:   "Compressed SKU archives; managed via catalog refresh",
		})
		return
	}

	fileName, ok := editableDatastores[key]
	if !ok {
		http.Error(w, `{"error":"unrecognized datastore key"}`, http.StatusNotFound)
		return
	}

	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))
	cleanDataDir := filepath.Clean(dataDir)
	if !strings.HasPrefix(fullPath, cleanDataDir+string(filepath.Separator)) {
		http.Error(w, `{"error":"path traversal rejected"}`, http.StatusBadRequest)
		return
	}

	info, err := os.Stat(fullPath)
	if err != nil {
		if os.IsNotExist(err) {
			http.Error(w, `{"error":"datastore file does not exist"}`, http.StatusNotFound)
			return
		}
		http.Error(w, `{"error":"failed to stat datastore file: `+jsonEscape(err.Error())+`"}`, http.StatusInternalServerError)
		return
	}

	if info.Size() > 2*1024*1024 {
		http.Error(w, `{"error":"file exceeds 2MB viewing limit"}`, http.StatusRequestEntityTooLarge)
		return
	}

	contentBytes, err := os.ReadFile(fullPath)
	if err != nil {
		http.Error(w, `{"error":"failed to read datastore file: `+jsonEscape(err.Error())+`"}`, http.StatusInternalServerError)
		return
	}

	// Format pretty JSON if valid
	var parsed any
	var formattedContent string
	if err := json.Unmarshal(contentBytes, &parsed); err == nil {
		if pretty, err := json.MarshalIndent(parsed, "", "  "); err == nil {
			formattedContent = string(pretty)
		} else {
			formattedContent = string(contentBytes)
		}
	} else {
		formattedContent = string(contentBytes)
	}

	// Item count
	count := 0
	switch v := parsed.(type) {
	case map[string]any:
		count = len(v)
	case []any:
		count = len(v)
	}

	// Backup metadata
	_, latestMod, backupCount := latestBackupFor(backupDir, key)
	lastBackupAt := ""
	if !latestMod.IsZero() {
		lastBackupAt = latestMod.UTC().Format(time.RFC3339)
	}

	ageDays := int(time.Since(info.ModTime()).Hours() / 24)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(DataStoreDetailDTO{
		Key:          key,
		File:         fileName,
		Editable:     true,
		SizeBytes:    info.Size(),
		AgeDays:      ageDays,
		Status:       "ok",
		Detail:       fmt.Sprintf("valid (%d items)", count),
		Count:        count,
		Content:      formattedContent,
		LastBackupAt: lastBackupAt,
		BackupCount:  backupCount,
	})
}

// AdminPutDataStore updates the content of an editable datastore with guardrails.
func (h *Handler) AdminPutDataStore(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	key := chi.URLParam(r, "key")
	fileName, ok := editableDatastores[key]
	if !ok {
		http.Error(w, `{"error":"datastore is read-only or unrecognized"}`, http.StatusBadRequest)
		return
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		http.Error(w, `{"error":"skill data is baked into container image layer; runtime modifications cannot persist across redeploys"}`, http.StatusBadRequest)
		return
	}

	dataDir := filepath.Join(skillDir, "data")
	cleanDataDir := filepath.Clean(dataDir)
	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))
	if !strings.HasPrefix(fullPath, cleanDataDir+string(filepath.Separator)) {
		http.Error(w, `{"error":"path traversal rejected"}`, http.StatusBadRequest)
		return
	}

	// 1. Read request body (cap at 2MB)
	bodyBytes, err := io.ReadAll(io.LimitReader(r.Body, 2*1024*1024))
	if err != nil {
		http.Error(w, `{"error":"failed to read body"}`, http.StatusBadRequest)
		return
	}

	// If body was wrapped in JSON struct e.g. {"content": "..."}
	var wrapper struct {
		Content string `json:"content"`
	}
	rawJSON := bodyBytes
	if err := json.Unmarshal(bodyBytes, &wrapper); err == nil && wrapper.Content != "" {
		rawJSON = []byte(wrapper.Content)
	}

	// 2. Validate JSON structure & light schema check
	var parsed any
	if err := json.Unmarshal(rawJSON, &parsed); err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"invalid JSON: %s"}`, jsonEscape(err.Error())), http.StatusBadRequest)
		return
	}

	// Light schema/sanity checks
	switch key {
	case "service_map", "review_config", "fx_rates":
		if _, ok := parsed.(map[string]any); !ok {
			http.Error(w, fmt.Sprintf(`{"error":"%s must be a top-level JSON object"}`, fileName), http.StatusBadRequest)
			return
		}
	case "pricing_matrix", "service_specs", "assumption_defaults":
		if _, isMap := parsed.(map[string]any); !isMap {
			if _, isSlice := parsed.([]any); !isSlice {
				http.Error(w, fmt.Sprintf(`{"error":"%s must be a JSON object or array"}`, fileName), http.StatusBadRequest)
				return
			}
		}
	}

	formattedJSON, err := json.MarshalIndent(parsed, "", "  ")
	if err != nil {
		http.Error(w, `{"error":"failed to serialize JSON"}`, http.StatusInternalServerError)
		return
	}
	formattedJSON = append(formattedJSON, '\n')

	// 3. Backup current file
	backupDir := filepath.Join(dataDir, "_backups")
	_ = os.MkdirAll(backupDir, 0755)

	var oldSize int64
	if currentData, err := os.ReadFile(fullPath); err == nil {
		oldSize = int64(len(currentData))
		// Format timestamp with hyphens to be legal across both Windows and Unix
		ts := time.Now().UTC().Format("2006-01-02T15-04-05Z07-00")
		backupPath := filepath.Join(backupDir, fmt.Sprintf("%s.%s.json", key, ts))
		if bErr := os.WriteFile(backupPath, currentData, 0644); bErr != nil {
			slog.Warn("failed to create backup before datastore edit", "key", key, "err", bErr)
		} else {
			pruneBackups(backupDir, key, 10)
		}
	}

	// 4. Atomic write via temp file + rename (Windows safe)
	tmp, err := os.CreateTemp(dataDir, fmt.Sprintf("%s_*.tmp", key))
	if err != nil {
		http.Error(w, `{"error":"failed to create temp file: `+jsonEscape(err.Error())+`"}`, http.StatusInternalServerError)
		return
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(formattedJSON); err != nil {
		tmp.Close()
		_ = os.Remove(tmpName)
		http.Error(w, `{"error":"failed to write temp file"}`, http.StatusInternalServerError)
		return
	}
	_ = tmp.Close()

	_ = os.Remove(fullPath) // Remove existing target for Windows os.Rename compatibility
	if err := os.Rename(tmpName, fullPath); err != nil {
		// Fallback direct write
		if wErr := os.WriteFile(fullPath, formattedJSON, 0644); wErr != nil {
			_ = os.Remove(tmpName)
			http.Error(w, `{"error":"failed to replace datastore file: `+jsonEscape(err.Error())+`"}`, http.StatusInternalServerError)
			return
		}
		_ = os.Remove(tmpName)
	}

	// 5. Audit
	sizeDelta := len(formattedJSON) - int(oldSize)
	_ = h.db.LogAdminAction(sess.Email, "datastore_edit", key, fmt.Sprintf("size_delta=%d bytes", sizeDelta))

	// 6. Return refreshed StoreHealth
	stores, _ := CheckDataStores(skillDir)
	var updatedStore StoreHealth
	for _, s := range stores {
		if s.Key == key {
			updatedStore = s
			break
		}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"message": "datastore updated successfully",
		"store":   updatedStore,
	})
}

// AdminRevertDataStore restores the most recent backup for an editable datastore.
func (h *Handler) AdminRevertDataStore(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	key := chi.URLParam(r, "key")
	fileName, ok := editableDatastores[key]
	if !ok {
		http.Error(w, `{"error":"datastore is read-only or unrecognized"}`, http.StatusBadRequest)
		return
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		http.Error(w, `{"error":"skill data is baked into container image layer; runtime modifications cannot persist across redeploys"}`, http.StatusBadRequest)
		return
	}

	dataDir := filepath.Join(skillDir, "data")
	backupDir := filepath.Join(dataDir, "_backups")

	latestBackup, modTime, _ := latestBackupFor(backupDir, key)
	if latestBackup == "" {
		http.Error(w, `{"error":"no backups found to revert for this store"}`, http.StatusNotFound)
		return
	}

	backupData, err := os.ReadFile(latestBackup)
	if err != nil {
		http.Error(w, `{"error":"failed to read backup file: `+jsonEscape(err.Error())+`"}`, http.StatusInternalServerError)
		return
	}

	var parsed any
	if err := json.Unmarshal(backupData, &parsed); err != nil {
		http.Error(w, `{"error":"backup contains corrupt JSON"}`, http.StatusInternalServerError)
		return
	}

	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))

	// Backup the current file before restoring
	if currentData, err := os.ReadFile(fullPath); err == nil && !bytes.Equal(currentData, backupData) {
		ts := time.Now().UTC().Format("2006-01-02T15-04-05Z07-00")
		preRevertBackup := filepath.Join(backupDir, fmt.Sprintf("%s.%s.pre-revert.json", key, ts))
		_ = os.WriteFile(preRevertBackup, currentData, 0644)
	}

	// Atomic write backupData to fullPath
	tmp, err := os.CreateTemp(dataDir, fmt.Sprintf("%s_revert_*.tmp", key))
	if err != nil {
		http.Error(w, `{"error":"failed to create temp file"}`, http.StatusInternalServerError)
		return
	}
	tmpName := tmp.Name()
	_, _ = tmp.Write(backupData)
	_ = tmp.Close()

	_ = os.Remove(fullPath)
	if err := os.Rename(tmpName, fullPath); err != nil {
		_ = os.WriteFile(fullPath, backupData, 0644)
		_ = os.Remove(tmpName)
	}

	// Audit
	_ = h.db.LogAdminAction(sess.Email, "datastore_revert", key, fmt.Sprintf("restored from %s (timestamp: %s)", filepath.Base(latestBackup), modTime.UTC().Format(time.RFC3339)))

	// Return updated store
	stores, _ := CheckDataStores(skillDir)
	var updatedStore StoreHealth
	for _, s := range stores {
		if s.Key == key {
			updatedStore = s
			break
		}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"message": fmt.Sprintf("reverted to backup from %s", modTime.UTC().Format(time.RFC3339)),
		"store":   updatedStore,
	})
}
