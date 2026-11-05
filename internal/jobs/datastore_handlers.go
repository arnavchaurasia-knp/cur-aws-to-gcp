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
	Key          string `json:"key"`
	Title        string `json:"title,omitempty"`
	Description  string `json:"description,omitempty"`
	File         string `json:"file"`
	Editable     bool   `json:"editable"`
	IsDBBacked   bool   `json:"is_db_backed"`
	ManageURL    string `json:"manage_url,omitempty"`
	SizeBytes    int64  `json:"size_bytes"`
	AgeDays      int    `json:"age_days"`
	Status       string `json:"status"`
	Detail       string `json:"detail"`
	Count        int    `json:"count"`
	Content      string `json:"content,omitempty"`
	LastBackupAt string `json:"last_backup_at,omitempty"`
	BackupCount  int    `json:"backup_count"`
}

// BackupInfo describes a single datastore backup.
type BackupInfo struct {
	Name      string `json:"name"`
	CreatedAt string `json:"created_at"`
	SizeBytes int64  `json:"size_bytes"`
	Kind      string `json:"kind"` // "pre-revert" | "pre-restore" | "before-edit"
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
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	skillDir := skill.ResolveDir()
	stores, dataStatus := CheckDataStores(skillDir)

	var allStores []StoreHealth
	allStores = append(allStores, stores...)

	// 1. Assumptions (DB-backed)
	assumpCount := 0
	if assumptions, err := h.db.GetAssumptions(); err == nil {
		assumpCount = len(assumptions)
	}
	allStores = append(allStores, StoreHealth{
		Key:        "db_assumptions",
		Title:      "Pricing Assumptions",
		Description: "Baseline sizing overrides stored in SQLite.",
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
		Title:      "Report & Quota Settings",
		Description: "Engine defaults and concurrent job limits stored in SQLite.",
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
		Title:      "Notification Integrations",
		Description: "Email and Slack channels stored in SQLite.",
		File:       "SQLite: integration_settings",
		Status:     "ok",
		Detail:     "Outbound notification channels (Email & Slack)",
		Count:      2,
		Editable:   false,
		IsDBBacked: true,
		ManageURL:  "/admin/integrations",
	})

	writeJSON(w, map[string]any{
		"stores":      allStores,
		"data_status": dataStatus,
	})
}

