// internal/jobs/admin_handlers.go
package jobs

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/metrics"
	"github.com/facets/cur-web/internal/skill"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// ─────────────────────────────────────────────────────────────────────────
// Admin: paginated/filtered job listing, cancel, delete
// ─────────────────────────────────────────────────────────────────────────

const defaultAdminPageSize = 50

// AdminListAllPaged extends AdminListAll with limit/offset/from/to/status/q/sort/dir
// query params and a total count for pagination. Registered at the same
// GET /api/admin/jobs route (replaces the plain AdminListAll handler).
func (h *Handler) AdminListAllPaged(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit := defaultAdminPageSize
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 500 {
			limit = n
		}
	}
	offset := 0
	if v := q.Get("offset"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 {
			offset = n
		}
	}
	status := q.Get("status")
	var from, to *time.Time
	if v := q.Get("from"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			from = &t
		}
	}
	if v := q.Get("to"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			// Inclusive end-of-day.
			t = t.Add(24*time.Hour - time.Second)
			to = &t
		}
	}
	searchQuery := q.Get("q")
	sortBy := q.Get("sort")
	sortDir := q.Get("dir")

	jobList, err := h.db.ListAllJobsFiltered(limit, offset, status, from, to, searchQuery, sortBy, sortDir)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if jobList == nil {
		jobList = []*db.Job{}
	}
	total, err := h.db.CountAllJobs(status, from, to, searchQuery)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"jobs":   jobList,
		"total":  total,
		"limit":  limit,
		"offset": offset,
	})
}

// cancelJob performs the authoritative stop of a job.
// Order of operations:
// 1. Guard valid id & load job.
// 2. If already terminal (cancelled, done, failed), idempotent return 200 with current status.
// 3. Write <jobDir>/cancelled sentinel file FIRST.
// 4. UpdateJobCancelled(id) in DB.
// 5. killGroup(job.AgentPID) — tree kill.
// 6. Remove <jobDir>/orchestrator.lock.
func (h *Handler) cancelJob(id, canceller string) (*db.Job, error) {
	if !isValidJobID(id) {
		return nil, os.ErrNotExist
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		return nil, err
	}
	// Idempotent: cancelling an already-terminal job is a no-op on processes
	switch job.Status {
	case "cancelled", "done", "failed":
		return job, nil
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if ok {
		_ = WriteCancelledSentinel(jobDir, canceller, "authoritative cancel")
	}

	if err := h.db.UpdateJobCancelled(id); err != nil {
		return nil, err
	}
	metrics.JobsCancelled.Add(1)

	if job.AgentPID > 0 {
		killGroup(job.AgentPID)
	}

	if ok {
		_ = os.Remove(filepath.Join(jobDir, "orchestrator.lock"))
	}

	job.Status = "cancelled"
	return job, nil
}

// AdminCancelJob kills the job's process tree and marks it cancelled authoritatively.
func (h *Handler) AdminCancelJob(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	canceller := "admin"
	if sess != nil && sess.Email != "" {
		canceller = sess.Email
	}
	job, err := h.cancelJob(id, canceller)
	if err != nil {
		if os.IsNotExist(err) {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if sess != nil {
		_ = h.db.LogAdminAction(sess.Email, "cancel_job", id, "")
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]string{"id": id, "status": job.Status})
}

// CancelAll cancels all non-terminal jobs in parallel, sets STOP_ALL, and reaps orphans.
func (h *Handler) CancelAll(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	canceller := "admin"
	if sess != nil && sess.Email != "" {
		canceller = sess.Email
	}

	// 1. Set persistent STOP_ALL sentinel file in dataDir (parent of jobsDir)
	dataDir := filepath.Dir(h.jobsDir)
	stopAllPath := filepath.Join(dataDir, "STOP_ALL")
	_ = os.WriteFile(stopAllPath, []byte(fmt.Sprintf("STOP_ALL set by %s at %s\n", canceller, time.Now().UTC().Format(time.RFC3339))), 0644)

	// 2. Load all non-terminal jobs
	jobs, err := h.db.ListNonTerminalJobs()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}

	var (
		mu              sync.Mutex
		cancelledIDs    []string
		alreadyTerminal []string
		errs            []string
		wg              sync.WaitGroup
	)

	for _, j := range jobs {
		wg.Add(1)
		go func(jobID string) {
			defer wg.Done()
			res, cErr := h.cancelJob(jobID, canceller)
			mu.Lock()
			defer mu.Unlock()
			if cErr != nil {
				errs = append(errs, fmt.Sprintf("%s: %v", jobID, cErr))
			} else if res != nil && res.Status == "cancelled" {
				cancelledIDs = append(cancelledIDs, jobID)
			} else {
				alreadyTerminal = append(alreadyTerminal, jobID)
			}
		}(j.ID)
	}
	wg.Wait()

	// 3. Sweep orphan agent processes
	activeIDs := make(map[string]bool)
	reapedPIDs := reapOrphanAgents(h.jobsDir, activeIDs)

	if sess != nil {
		_ = h.db.LogAdminAction(sess.Email, "cancel_all", "", fmt.Sprintf("cancelled=%d reaped_pids=%v", len(cancelledIDs), reapedPIDs))
	}

	if cancelledIDs == nil {
		cancelledIDs = []string{}
	}
	if alreadyTerminal == nil {
		alreadyTerminal = []string{}
	}
	if errs == nil {
		errs = []string{}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"cancelled":        cancelledIDs,
		"already_terminal": alreadyTerminal,
		"reaped_pids":      reapedPIDs,
		"errors":           errs,
		"stop_all":         true,
	})
}

