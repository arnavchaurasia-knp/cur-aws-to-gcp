package jobs

import (
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
	"github.com/facets/cur-web/internal/db"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// JobAssumptionItem represents a single assumption with its effective value for a specific job,
// matching the admin schema plus an indicator of whether it has been overridden.
type JobAssumptionItem struct {
	Key          string               `json:"key"`
	Value        float64              `json:"value"`
	DefaultValue float64              `json:"default_value"`
	MinValue     float64              `json:"min_value"`
	MaxValue     float64              `json:"max_value"`
	Unit         string               `json:"unit"`
	Title        string               `json:"title"`
	Rationale    string               `json:"rationale"`
	Category     string               `json:"category"`
	Scope        string               `json:"scope"`
	IsOverridden bool                 `json:"is_overridden"`
	Domain       *db.AssumptionDomain `json:"domain,omitempty"`
}

type jobAssumptionsSnapshot struct {
	Version        string             `json:"version"`
	GeneratedAt    string             `json:"generated_at"`
	OverriddenKeys []string           `json:"overridden_keys"`
	Overrides      map[string]float64 `json:"overrides"`
	Values         map[string]float64 `json:"values"`
	Reason         string             `json:"reason"`
	ChangedBy      string             `json:"changed_by"`
}

// GetJobAssumptions returns the effective assumptions for a job (user-facing, ownership-gated).
// It re-uses the admin schema (key, title, value, unit, min/max, rationale, category)
// and indicates which keys are overridden for this job.
func (h *Handler) GetJobAssumptions(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	h.ensureJobDirRestored(r.Context(), id, jobDir)

	globalAssumptions, err := h.db.GetAssumptions()
	if err != nil {
		http.Error(w, `{"error":"failed to load assumptions"}`, http.StatusInternalServerError)
		return
	}

	globalVer, _ := h.db.AssumptionsVersionHash()
	version := globalVer
	overriddenKeys := []string{}
	overriddenSet := make(map[string]bool)

	// Check if job snapshot exists
	snapshotPath := filepath.Join(jobDir, "assumptions.json")
	var snapshot jobAssumptionsSnapshot
	if data, err := os.ReadFile(snapshotPath); err == nil {
		if err := json.Unmarshal(data, &snapshot); err == nil {
			if snapshot.Version != "" {
				version = snapshot.Version
			}
			for _, k := range snapshot.OverriddenKeys {
				overriddenSet[k] = true
			}
			for k := range snapshot.Overrides {
				overriddenSet[k] = true
			}
			for k := range overriddenSet {
				overriddenKeys = append(overriddenKeys, k)
			}
			sort.Strings(overriddenKeys)
		}
	}

	items := make([]JobAssumptionItem, len(globalAssumptions))
	for i, a := range globalAssumptions {
		effectiveVal := a.Value
		if snapshot.Values != nil {
			if val, exists := snapshot.Values[a.Key]; exists {
				effectiveVal = val
			}
		}
		items[i] = JobAssumptionItem{
			Key:          a.Key,
			Value:        effectiveVal,
			DefaultValue: a.DefaultValue,
			MinValue:     a.MinValue,
			MaxValue:     a.MaxValue,
			Unit:         a.Unit,
			Title:        a.Title,
			Rationale:    a.Rationale,
			Category:     a.Category,
			Scope:        a.Scope,
			IsOverridden: overriddenSet[a.Key],
			Domain:       a.Domain,
		}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"assumptions":     items,
		"categories":      db.AssumptionCategories,
		"overridden_keys": overriddenKeys,
		"version":         version,
	})
}