// AdminGetDataStore retrieves the raw byte content and metadata of a specific datastore.
func (h *Handler) AdminGetDataStore(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
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
		writeJSON(w, DataStoreDetailDTO{
			Key:         key,
			Title:       "Pricing Assumptions",
			Description: "Baseline sizing overrides stored in SQLite.",
			File:        "SQLite: assumptions",
			Editable:    false,
			IsDBBacked:  true,
			ManageURL:   "/admin/assumptions",
			Status:      "ok",
			Detail:      "DB-backed assumptions table",
			Count:       count,
		})
		return
	case "db_report_settings":
		count := 0
		if settings, err := h.db.GetReportSettings(); err == nil {
			count = len(settings)
		}
		writeJSON(w, DataStoreDetailDTO{
			Key:         key,
			Title:       "Report & Quota Settings",
			Description: "Engine defaults and concurrent job limits stored in SQLite.",
			File:        "SQLite: report_settings",
			Editable:    false,
			IsDBBacked:  true,
			ManageURL:   "/admin/report-settings",
			Status:      "ok",
			Detail:      "DB-backed report and quota settings",
			Count:       count,
		})
		return
	case "db_integrations":
		writeJSON(w, DataStoreDetailDTO{
			Key:         key,
			Title:       "Notification Integrations",
			Description: "Email and Slack channels stored in SQLite.",
			File:        "SQLite: integration_settings",
			Editable:    false,
			IsDBBacked:  true,
			ManageURL:   "/admin/integrations",
			Status:      "ok",
			Detail:      "DB-backed integration settings",
			Count:       2,
		})
		return
	case "catalog_artifact":
		info, _ := os.Stat(filepath.Join(dataDir, "catalog.duckdb"))
		var size int64
		if info != nil {
			size = info.Size()
		}
		writeJSON(w, DataStoreDetailDTO{
			Key:         key,
			Title:       storeManifest["catalog_artifact"].Title,
			Description: storeManifest["catalog_artifact"].Description,
			File:        "catalog.duckdb",
			Editable:    false,
			SizeBytes:   size,
			Status:      "ok",
			Detail:      "Binary DuckDB catalog artifact; managed via catalog refresh",
		})
		return
	case "catalog_source":
		writeJSON(w, DataStoreDetailDTO{
			Key:         key,
			Title:       storeManifest["catalog_source"].Title,
			Description: storeManifest["catalog_source"].Description,
			File:        "skus/*.json.gz",
			Editable:    false,
			Status:      "ok",
			Detail:      "Compressed SKU archives; managed via catalog refresh",
		})
		return
	}

	fileName, ok := editableDatastores[key]
	if !ok {
		writeJSONError(w, http.StatusNotFound, "Unknown datastore file.")
		return
	}

	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))
	cleanDataDir := filepath.Clean(dataDir)
	if !strings.HasPrefix(fullPath, cleanDataDir+string(filepath.Separator)) {
		writeJSONError(w, http.StatusBadRequest, "Path traversal rejected.")
		return
	}

	info, err := os.Stat(fullPath)
	if err != nil {
		if os.IsNotExist(err) {
			writeJSONError(w, http.StatusNotFound, "Datastore file does not exist.")
			return
		}
		writeJSONError(w, http.StatusInternalServerError, "Couldn't read file info: "+err.Error())
		return
	}

	if info.Size() > 2*1024*1024 {
		writeJSONError(w, http.StatusRequestEntityTooLarge, "File exceeds 2MB viewing limit.")
		return
	}

	contentBytes, err := os.ReadFile(fullPath)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Couldn't read the datastore file.")
		return
	}

	// Count items with a separate unmarshal only
	count := 0
	var parsed any
	if json.Unmarshal(contentBytes, &parsed) == nil {
		switch v := parsed.(type) {
		case map[string]any:
			count = len(v)
		case []any:
			count = len(v)
		}
	}

	// Backup metadata
	_, latestMod, backupCount := latestBackupFor(backupDir, key)
	lastBackupAt := ""
	if !latestMod.IsZero() {
		lastBackupAt = latestMod.UTC().Format(time.RFC3339)
	}

	ageDays := int(time.Since(info.ModTime()).Hours() / 24)

	title := storeManifest[key].Title
	description := storeManifest[key].Description

	writeJSON(w, DataStoreDetailDTO{
		Key:          key,
		Title:        title,
		Description:  description,
		File:         fileName,
		Editable:     true,
		SizeBytes:    info.Size(),
		AgeDays:      ageDays,
		Status:       "ok",
		Detail:       fmt.Sprintf("valid (%d items)", count),
		Count:        count,
		Content:      string(contentBytes),
		LastBackupAt: lastBackupAt,
		BackupCount:  backupCount,
	})
}

// unwrapContent accepts either the raw file, or {"content": "<raw file>"}
// (what the frontend sends). Only an object with exactly one key "content"
// holding a JSON string is treated as the wrapper.
func unwrapContent(body []byte) []byte {
	var m map[string]json.RawMessage
	if json.Unmarshal(body, &m) == nil && len(m) == 1 {
		if c, ok := m["content"]; ok {
			var s string
			if json.Unmarshal(c, &s) == nil {
				return []byte(s)
			}
		}
	}
	return body
}

// validateStoreJSON checks syntax (with line/column on error) and the
// top-level shape. It never re-encodes, so key order and formatting survive.
func validateStoreJSON(key string, raw []byte) error {
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		if se, ok := err.(*json.SyntaxError); ok {
			line, col := lineCol(raw, se.Offset)
			return fmt.Errorf("This isn't valid JSON — %s (line %d, column %d).", se.Error(), line, col)
		}
		return fmt.Errorf("This isn't valid JSON — %s.", err.Error())
	}
	switch key {
	case "service_map", "review_config", "fx_rates", "service_specs", "assumption_defaults", "pricing_matrix", "refresh_store":
		if _, ok := v.(map[string]any); !ok {
			return fmt.Errorf("The file must be a JSON object ({ … }) at the top level.")
		}
	}
	return nil
}

func lineCol(b []byte, off int64) (int, int) {
	line, col := 1, 1
	for i := int64(0); i < off && i < int64(len(b)); i++ {
		if b[i] == '\n' {
			line++
			col = 1
		} else {
			col++
		}
	}
	return line, col
}