// ResumeAll removes the STOP_ALL sentinel file and re-enables job spawning.
func (h *Handler) ResumeAll(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	dataDir := filepath.Dir(h.jobsDir)
	stopAllPath := filepath.Join(dataDir, "STOP_ALL")
	_ = os.Remove(stopAllPath)

	if sess != nil {
		_ = h.db.LogAdminAction(sess.Email, "resume_all", "", "")
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"status":   "ok",
		"stop_all": false,
	})
}

// SystemState reports whether orchestration is halted (for the UI banner).
func (h *Handler) SystemState(w http.ResponseWriter, r *http.Request) {
	dataDir := filepath.Dir(h.jobsDir)
	halted := IsStopAllActive(dataDir)
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"halted":   halted,
		"stop_all": halted,
	})
}

// AdminDeleteJob removes a terminal-status job's row and on-disk directory.
func (h *Handler) AdminDeleteJob(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	switch job.Status {
	case "done", "failed", "cancelled":
		// terminal — ok to delete
	default:
		http.Error(w, `{"error":"can only delete a job in a terminal state"}`, http.StatusConflict)
		return
	}
	if err := os.RemoveAll(jobDir); err != nil {
		http.Error(w, `{"error":"failed to remove job directory"}`, http.StatusInternalServerError)
		return
	}
	if err := h.db.ArchiveAndDeleteJob(id, sess.Email); err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "delete_job", id, "")
	w.WriteHeader(http.StatusNoContent)
}

