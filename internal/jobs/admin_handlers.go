// internal/jobs/admin_handlers.go
package jobs

import (
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
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// ─────────────────────────────────────────────────────────────────────────
// Admin: paginated/filtered job listing, cancel, delete
// ─────────────────────────────────────────────────────────────────────────

const defaultAdminPageSize = 50

// AdminListAllPaged extends AdminListAll with limit/offset/from/to/status
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

	jobList, err := h.db.ListAllJobsFiltered(limit, offset, status, from, to)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if jobList == nil {
		jobList = []*db.Job{}
	}
	total, err := h.db.CountAllJobs(status, from, to)
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
	list, err := h.db.ListDeletedJobs()
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
	entries, err := h.db.ListAuditLog(limit, offset)
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
// Shareable report links
// ─────────────────────────────────────────────────────────────────────────

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
	expiresAt := time.Now().Add(30 * 24 * time.Hour)
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
	if share.ExpiresAt != nil && time.Now().After(*share.ExpiresAt) {
		http.Error(w, `{"error":"this share link has expired"}`, http.StatusGone)
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