// AdminPutDataStore updates the content of an editable datastore preserving raw formatting.
func (h *Handler) AdminPutDataStore(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	fileName, ok := editableDatastores[key]
	if !ok {
		writeJSONError(w, http.StatusBadRequest, "This file is read-only here.")
		return
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		writeJSONError(w, http.StatusBadRequest, "Skill data is baked into container image layer; runtime modifications cannot persist across redeploys.")
		return
	}

	dataDir := filepath.Join(skillDir, "data")
	cleanDataDir := filepath.Clean(dataDir)
	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))
	if !strings.HasPrefix(fullPath, cleanDataDir+string(filepath.Separator)) {
		writeJSONError(w, http.StatusBadRequest, "Path traversal rejected.")
		return
	}

	// 1. Read request body (cap at 2MB)
	bodyBytes, err := io.ReadAll(io.LimitReader(r.Body, 2*1024*1024))
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "Failed to read request body.")
		return
	}

	raw := unwrapContent(bodyBytes)
	if err := validateStoreJSON(key, raw); err != nil {
		writeJSONError(w, http.StatusBadRequest, err.Error())
		return
	}
	if len(raw) == 0 || raw[len(raw)-1] != '\n' {
		raw = append(raw, '\n')
	}

	// 2. Backup current file
	backupDir := filepath.Join(dataDir, "_backups")
	_ = os.MkdirAll(backupDir, 0755)

	var oldSize int64
	if currentData, err := os.ReadFile(fullPath); err == nil {
		oldSize = int64(len(currentData))
		ts := time.Now().UTC().Format("2006-01-02T15-04-05Z07-00")
		backupPath := filepath.Join(backupDir, fmt.Sprintf("%s.%s.json", key, ts))
		if bErr := os.WriteFile(backupPath, currentData, 0644); bErr != nil {
			slog.Warn("failed to create backup before datastore edit", "key", key, "err", bErr)
		} else {
			pruneBackups(backupDir, key, 10)
		}
	}

	// 3. Atomic write of raw bytes
	if err := atomicWriteFile(fullPath, raw); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Failed to save datastore file: "+err.Error())
		return
	}

	// 4. Audit
	sizeDelta := len(raw) - int(oldSize)
	_ = h.db.LogAdminAction(sess.Email, "datastore_edit", key, fmt.Sprintf("size_delta=%d bytes", sizeDelta))

	// 5. Return refreshed StoreHealth
	stores, _ := CheckDataStores(skillDir)
	var updatedStore StoreHealth
	for _, s := range stores {
		if s.Key == key {
			updatedStore = s
			break
		}
	}

	writeJSON(w, map[string]any{
		"message": "datastore updated successfully",
		"store":   updatedStore,
	})
}

// AdminListDataStoreBackups returns the version history for an editable datastore.
func (h *Handler) AdminListDataStoreBackups(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	if _, ok := editableDatastores[key]; !ok {
		writeJSONError(w, http.StatusBadRequest, "This file is read-only here.")
		return
	}

	skillDir := skill.ResolveDir()
	backupDir := filepath.Join(skillDir, "data", "_backups")
	entries, err := os.ReadDir(backupDir)
	if err != nil && !os.IsNotExist(err) {
		writeJSONError(w, http.StatusInternalServerError, "Couldn't read backups.")
		return
	}

	var backups []BackupInfo
	prefix := key + "."
	for _, e := range entries {
		if e.IsDir() || !strings.HasPrefix(e.Name(), prefix) || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		kind := "before-edit"
		if strings.Contains(e.Name(), ".pre-revert.") {
			kind = "pre-revert"
		} else if strings.Contains(e.Name(), ".pre-restore.") {
			kind = "pre-restore"
		}
		backups = append(backups, BackupInfo{
			Name:      e.Name(),
			CreatedAt: info.ModTime().UTC().Format(time.RFC3339),
			SizeBytes: info.Size(),
			Kind:      kind,
		})
	}
	if backups == nil {
		backups = []BackupInfo{}
	}

	sort.Slice(backups, func(i, j int) bool {
		return backups[i].CreatedAt > backups[j].CreatedAt
	})

	writeJSON(w, map[string]any{
		"backups": backups,
	})
}