// AdminListDeletedJobs lists all archived deleted jobs for compliance and auditing.
func (h *Handler) AdminListDeletedJobs(w http.ResponseWriter, r *http.Request) {
	searchQuery := r.URL.Query().Get("q")
	list, err := h.db.ListDeletedJobs(searchQuery)
	if err != nil {
		http.Error(w, `{"error":"failed to query deleted jobs"}`, http.StatusInternalServerError)
		return
	}
	if list == nil {
		list = []*db.DeletedJob{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(list)
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: users / RBAC
// ─────────────────────────────────────────────────────────────────────────

func (h *Handler) AdminListUsers(w http.ResponseWriter, r *http.Request) {
	users, err := h.db.ListUsers()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if users == nil {
		users = []*db.User{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(users)
}

var validRoles = map[string]bool{"viewer": true, "admin": true, "facets": true}

func (h *Handler) AdminSetUserRole(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	email := chi.URLParam(r, "email")
	var body struct {
		Role string `json:"role"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || !validRoles[body.Role] {
		http.Error(w, `{"error":"role must be one of viewer, admin, facets"}`, http.StatusBadRequest)
		return
	}
	lowerEmail := strings.ToLower(email)
	isFacets := strings.HasSuffix(lowerEmail, "@facets.cloud") || strings.HasSuffix(lowerEmail, "@facets.io") || strings.Contains(lowerEmail, "@facets.")
	if body.Role == "admin" && !isFacets {
		http.Error(w, `{"error":"admin role is restricted to @facets accounts"}`, http.StatusForbidden)
		return
	}
	if err := h.db.SetUserRole(email, body.Role); err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "set_user_role", email, body.Role)
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]string{"email": email, "role": body.Role})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: catalog status / forced refresh
// ─────────────────────────────────────────────────────────────────────────

func (h *Handler) AdminCatalogStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(GetCatalogStatus())
}

func (h *Handler) AdminCatalogRefresh(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	started, reason := TriggerCatalogRefresh()
	_ = h.db.LogAdminAction(sess.Email, "catalog_refresh", "", reason)
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	if !started {
		// Report the refusal rather than claiming success. See
		// TriggerCatalogRefresh for why a runtime refresh cannot persist here.
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]string{
			"status": "skipped",
			"reason": reason,
		})
		return
	}
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{"status": "refresh started"})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: audit log / analytics
// ─────────────────────────────────────────────────────────────────────────

func (h *Handler) AdminAuditLog(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit := 100
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 500 {
			limit = n
		}
	}
	offset := 0
	if v := q.Get("offset"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 {
			offset = n
		}
	}
	actor := q.Get("actor")
	action := q.Get("action")
	var from, to *time.Time
	if v := q.Get("from"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			from = &t
		}
	}
	if v := q.Get("to"); v != "" {
		if t, err := time.Parse("2006-01-02", v); err == nil {
			t = t.Add(24*time.Hour - time.Second)
			to = &t
		}
	}

	entries, err := h.db.ListAuditLog(limit, offset, actor, action, from, to)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if entries == nil {
		entries = []*db.AuditEntry{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(entries)
}

func (h *Handler) AdminAnalytics(w http.ResponseWriter, r *http.Request) {
	stats, err := h.db.GetUsageStats()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(stats)
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: pricing assumptions
// ─────────────────────────────────────────────────────────────────────────

// AdminGetAssumptions returns all assumption rows + current version hash.
func (h *Handler) AdminGetAssumptions(w http.ResponseWriter, r *http.Request) {
	assumptions, err := h.db.GetAssumptions()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if assumptions == nil {
		assumptions = []*db.Assumption{}
	}
	version, err := h.db.AssumptionsVersionHash()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"assumptions": assumptions,
		"version":     version,
	})
}

// AdminPutAssumption updates a single assumption value within bounds.
func (h *Handler) AdminPutAssumption(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	key := chi.URLParam(r, "key")

	var body struct {
		Value  float64 `json:"value"`
		Reason string  `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Reason == "" {
		http.Error(w, `{"error":"value and non-empty reason are required"}`, http.StatusBadRequest)
		return
	}

	version, err := h.db.UpdateAssumption(key, body.Value, body.Reason, sess.Email)
	if err != nil {
		msg := err.Error()
		if strings.Contains(msg, "not found") {
			http.Error(w, `{"error":"assumption not found"}`, http.StatusNotFound)
			return
		}
		if strings.Contains(msg, "out of range") {
			http.Error(w, `{"error":"` + msg + `"}`, http.StatusBadRequest)
			return
		}
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "update_assumption", key, body.Reason)
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"key":     key,
		"value":   body.Value,
		"version": version,
	})
}

// AdminGetAssumptionHistory returns assumption changes, newest first.
func (h *Handler) AdminGetAssumptionHistory(w http.ResponseWriter, r *http.Request) {
	changes, err := h.db.GetAssumptionHistory()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if changes == nil {
		changes = []*db.AssumptionChange{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(changes)
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: report settings toggles
// ─────────────────────────────────────────────────────────────────────────

// AdminGetReportSettings returns all report settings + current version hash.
func (h *Handler) AdminGetReportSettings(w http.ResponseWriter, r *http.Request) {
	settings, err := h.db.GetReportSettings()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if settings == nil {
		settings = []*db.ReportSetting{}
	}
	version, err := h.db.ReportSettingsVersionHash()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"settings": settings,
		"version":  version,
	})
}

// AdminPutReportSetting updates a single report setting within bounds/type.
func (h *Handler) AdminPutReportSetting(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	key := chi.URLParam(r, "key")

	var body struct {
		Value  string `json:"value"`
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Reason == "" {
		http.Error(w, `{"error":"value and non-empty reason are required"}`, http.StatusBadRequest)
		return
	}

	changedBy := ""
	if sess != nil && sess.Email != "" {
		changedBy = sess.Email
	}

	version, err := h.db.UpdateReportSetting(key, body.Value, changedBy)
	if err != nil {
		msg := err.Error()
		if strings.Contains(msg, "not found") {
			http.Error(w, `{"error":"report setting not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, `{"error":"`+msg+`"}`, http.StatusBadRequest)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "update_report_setting", key, body.Reason)
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"key":     key,
		"value":   body.Value,
		"version": version,
	})
}

// AdminGetReportSettingHistory returns report setting changes, newest first.
func (h *Handler) AdminGetReportSettingHistory(w http.ResponseWriter, r *http.Request) {
	changes, err := h.db.GetReportSettingHistory()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if changes == nil {
		changes = []*db.ReportSettingChange{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(changes)
}

// AdminResetReportSettings resets all report settings to their defaults.
func (h *Handler) AdminResetReportSettings(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())

	changedBy := ""
	if sess != nil && sess.Email != "" {
		changedBy = sess.Email
	}

	version, err := h.db.ResetReportSettings(changedBy)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "reset_report_settings", "", "reset to defaults")
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"version": version,
	})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: shared report links (list + revoke)
// ─────────────────────────────────────────────────────────────────────────

// shareRow is the masked representation returned to admins — the token is
// truncated so a compromised admin session cannot hand out working links.
type shareRow struct {
	Token      string     `json:"token"` // first 6 chars only
	JobID      string     `json:"job_id"`
	CreatedBy  string     `json:"created_by"`
	CreatedAt  time.Time  `json:"created_at"`
	ExpiresAt  *time.Time `json:"expires_at,omitempty"`
	Expired    bool       `json:"expired"`
}

// maskToken shows only the first 6 characters of a share token.
func maskToken(token string) string {
	if len(token) <= 6 {
		return token
	}
	return token[:6] + "\u2026"
}

// AdminListReportShares lists all share links (tokens masked).
func (h *Handler) AdminListReportShares(w http.ResponseWriter, r *http.Request) {
	shares, err := h.db.ListReportShares()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if shares == nil {
		shares = []*db.ReportShare{}
	}
	now := time.Now()
	rows := make([]shareRow, 0, len(shares))
	for _, s := range shares {
		rows = append(rows, shareRow{
			Token:     maskToken(s.Token),
			JobID:     s.JobID,
			CreatedBy: s.CreatedBy,
			CreatedAt: s.CreatedAt,
			ExpiresAt: s.ExpiresAt,
			Expired:   s.ExpiresAt != nil && now.After(*s.ExpiresAt),
		})
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(rows)
}

// AdminDeleteReportShare revokes (deletes) a share link.
func (h *Handler) AdminDeleteReportShare(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	token := chi.URLParam(r, "token")
	if err := h.db.DeleteReportShare(token); err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "revoke_share", token, "")
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.WriteHeader(http.StatusNoContent)
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: notification health
// ─────────────────────────────────────────────────────────────────────────

// AdminNotifyStatus returns notification channel health status.
func (h *Handler) AdminNotifyStatus(w http.ResponseWriter, r *http.Request) {
	status := map[string]any{
		"email": map[string]any{
			"configured": h.notify.Email.APIKey != "" && h.notify.Email.From != "",
		},
		"slack": map[string]any{
			"configured": h.notify.SlackURL != "",
		},
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(status)
}

// AdminNotifyTest sends a test notification.
func (h *Handler) AdminNotifyTest(w http.ResponseWriter, r *http.Request) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"status": "not_implemented",
	})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: job health diagnostics
// ─────────────────────────────────────────────────────────────────────────

// AdminJobDiagnostics returns the persisted diagnostics summary for a job.
// Falls back to reading run_diagnostics.json from the job directory if the DB
// row is absent. Returns {"available": false} with HTTP 200 when neither exists.
func (h *Handler) AdminJobDiagnostics(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(auth.SessionFromCtx(r.Context()), job) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	// Try durable DB first.
	row, err := h.db.GetJobDiagnostics(id, "initial")
	if err == nil && row != nil && row.Summary != nil {
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(map[string]any{
			"available": true,
			"summary":   row.Summary,
			"version":   row.RecordedAt,
		})
		return
	}

	// Fall back to job-directory file.
	jobDir := filepath.Clean(filepath.Join(h.jobsDir, id))
	diagPath := filepath.Join(jobDir, "projection-audit", "run_diagnostics.json")
	summary, err := os.ReadFile(diagPath)
	if err != nil {
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]any{
			"available": false,
		})
		return
	}
	var summaryObj map[string]any
	if err := json.Unmarshal(summary, &summaryObj); err != nil {
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]any{
			"available": false,
		})
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"available": true,
		"summary":   summaryObj,
	})
}

// AdminJobDiagnosticRecords returns individual diagnostic records from
// projection-audit/diagnostics.jsonl. Caps at 500 most recent records.
func (h *Handler) AdminJobDiagnosticRecords(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(auth.SessionFromCtx(r.Context()), job) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	jobDir := filepath.Clean(filepath.Join(h.jobsDir, id))
	diagPath := filepath.Join(jobDir, "projection-audit", "diagnostics.jsonl")
	data, err := os.ReadFile(diagPath)
	if err != nil {
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		json.NewEncoder(w).Encode(map[string]any{
			"available": false,
			"records":   []any{},
		})
		return
	}

	var records []map[string]any
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			continue // skip malformed lines
		}
		records = append(records, rec)
	}
	// Cap at most recent 500.
	if len(records) > 500 {
		records = records[len(records)-500:]
	}
	if records == nil {
		records = []map[string]any{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"available": true,
		"records":   records,
	})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: run comparison
// ─────────────────────────────────────────────────────────────────────────

// AdminCompareRuns compares two runs of a job and returns per-run aggregates
// plus header info about what changed between runs.
func (h *Handler) AdminCompareRuns(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(auth.SessionFromCtx(r.Context()), job) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	fromRunID := r.URL.Query().Get("from")
	toRunID := r.URL.Query().Get("to")
	if fromRunID == "" || toRunID == "" {
		http.Error(w, `{"error":"from and to run_id are required"}`, http.StatusBadRequest)
		return
	}

	jobDir := filepath.Clean(filepath.Join(h.jobsDir, id))
	h.ensureJobDirRestored(r.Context(), id, jobDir)
	dbPath := filepath.Join(jobDir, projectionAuditDir, projectionDuckDB)

	runs, err := QueryRunResults(dbPath)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}

	var runA, runB *RunResult
	for i := range runs {
		if runs[i].RunID == fromRunID {
			r := runs[i]
			runA = &r
		}
		if runs[i].RunID == toRunID {
			r := runs[i]
			runB = &r
		}
	}
	if runA == nil || runB == nil {
		http.Error(w, `{"error":"one or both runs not found"}`, http.StatusNotFound)
		return
	}

	// Compute deltas
	awsDelta := 0.0
	gcpODDelta := 0.0
	gcp1yrDelta := 0.0
	gcp3yrDelta := 0.0
	if runA.AWSTotal != nil && runB.AWSTotal != nil {
		awsDelta = *runB.AWSTotal - *runA.AWSTotal
	}
	if runA.GCPOD != nil && runB.GCPOD != nil {
		gcpODDelta = *runB.GCPOD - *runA.GCPOD
	}
	if runA.GCP1yrCUD != nil && runB.GCP1yrCUD != nil {
		gcp1yrDelta = *runB.GCP1yrCUD - *runA.GCP1yrCUD
	}
	if runA.GCP3yrCUD != nil && runB.GCP3yrCUD != nil {
		gcp3yrDelta = *runB.GCP3yrCUD - *runA.GCP3yrCUD
	}

	// Header info: get assumptions version from job, catalog vintage from job dir
	assumptionsVersionA := ""
	assumptionsVersionB := ""
	// Both runs belong to the same job, so they share the same current
	// assumptions version. Per-run version info is not stored in run_results.
	assumptionsVersionA = job.AssumptionsVersion
	assumptionsVersionB = job.AssumptionsVersion

	// Report-settings version from job directory
	reportSettingsVersionA := ""
	reportSettingsVersionB := ""
	settingsPath := filepath.Join(jobDir, "report-settings.json")
	if settingsData, err := os.ReadFile(settingsPath); err == nil {
		var settings struct {
			Version string `json:"version"`
		}
		if json.Unmarshal(settingsData, &settings) == nil {
			reportSettingsVersionA = settings.Version
			reportSettingsVersionB = settings.Version
		}
	}

	// Catalog vintage from CATALOG_META.json
	catalogVintageA := ""
	catalogVintageB := ""
	skillDir := skill.ResolveDir()
	metaPath := filepath.Join(skillDir, "data", "CATALOG_META.json")
	if metaData, err := os.ReadFile(metaPath); err == nil {
		var meta struct {
			FetchedAt string `json:"fetched_at"`
		}
		if json.Unmarshal(metaData, &meta) == nil && meta.FetchedAt != "" {
			catalogVintageA = meta.FetchedAt
			catalogVintageB = meta.FetchedAt
		}
	}

	resp := map[string]any{
		"run_a": map[string]any{
			"run_id":             runA.RunID,
			"ts_utc":             runA.TsUTC,
			"run_type":           runA.RunType,
			"instruction":        runA.Instruction,
			"aws_total":          runA.AWSTotal,
			"gcp_od":             runA.GCPOD,
			"gcp_1yr_cud":        runA.GCP1yrCUD,
			"gcp_3yr_cud":        runA.GCP3yrCUD,
			"mapped_rows":        runA.MappedRows,
			"passthroughs":       runA.Passthroughs,
			"confidence":         runA.Confidence,
		},
		"run_b": map[string]any{
			"run_id":             runB.RunID,
			"ts_utc":             runB.TsUTC,
			"run_type":           runB.RunType,
			"instruction":        runB.Instruction,
			"aws_total":          runB.AWSTotal,
			"gcp_od":             runB.GCPOD,
			"gcp_1yr_cud":        runB.GCP1yrCUD,
			"gcp_3yr_cud":        runB.GCP3yrCUD,
			"mapped_rows":        runB.MappedRows,
			"passthroughs":       runB.Passthroughs,
			"confidence":         runB.Confidence,
		},
		"aws_delta":             awsDelta,
		"gcp_od_delta":          gcpODDelta,
		"gcp_1yr_cud_delta":     gcp1yrDelta,
		"gcp_3yr_cud_delta":     gcp3yrDelta,
		"assumptions_version_a": assumptionsVersionA,
		"assumptions_version_b": assumptionsVersionB,
		"report_settings_version_a": reportSettingsVersionA,
		"report_settings_version_b": reportSettingsVersionB,
		"catalog_vintage_a":       catalogVintageA,
		"catalog_vintage_b":       catalogVintageB,
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(resp)
}

// ShareJob creates a report_shares row for job {id} (owner/admin only) and
// returns the public URL for it. Shares expire after 30 days.
func (h *Handler) ShareJob(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if !h.canAccess(sess, job) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}
	if job.Status != "done" {
		http.Error(w, `{"error":"can only share a completed job"}`, http.StatusConflict)
		return
	}
	token := uuid.New().String()
	expiresAt := time.Now().Add(90 * 24 * time.Hour)
	if err := h.db.CreateReportShare(token, id, sess.Email, expiresAt); err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]string{
		"token": token,
		"url":   "/reports/" + token,
	})
}

// GetSharedReport is the PUBLIC (no-auth) read-only summary lookup for a
// share token. Reuses the same run_results summary-file resolution as
// Handler.Summary.
func (h *Handler) GetSharedReport(w http.ResponseWriter, r *http.Request) {
	token := chi.URLParam(r, "token")
	share, err := h.db.GetReportShare(token)
	if err != nil || share == nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	// Enforce expiry: an expired link is treated the same as one that never
	// existed — do not distinguish (leaks whether a token was ever valid).
	if share.ExpiresAt != nil && time.Now().After(*share.ExpiresAt) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(share.JobID)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	jobDir := filepath.Clean(filepath.Join(h.jobsDir, share.JobID))
	dbPath := filepath.Join(jobDir, projectionAuditDir, projectionDuckDB)
	runs, err := QueryRunResults(dbPath)
	if err != nil {
		http.Error(w, `{"error":"internal error"}`, http.StatusInternalServerError)
		return
	}
	var pick *RunResult
	if len(runs) > 0 {
		pick = &runs[0]
	}
	resp := map[string]any{
		"prospect":   job.Prospect,
		"status":     job.Status,
		"created_at": job.CreatedAt,
	}
	if pick != nil && pick.SummaryMD != nil && *pick.SummaryMD != "" {
		p := filepath.Join(jobDir, *pick.SummaryMD)
		if data, err := os.ReadFile(p); err == nil {
			resp["summary_md"] = string(data)
		}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(resp)
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: ticket management
// ─────────────────────────────────────────────────────────────────────────

// AdminCreateTicket creates a new support ticket.
func (h *Handler) AdminCreateTicket(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	var body struct {
		Title       string `json:"title"`
		Description string `json:"description"`
		Priority    string `json:"priority"`
		Category    string `json:"category"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if body.Title == "" || body.Description == "" {
		http.Error(w, `{"error":"title and description are required"}`, http.StatusBadRequest)
		return
	}
	if body.Priority == "" {
		body.Priority = "medium"
	}
	if body.Category == "" {
		body.Category = "general"
	}
	ticket := &db.Ticket{
		ID:          db.SanitizeTicketID("ticket"),
		Title:       body.Title,
		Description: body.Description,
		Status:      "open",
		Priority:    body.Priority,
		Category:    body.Category,
		CreatedBy:   sess.Email,
	}
	if err := h.db.CreateTicket(ticket); err != nil {
		http.Error(w, `{"error":"failed to create ticket"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(ticket)
}

// AdminListTickets returns tickets. Admins see all; non-admins see only their own.
func (h *Handler) AdminListTickets(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	isAdmin := sess.IsAdmin(h.adminEmails)
	createdBy := ""
	if !isAdmin {
		createdBy = sess.Email
	}
	tickets, err := h.db.ListTickets(createdBy)
	if err != nil {
		http.Error(w, `{"error":"failed to list tickets"}`, http.StatusInternalServerError)
		return
	}
	if tickets == nil {
		tickets = []db.Ticket{}
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{"tickets": tickets})
}

// AdminGetTicket returns a single ticket.
func (h *Handler) AdminGetTicket(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	id := chi.URLParam(r, "id")
	ticket, err := h.db.GetTicket(id)
	if err != nil {
		http.Error(w, `{"error":"failed to get ticket"}`, http.StatusInternalServerError)
		return
	}
	if ticket == nil {
		http.Error(w, `{"error":"ticket not found"}`, http.StatusNotFound)
		return
	}
	isAdmin := sess.IsAdmin(h.adminEmails)
	if !isAdmin && ticket.CreatedBy != sess.Email {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(ticket)
}

// AdminUpdateTicket updates a ticket's status or other fields.
func (h *Handler) AdminUpdateTicket(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	id := chi.URLParam(r, "id")
	ticket, err := h.db.GetTicket(id)
	if err != nil {
		http.Error(w, `{"error":"failed to get ticket"}`, http.StatusInternalServerError)
		return
	}
	if ticket == nil {
		http.Error(w, `{"error":"ticket not found"}`, http.StatusNotFound)
		return
	}
	isAdmin := sess.IsAdmin(h.adminEmails)
	if !isAdmin && ticket.CreatedBy != sess.Email {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}
	var body struct {
		Status      string `json:"status"`
		Priority    string `json:"priority"`
		Category    string `json:"category"`
		Title       string `json:"title"`
		Description string `json:"description"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	// Only admins can change status to resolved/closed
	if body.Status != "" && body.Status != ticket.Status && !isAdmin {
		http.Error(w, `{"error":"only admins can change ticket status"}`, http.StatusForbidden)
		return
	}
	if body.Status != "" {
		if err := h.db.UpdateTicketStatus(id, body.Status, sess.Email); err != nil {
			http.Error(w, `{"error":"failed to update ticket"}`, http.StatusInternalServerError)
			return
		}
	}
	if body.Title != "" || body.Description != "" || body.Priority != "" || body.Category != "" {
		title := body.Title
		description := body.Description
		priority := body.Priority
		category := body.Category
		if title == "" { title = ticket.Title }
		if description == "" { description = ticket.Description }
		if priority == "" { priority = ticket.Priority }
		if category == "" { category = ticket.Category }
		if err := h.db.UpdateTicket(id, title, description, priority, category); err != nil {
			http.Error(w, `{"error":"failed to update ticket"}`, http.StatusInternalServerError)
			return
		}
	}
	updated, err := h.db.GetTicket(id)
	if err != nil {
		http.Error(w, `{"error":"failed to get updated ticket"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(updated)
}