// RerunJobAssumptions writes per-job assumption overrides and re-runs the projection pipeline.
// Ownership-gated, clamped to allowed ranges, with per-job lock and baseline preservation.
func (h *Handler) RerunJobAssumptions(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	if job.Status == "running" {
		http.Error(w, `{"error":"job is already running"}`, http.StatusConflict)
		return
	}
	if job.Status != "done" && job.Status != "failed" {
		http.Error(w, `{"error":"can only re-run a completed or failed job"}`, http.StatusConflict)
		return
	}

	// Per-job concurrency lock
	regenLocks.mu.Lock()
	if regenLocks.m[id] {
		regenLocks.mu.Unlock()
		http.Error(w, `{"error":"a re-run is already in progress for this job"}`, http.StatusConflict)
		return
	}
	regenLocks.m[id] = true
	regenLocks.mu.Unlock()
	defer func() {
		regenLocks.mu.Lock()
		delete(regenLocks.m, id)
		regenLocks.mu.Unlock()
	}()

	// Parse body: either {"overrides": {...}, "reason": "..."} or direct map
	var rawBody map[string]any
	if err := json.NewDecoder(r.Body).Decode(&rawBody); err != nil {
		http.Error(w, `{"error":"invalid json body"}`, http.StatusBadRequest)
		return
	}

	overrides := make(map[string]float64)
	reason := ""
	if rVal, ok := rawBody["reason"].(string); ok {
		reason = strings.TrimSpace(rVal)
	}

	if nested, ok := rawBody["overrides"].(map[string]any); ok {
		for k, v := range nested {
			if num, ok := v.(float64); ok {
				overrides[k] = num
			} else {
				http.Error(w, fmt.Sprintf(`{"error":"override value for %s must be a number"}`, k), http.StatusBadRequest)
				return
			}
		}
	} else {
		for k, v := range rawBody {
			if k == "reason" {
				continue
			}
			if num, ok := v.(float64); ok {
				overrides[k] = num
			}
		}
	}

	// Validate against global assumptions schema and clamp ranges
	globalAssumptions, err := h.db.GetAssumptions()
	if err != nil {
		http.Error(w, `{"error":"db error fetching assumptions"}`, http.StatusInternalServerError)
		return
	}
	assumpMap := make(map[string]*db.Assumption, len(globalAssumptions))
	for _, a := range globalAssumptions {
		assumpMap[a.Key] = a
	}

	for k, v := range overrides {
		a, exists := assumpMap[k]
		if !exists {
			http.Error(w, fmt.Sprintf(`{"error":"unknown assumption key: %s"}`, k), http.StatusBadRequest)
			return
		}
		if v < a.MinValue || v > a.MaxValue {
			http.Error(w, fmt.Sprintf(`{"error":"value %g for %s out of allowed range [%g, %g]"}`, v, k, a.MinValue, a.MaxValue), http.StatusBadRequest)
			return
		}
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}
	h.ensureJobDirRestored(r.Context(), id, jobDir)

	inputMatches, _ := filepath.Glob(filepath.Join(jobDir, "input.*"))
	if len(inputMatches) == 0 {
		http.Error(w, `{"error":"Original input file is missing. Please upload the bill again as a new report."}`, http.StatusBadRequest)
		return
	}

	// Preserve baseline files (Phase 4 / Invariant 7)
	copyFileIfNotExists(filepath.Join(jobDir, "report.html"), filepath.Join(jobDir, "report.baseline.html"))
	copyFileIfNotExists(
		filepath.Join(jobDir, projectionAuditDir, projectionDuckDB),
		filepath.Join(jobDir, projectionAuditDir, "projection.baseline.duckdb"),
	)
	copyFileIfNotExists(filepath.Join(jobDir, "assumptions.json"), filepath.Join(jobDir, "assumptions.baseline.json"))
	copyFileIfNotExists(filepath.Join(jobDir, "directives.json"), filepath.Join(jobDir, "directives.baseline.json"))

	// Build effective merged values
	values := make(map[string]float64, len(globalAssumptions))
	for _, a := range globalAssumptions {
		values[a.Key] = a.Value
	}
	overriddenKeys := make([]string, 0, len(overrides))
	for k, v := range overrides {
		values[k] = v
		overriddenKeys = append(overriddenKeys, k)
	}
	sort.Strings(overriddenKeys)

	globalVer, _ := h.db.AssumptionsVersionHash()
	version := fmt.Sprintf("%s-custom-%d", globalVer, time.Now().Unix())

	changedBy := ""
	if sess != nil && sess.Email != "" {
		changedBy = sess.Email
	}

	snapshot := map[string]any{
		"version":         version,
		"generated_at":    time.Now().UTC().Format(time.RFC3339),
		"overridden_keys": overriddenKeys,
		"overrides":       overrides,
		"values":          values,
		"reason":          reason,
		"changed_by":      changedBy,
	}

	snapshotData, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		http.Error(w, `{"error":"failed to serialize assumptions snapshot"}`, http.StatusInternalServerError)
		return
	}
	if err := os.WriteFile(filepath.Join(jobDir, "assumptions.json"), append(snapshotData, '\n'), 0644); err != nil {
		http.Error(w, `{"error":"failed to write assumptions snapshot"}`, http.StatusInternalServerError)
		return
	}

	// Log audit action and update assumption version in DB
	_ = h.db.UpdateJobAssumptionsVersion(id, version)
	_ = h.db.LogAdminAction(changedBy, "rerun_assumptions", id, version)

	// Clean up orchestrator state to re-run from Phase 1
	_ = os.Remove(filepath.Join(jobDir, "failure.txt"))
	_ = os.Remove(filepath.Join(jobDir, "orchestrator.lock"))
	_ = os.Remove(filepath.Join(jobDir, "phase_checkpoint.json"))

	newSession := uuid.New().String()
	if err := h.db.ResetJobForRetry(id, newSession); err != nil {
		http.Error(w, `{"error":"db reset failed"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.UpdateJobAssumptionsVersion(id, version)

	pid, _, err := h.spawner.Start(jobDir, job.InputExt, newSession, job.Engine)
	if err != nil {
		h.db.UpdateJobFailed(id, "rerun spawn failed: "+err.Error())
		http.Error(w, `{"error":"spawn failed"}`, http.StatusInternalServerError)
		return
	}

	if err := h.db.UpdateJobPID(id, pid); err != nil {
		slog.Error("UpdateJobPID failed after RerunJobAssumptions", "job_id", id, "pid", pid, "err", err)
	}
	h.db.UpdateJobRunning(id)
	if h.watcher != nil {
		go h.watcher.Watch(id, pid)
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]any{
		"id":      id,
		"status":  "running",
		"pid":     pid,
		"version": version,
	})
}

func copyFileIfNotExists(src, dst string) {
	if _, err := os.Stat(dst); err == nil {
		return // dst already exists, preserve original
	}
	srcFile, err := os.Open(src)
	if err != nil {
		return
	}
	defer srcFile.Close()

	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return
	}
	dstFile, err := os.Create(dst)
	if err != nil {
		return
	}
	defer dstFile.Close()

	_, _ = io.Copy(dstFile, srcFile)
}