// AdminRestoreDataStoreBackup restores a named backup file for an editable datastore.
func (h *Handler) AdminRestoreDataStoreBackup(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	fileName, ok := editableDatastores[key]
	if !ok {
		writeJSONError(w, http.StatusBadRequest, "This file is read-only here.")
		return
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		writeJSONError(w, http.StatusBadRequest, "Skill data is baked into container image layer; runtime modifications cannot persist across redeploys.")
		return
	}

	var req struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "Invalid request body.")
		return
	}

	if filepath.Base(req.Name) != req.Name || !strings.HasPrefix(req.Name, key+".") || !strings.HasSuffix(req.Name, ".json") {
		writeJSONError(w, http.StatusBadRequest, "That backup doesn't exist.")
		return
	}

	dataDir := filepath.Join(skillDir, "data")
	backupDir := filepath.Join(dataDir, "_backups")
	backupPath := filepath.Join(backupDir, req.Name)

	backupData, err := os.ReadFile(backupPath)
	if err != nil {
		writeJSONError(w, http.StatusNotFound, "That backup doesn't exist.")
		return
	}

	if err := validateStoreJSON(key, backupData); err != nil {
		writeJSONError(w, http.StatusBadRequest, "Backup contains invalid JSON: "+err.Error())
		return
	}

	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))

	// Backup current file as <key>.<ts>.pre-restore.json
	if currentData, err := os.ReadFile(fullPath); err == nil && !bytes.Equal(currentData, backupData) {
		ts := time.Now().UTC().Format("2006-01-02T15-04-05Z07-00")
		preRestoreBackup := filepath.Join(backupDir, fmt.Sprintf("%s.%s.pre-restore.json", key, ts))
		_ = os.WriteFile(preRestoreBackup, currentData, 0644)
	}

	if err := atomicWriteFile(fullPath, backupData); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Couldn't restore backup: "+err.Error())
		return
	}

	_ = h.db.LogAdminAction(sess.Email, "datastore_restore", key, req.Name)

	stores, _ := CheckDataStores(skillDir)
	var updatedStore StoreHealth
	for _, s := range stores {
		if s.Key == key {
			updatedStore = s
			break
		}
	}

	writeJSON(w, map[string]any{
		"message": fmt.Sprintf("Restored backup %s successfully.", req.Name),
		"store":   updatedStore,
	})
}

// AdminRevertDataStore restores the most recent backup for an editable datastore.
func (h *Handler) AdminRevertDataStore(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	fileName, ok := editableDatastores[key]
	if !ok {
		writeJSONError(w, http.StatusBadRequest, "This file is read-only here.")
		return
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		writeJSONError(w, http.StatusBadRequest, "Skill data is baked into container image layer; runtime modifications cannot persist across redeploys.")
		return
	}

	dataDir := filepath.Join(skillDir, "data")
	backupDir := filepath.Join(dataDir, "_backups")

	latestBackup, modTime, _ := latestBackupFor(backupDir, key)
	if latestBackup == "" {
		writeJSONError(w, http.StatusNotFound, "No backups found to revert for this store.")
		return
	}

	backupData, err := os.ReadFile(latestBackup)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Failed to read backup file.")
		return
	}

	if err := validateStoreJSON(key, backupData); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Backup contains corrupt JSON.")
		return
	}

	fullPath := filepath.Clean(filepath.Join(dataDir, fileName))

	// Backup current file before reverting
	if currentData, err := os.ReadFile(fullPath); err == nil && !bytes.Equal(currentData, backupData) {
		ts := time.Now().UTC().Format("2006-01-02T15-04-05Z07-00")
		preRevertBackup := filepath.Join(backupDir, fmt.Sprintf("%s.%s.pre-revert.json", key, ts))
		_ = os.WriteFile(preRevertBackup, currentData, 0644)
	}

	if err := atomicWriteFile(fullPath, backupData); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "Failed to restore backup: "+err.Error())
		return
	}

	_ = h.db.LogAdminAction(sess.Email, "datastore_revert", key, fmt.Sprintf("restored from %s (timestamp: %s)", filepath.Base(latestBackup), modTime.UTC().Format(time.RFC3339)))

	stores, _ := CheckDataStores(skillDir)
	var updatedStore StoreHealth
	for _, s := range stores {
		if s.Key == key {
			updatedStore = s
			break
		}
	}

	writeJSON(w, map[string]any{
		"message": fmt.Sprintf("reverted to backup from %s", modTime.UTC().Format(time.RFC3339)),
		"store":   updatedStore,
	})
}
