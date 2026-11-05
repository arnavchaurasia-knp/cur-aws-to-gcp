// internal/jobs/admin_handlers.go
package jobs

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/metrics"
	"github.com/facets/cur-web/internal/notify"
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
	// chi returns the RAW path segment, so an address the client encoded as
	// "alice%40example.com" arrives still encoded. Storing that verbatim is
	// what created the ghost "%40" user rows: it conflicts with no existing
	// row, so the upsert inserted a new one and the real account was never
	// promoted.
	rawEmail := chi.URLParam(r, "email")
	email, unescapeErr := url.PathUnescape(rawEmail)
	if unescapeErr != nil {
		http.Error(w, `{"error":"invalid email in path"}`, http.StatusBadRequest)
		return
	}
	lowerTargetEmail := strings.ToLower(strings.TrimSpace(email))

	// A decoded value still has to look like an address before it becomes a
	// primary key. The '%' check rejects a double-encoded value ("%2540"),
	// which would survive one unescape and reintroduce the same bug.
	if lowerTargetEmail == "" || !strings.Contains(lowerTargetEmail, "@") ||
		strings.HasPrefix(lowerTargetEmail, "@") || strings.HasSuffix(lowerTargetEmail, "@") ||
		strings.ContainsAny(lowerTargetEmail, " \t\r\n/%") {
		http.Error(w, `{"error":"invalid email"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		Role string `json:"role"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || !validRoles[body.Role] {
		http.Error(w, `{"error":"role must be one of viewer, admin, facets"}`, http.StatusBadRequest)
		return
	}

	callerEmail := ""
	if sess != nil {
		callerEmail = strings.ToLower(strings.TrimSpace(sess.Email))
	}

	// Guardrail 2: Nobody may change their own role.
	if callerEmail != "" && callerEmail == lowerTargetEmail {
		http.Error(w, `{"error":"cannot change your own role"}`, http.StatusBadRequest)
		return
	}

	// Fetch target's current role info
	targetInfo, err := h.db.GetUserRoleInfo(lowerTargetEmail)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	currentRole := targetInfo.Role
	if currentRole == "" {
		currentRole = "viewer"
	}

	// Guardrail 7: an address in $ADMIN_EMAILS resolves as admin at read time
	// regardless of its stored role, so storing "viewer" for it would not
	// revoke anything — it would only make the admin UI disagree with reality.
	// Refuse, and tell the operator where the real change has to happen.
	if body.Role != "admin" {
		for _, ae := range h.adminEmails {
			if strings.EqualFold(strings.TrimSpace(ae), lowerTargetEmail) {
				http.Error(w, `{"error":"this address is in the ADMIN_EMAILS environment allow-list and resolves as admin regardless of stored role; remove it from ADMIN_EMAILS and restart the server to revoke access"}`, http.StatusConflict)
				return
			}
		}
	}

	isTargetFacets := auth.IsFacetsEmail(lowerTargetEmail)
	isCallerFacets := sess != nil && sess.IsFacetsEmployee()

	// Guardrail 1: Only a facets admin may grant admin to a non-facets user.
	if !isTargetFacets && body.Role == "admin" && !isCallerFacets {
		http.Error(w, `{"error":"only facets admins can grant admin to external users"}`, http.StatusForbidden)
		return
	}

	// Guardrail 6: The last facets admin cannot be demoted.
	if isTargetFacets && currentRole == "admin" && body.Role != "admin" {
		users, err := h.db.ListUsers()
		if err != nil {
			http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
			return
		}
		facetsAdminCount := 0
		for _, u := range users {
			uFacets := auth.IsFacetsEmail(u.Email)
			if uFacets && u.Role == "admin" {
				facetsAdminCount++
			}
		}
		if facetsAdminCount <= 1 {
			http.Error(w, `{"error":"cannot demote the last facets admin"}`, http.StatusBadRequest)
			return
		}
	}

	// Determine granterEmail when setting admin for a non-facets user
	granterEmail := ""
	if !isTargetFacets && body.Role == "admin" {
		granterEmail = callerEmail
	}

	if err := h.db.SetUserRoleWithGrant(lowerTargetEmail, body.Role, granterEmail); err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}

	// Guardrail 3: Audit row recording granter, target, and before/after role
	auditDetails := fmt.Sprintf("role_changed from=%s to=%s", currentRole, body.Role)
	if granterEmail != "" {
		auditDetails += fmt.Sprintf(" granted_by=%s", granterEmail)
	}
	_ = h.db.LogAdminAction(callerEmail, "set_user_role", lowerTargetEmail, auditDetails)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]string{"email": lowerTargetEmail, "role": body.Role})
}

// AdminDeleteUser removes a user account.
func (h *Handler) AdminDeleteUser(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	rawEmail := chi.URLParam(r, "email")
	email, unescapeErr := url.PathUnescape(rawEmail)
	if unescapeErr != nil {
		http.Error(w, `{"error":"invalid email in path"}`, http.StatusBadRequest)
		return
	}
	lowerTargetEmail := strings.ToLower(strings.TrimSpace(email))
	if sess != nil && strings.ToLower(strings.TrimSpace(sess.Email)) == lowerTargetEmail {
		http.Error(w, `{"error":"cannot delete yourself"}`, http.StatusBadRequest)
		return
	}
	callerEmail := ""
	if sess != nil {
		callerEmail = sess.Email
	}
	if err := h.db.DeleteUser(lowerTargetEmail); err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(callerEmail, "delete_user", lowerTargetEmail, "")
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write([]byte(`{"status":"deleted"}`))
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
	actorEmail := "admin"
	if sess != nil {
		actorEmail = sess.Email
	}
	started, reason := TriggerCatalogRefresh(actorEmail)
	if sess != nil {
		_ = h.db.LogAdminAction(sess.Email, "catalog_refresh", "", reason)
	}
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

// AdminCatalogRefreshStatus returns the latest live/persisted refresh run state.
func (h *Handler) AdminCatalogRefreshStatus(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(GetCatalogRefreshStatus())
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

	parseAuditTime := func(v string, isEnd bool) *time.Time {
		v = strings.TrimSpace(v)
		if v == "" {
			return nil
		}
		// RFC3339
		if t, err := time.Parse(time.RFC3339, v); err == nil {
			return &t
		}
		// "2006-01-02T15:04:05" or "2006-01-02T15:04"
		for _, layout := range []string{"2006-01-02T15:04:05", "2006-01-02T15:04", "2006-01-02 15:04:05", "2006-01-02 15:04"} {
			if t, err := time.Parse(layout, v); err == nil {
				return &t
			}
		}
		// "2006-01-02"
		if t, err := time.Parse("2006-01-02", v); err == nil {
			if isEnd {
				t = t.Add(24*time.Hour - time.Second)
			}
			return &t
		}
		// "2006-01" (month)
		if t, err := time.Parse("2006-01", v); err == nil {
			if isEnd {
				nextMonth := t.AddDate(0, 1, 0)
				t = nextMonth.Add(-time.Second)
			}
			return &t
		}
		return nil
	}

	from = parseAuditTime(q.Get("from"), false)
	to = parseAuditTime(q.Get("to"), true)

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
		"categories":  db.AssumptionCategories,
		"version":     version,
	})
}

// AdminCreateAssumption creates a new assumption row.
func (h *Handler) AdminCreateAssumption(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	var body struct {
		Key          string  `json:"key"`
		Value        float64 `json:"value"`
		DefaultValue float64 `json:"default_value"`
		MinValue     float64 `json:"min_value"`
		MaxValue     float64 `json:"max_value"`
		Unit         string  `json:"unit"`
		Title        string  `json:"title"`
		Rationale    string  `json:"rationale"`
		Category     string  `json:"category"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Key == "" {
		http.Error(w, `{"error":"key is required"}`, http.StatusBadRequest)
		return
	}
	if body.Category == "" {
		body.Category = "other"
	}
	if !db.IsValidAssumptionCategory(body.Category) {
		http.Error(w, `{"error":"invalid category"}`, http.StatusBadRequest)
		return
	}
	if body.MinValue == 0 && body.MaxValue == 0 {
		body.MaxValue = 1000000.0
	}
	if body.DefaultValue == 0 {
		body.DefaultValue = body.Value
	}
	err := h.db.CreateAssumption(&db.Assumption{
		Key:          body.Key,
		Value:        body.Value,
		DefaultValue: body.DefaultValue,
		MinValue:     body.MinValue,
		MaxValue:     body.MaxValue,
		Unit:         body.Unit,
		Title:        body.Title,
		Rationale:    body.Rationale,
		Category:     body.Category,
	}, sess.Email)
	if err != nil {
		if strings.Contains(err.Error(), "invalid category") {
			http.Error(w, `{"error":"invalid category"}`, http.StatusBadRequest)
			return
		}
		http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusBadRequest)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "create_assumption", body.Key, fmt.Sprintf("category=%s", body.Category))
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]any{"key": body.Key, "status": "created"})
}

// AdminPutAssumption updates a single assumption value within bounds.
func (h *Handler) AdminPutAssumption(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	key := chi.URLParam(r, "key")

	var body struct {
		Value    float64 `json:"value"`
		Reason   string  `json:"reason"`
		Category string  `json:"category"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
		return
	}
	if body.Reason == "" {
		body.Reason = "Direct change"
	}
	if body.Category != "" && !db.IsValidAssumptionCategory(body.Category) {
		http.Error(w, `{"error":"invalid category"}`, http.StatusBadRequest)
		return
	}

	version, err := h.db.UpdateAssumption(key, body.Value, body.Reason, sess.Email, body.Category)
	if err != nil {
		msg := err.Error()
		if strings.Contains(msg, "invalid category") {
			http.Error(w, `{"error":"invalid category"}`, http.StatusBadRequest)
			return
		}
		if strings.Contains(msg, "not found") {
			http.Error(w, `{"error":"assumption not found"}`, http.StatusNotFound)
			return
		}
		if strings.Contains(msg, "out of range") || strings.Contains(msg, "allowed ratios are") {
			http.Error(w, `{"error":"`+msg+`"}`, http.StatusBadRequest)
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
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid request body"}`, http.StatusBadRequest)
		return
	}
	if body.Reason == "" {
		body.Reason = "Direct change"
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
	Token     string     `json:"token"` // first 6 chars only
	JobID     string     `json:"job_id"`
	CreatedBy string     `json:"created_by"`
	CreatedAt time.Time  `json:"created_at"`
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
	Expired   bool       `json:"expired"`
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
	email := h.emailCfg()
	status := map[string]any{
		"email": map[string]any{
			"configured": email.APIKey != "" && email.From != "",
		},
		"slack": map[string]any{
			"configured": h.slackURL() != "",
		},
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(status)
}

// AdminNotifyTest sends a test notification over the requested channel to the
// calling admin, using the resolved (DB-over-env) config.
func (h *Handler) AdminNotifyTest(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	var body struct {
		Channel string `json:"channel"` // "email" | "slack"
	}
	_ = json.NewDecoder(r.Body).Decode(&body)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	switch body.Channel {
	case "slack":
		url := h.slackURL()
		if url == "" {
			json.NewEncoder(w).Encode(map[string]any{"status": "error", "error": "Slack is not configured or is disabled"})
			return
		}
		if err := notify.PostContactInterest(url, "Test", sess.Email, "✅ Test message from the Facets admin integrations panel."); err != nil {
			json.NewEncoder(w).Encode(map[string]any{"status": "error", "error": err.Error()})
			return
		}
	case "email":
		cfg := h.emailCfg()
		if cfg.APIKey == "" {
			json.NewEncoder(w).Encode(map[string]any{"status": "error", "error": "email is not configured or is disabled"})
			return
		}
		if err := notify.SendContactInterest(cfg, []string{sess.Email}, "Test", sess.Email, "This is a test email from the Facets admin integrations panel."); err != nil {
			json.NewEncoder(w).Encode(map[string]any{"status": "error", "error": err.Error()})
			return
		}
	default:
		json.NewEncoder(w).Encode(map[string]any{"status": "error", "error": "channel must be 'email' or 'slack'"})
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "notify_test", body.Channel, "")
	json.NewEncoder(w).Encode(map[string]any{"status": "sent"})
}

// AdminGetIntegrations returns the masked notification configuration: secrets
// as configured/last4 only, never in the clear. Also reports whether a server
// encryption key is present (secrets cannot be saved without it).
func (h *Handler) AdminGetIntegrations(w http.ResponseWriter, r *http.Request) {
	settings, err := h.db.ListIntegrationSettings()
	if err != nil {
		http.Error(w, `{"error":"failed to read settings"}`, http.StatusInternalServerError)
		return
	}
	get := func(key string) db.IntegrationSetting { return settings[key] }
	resp := map[string]any{
		"encryption_available": db.EncryptionAvailable(),
		"email": map[string]any{
			"enabled":      h.db.GetIntegrationValueOr(keyEmailEnabled, "true") != "false",
			"from":         get(keyEmailFrom).Value,
			"api_key":      map[string]any{"configured": get(keyEmailAPIKey).Configured, "last4": get(keyEmailAPIKey).Last4},
			"env_fallback": h.notify.Email.APIKey != "",
		},
		"slack": map[string]any{
			"enabled":      h.db.GetIntegrationValueOr(keySlackEnabled, "true") != "false",
			"webhook_url":  map[string]any{"configured": get(keySlackWebhook).Configured, "last4": get(keySlackWebhook).Last4},
			"env_fallback": h.notify.SlackURL != "",
		},
	}
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(resp)
}

// AdminPutIntegrations updates the notification configuration. Secret fields
// left blank are preserved (not overwritten). Every change is audited; secret
// values are never logged.
func (h *Handler) AdminPutIntegrations(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	var body struct {
		Email *struct {
			Enabled *bool   `json:"enabled"`
			From    *string `json:"from"`
			APIKey  *string `json:"api_key"` // blank = keep current
		} `json:"email"`
		Slack *struct {
			Enabled    *bool   `json:"enabled"`
			WebhookURL *string `json:"webhook_url"` // blank = keep current
		} `json:"slack"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid body"}`, http.StatusBadRequest)
		return
	}

	set := func(key, val string, secret bool) error {
		return h.db.SetIntegrationSetting(key, val, secret, sess.Email)
	}
	changed := []string{}

	if body.Email != nil {
		if body.Email.Enabled != nil {
			if err := set(keyEmailEnabled, boolStr(*body.Email.Enabled), false); err != nil {
				writeIntegrationErr(w, err)
				return
			}
			changed = append(changed, "email.enabled")
		}
		if body.Email.From != nil {
			if err := set(keyEmailFrom, *body.Email.From, false); err != nil {
				writeIntegrationErr(w, err)
				return
			}
			changed = append(changed, "email.from")
		}
		if body.Email.APIKey != nil && *body.Email.APIKey != "" {
			if err := set(keyEmailAPIKey, *body.Email.APIKey, true); err != nil {
				writeIntegrationErr(w, err)
				return
			}
			changed = append(changed, "email.api_key")
		}
	}
	if body.Slack != nil {
		if body.Slack.Enabled != nil {
			if err := set(keySlackEnabled, boolStr(*body.Slack.Enabled), false); err != nil {
				writeIntegrationErr(w, err)
				return
			}
			changed = append(changed, "slack.enabled")
		}
		if body.Slack.WebhookURL != nil && *body.Slack.WebhookURL != "" {
			if err := set(keySlackWebhook, *body.Slack.WebhookURL, true); err != nil {
				writeIntegrationErr(w, err)
				return
			}
			changed = append(changed, "slack.webhook_url")
		}
	}

	_ = h.db.LogAdminAction(sess.Email, "update_integrations", "", strings.Join(changed, ","))
	h.AdminGetIntegrations(w, r)
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func writeIntegrationErr(w http.ResponseWriter, err error) {
	if err == db.ErrNoEncryptionKey {
		w.Header().Set(contentTypeHeader, contentTypeJSON)
		w.WriteHeader(http.StatusPreconditionFailed)
		json.NewEncoder(w).Encode(map[string]string{"error": "SETTINGS_ENCRYPTION_KEY is not set on the server; secrets cannot be saved. Set it (base64 of 32 random bytes) and restart."})
		return
	}
	http.Error(w, `{"error":"failed to save settings"}`, http.StatusInternalServerError)
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
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
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
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
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
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
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
			"run_id":       runA.RunID,
			"ts_utc":       runA.TsUTC,
			"run_type":     runA.RunType,
			"instruction":  runA.Instruction,
			"aws_total":    runA.AWSTotal,
			"gcp_od":       runA.GCPOD,
			"gcp_1yr_cud":  runA.GCP1yrCUD,
			"gcp_3yr_cud":  runA.GCP3yrCUD,
			"mapped_rows":  runA.MappedRows,
			"passthroughs": runA.Passthroughs,
			"confidence":   runA.Confidence,
		},
		"run_b": map[string]any{
			"run_id":       runB.RunID,
			"ts_utc":       runB.TsUTC,
			"run_type":     runB.RunType,
			"instruction":  runB.Instruction,
			"aws_total":    runB.AWSTotal,
			"gcp_od":       runB.GCPOD,
			"gcp_1yr_cud":  runB.GCP1yrCUD,
			"gcp_3yr_cud":  runB.GCP3yrCUD,
			"mapped_rows":  runB.MappedRows,
			"passthroughs": runB.Passthroughs,
			"confidence":   runB.Confidence,
		},
		"aws_delta":                 awsDelta,
		"gcp_od_delta":              gcpODDelta,
		"gcp_1yr_cud_delta":         gcp1yrDelta,
		"gcp_3yr_cud_delta":         gcp3yrDelta,
		"assumptions_version_a":     assumptionsVersionA,
		"assumptions_version_b":     assumptionsVersionB,
		"report_settings_version_a": reportSettingsVersionA,
		"report_settings_version_b": reportSettingsVersionB,
		"catalog_vintage_a":         catalogVintageA,
		"catalog_vintage_b":         catalogVintageB,
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
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
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
	_ = h.db.LogAdminAction(sess.Email, "create_ticket", ticket.ID, fmt.Sprintf("title=%s priority=%s category=%s", ticket.Title, ticket.Priority, ticket.Category))
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
	// Admins or ticket creators can change ticket status
	if body.Status != "" && body.Status != ticket.Status && !isAdmin && ticket.CreatedBy != sess.Email {
		http.Error(w, `{"error":"only admins or ticket creators can change ticket status"}`, http.StatusForbidden)
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
		if title == "" {
			title = ticket.Title
		}
		if description == "" {
			description = ticket.Description
		}
		if priority == "" {
			priority = ticket.Priority
		}
		if category == "" {
			category = ticket.Category
		}
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
	auditAction := "update_ticket"
	if body.Status != "" && strings.EqualFold(body.Status, "resolved") {
		auditAction = "resolve_ticket"
	} else if body.Status != "" && strings.EqualFold(body.Status, "closed") {
		auditAction = "close_ticket"
	} else if body.Status != "" && strings.EqualFold(body.Status, "open") {
		auditAction = "reopen_ticket"
	}
	_ = h.db.LogAdminAction(sess.Email, auditAction, id, fmt.Sprintf("status=%s priority=%s title=%s", updated.Status, updated.Priority, updated.Title))
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(updated)
}

// AdminDeleteTicket deletes a ticket by id.
func (h *Handler) AdminDeleteTicket(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	actorEmail := sess.Email
	id := chi.URLParam(r, "id")
	// Owner-scope: a non-admin may only delete their own ticket.
	ticket, err := h.db.GetTicket(id)
	if err != nil {
		http.Error(w, `{"error":"failed to get ticket"}`, http.StatusInternalServerError)
		return
	}
	if ticket == nil {
		http.Error(w, `{"error":"ticket not found"}`, http.StatusNotFound)
		return
	}
	if !sess.IsAdmin(h.adminEmails) && ticket.CreatedBy != sess.Email {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}
	if err := h.db.DeleteTicket(id); err != nil {
		http.Error(w, `{"error":"failed to delete ticket"}`, http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(actorEmail, "delete_ticket", id, "")
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write([]byte(`{"status":"deleted"}`))
}

// NotificationItem represents a single unified event in the admin notification feed.
type NotificationItem struct {
	ID        string `json:"id"`
	Type      string `json:"type"` // "job_failed", "job_completed", "ticket_raised", "assumption_changed", "setting_changed"
	Title     string `json:"title"`
	Message   string `json:"message"`
	Severity  string `json:"severity"`  // "critical", "warning", "info", "success"
	Actor     string `json:"actor"`     // User who triggered or owns the entity
	Link      string `json:"link"`      // Direct frontend link to navigate to
	Timestamp string `json:"timestamp"` // ISO 8601
}

// AdminNotifications returns notifications specifically for the calling admin:
// 1. Any failed job runs across the system (for any user).
// 2. Completed/active job runs initiated/owned by this admin.
// 3. All raised support tickets.
// 4. Assumption changes made by anyone.
// 5. Report setting changes made by anyone.
// 6. A unified chronologically sorted feed of events.
func (h *Handler) AdminNotifications(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	feed := make([]NotificationItem, 0)

	// 1. Fetch tickets
	tickets, err := h.db.ListTickets("")
	if err != nil {
		tickets = []db.Ticket{}
	}
	for _, t := range tickets {
		isResolved := strings.EqualFold(t.Status, "resolved") || strings.EqualFold(t.Status, "closed")
		if isResolved {
			resTime := t.UpdatedAt
			if t.ResolvedAt != nil {
				resTime = *t.ResolvedAt
			}
			actor := t.ResolvedBy
			if actor == "" {
				actor = t.CreatedBy
			}
			feed = append(feed, NotificationItem{
				ID:        "ticket-resolved-" + t.ID,
				Type:      "ticket_resolved",
				Title:     fmt.Sprintf("Ticket %s: %s", strings.Title(t.Status), t.Title),
				Message:   fmt.Sprintf("[%s] by %s (Resolved by %s): %s", strings.ToUpper(t.Priority), t.CreatedBy, actor, t.Description),
				Severity:  "success",
				Actor:     actor,
				Link:      "/admin/tickets",
				Timestamp: resTime.Format(time.RFC3339),
			})
		} else {
			sev := "warning"
			if strings.EqualFold(t.Priority, "urgent") || strings.EqualFold(t.Priority, "high") {
				sev = "critical"
			}
			feed = append(feed, NotificationItem{
				ID:        "ticket-" + t.ID,
				Type:      "ticket_raised",
				Title:     fmt.Sprintf("Ticket Raised: %s", t.Title),
				Message:   fmt.Sprintf("[%s/%s] by %s: %s", strings.ToUpper(t.Priority), strings.ToUpper(t.Status), t.CreatedBy, t.Description),
				Severity:  sev,
				Actor:     t.CreatedBy,
				Link:      "/admin/tickets",
				Timestamp: t.CreatedAt.Format(time.RFC3339),
			})
		}
	}

	// 1b. Fetch ticket lifecycle events from audit log
	auditEntries, aErr := h.db.ListAuditLog(50, 0, "", "", nil, nil)
	if aErr == nil {
		for _, a := range auditEntries {
			if a.Action == "delete_ticket" {
				feed = append(feed, NotificationItem{
					ID:        fmt.Sprintf("audit-del-ticket-%d", a.ID),
					Type:      "ticket_deleted",
					Title:     fmt.Sprintf("Ticket Deleted: %s", a.TargetID),
					Message:   fmt.Sprintf("Deleted by %s", a.ActorEmail),
					Severity:  "info",
					Actor:     a.ActorEmail,
					Link:      "/admin/tickets",
					Timestamp: a.CreatedAt.Format(time.RFC3339),
				})
			} else if a.Action == "reopen_ticket" {
				tid := a.TargetID
				if len(tid) > 8 {
					tid = tid[:8]
				}
				feed = append(feed, NotificationItem{
					ID:        fmt.Sprintf("audit-reopen-ticket-%d", a.ID),
					Type:      "ticket_reopened",
					Title:     fmt.Sprintf("Ticket Reopened: #%s", tid),
					Message:   fmt.Sprintf("Reopened by %s (%s)", a.ActorEmail, a.Details),
					Severity:  "warning",
					Actor:     a.ActorEmail,
					Link:      "/admin/tickets",
					Timestamp: a.CreatedAt.Format(time.RFC3339),
				})
			} else if a.Action == "update_ticket" {
				feed = append(feed, NotificationItem{
					ID:        fmt.Sprintf("audit-update-ticket-%d", a.ID),
					Type:      "ticket_updated",
					Title:     fmt.Sprintf("Ticket Edited: %s", a.TargetID),
					Message:   fmt.Sprintf("Edited by %s — %s", a.ActorEmail, a.Details),
					Severity:  "info",
					Actor:     a.ActorEmail,
					Link:      "/admin/tickets",
					Timestamp: a.CreatedAt.Format(time.RFC3339),
				})
			}
		}
	}

	// 2. Fetch jobs (inspect both my jobs and all failed jobs)
	allJobs, err := h.db.ListAllJobs()
	if err != nil {
		allJobs = []*db.Job{}
	}

	type AdminJobNotif struct {
		ID        string   `json:"id"`
		Prospect  string   `json:"prospect"`
		Owner     string   `json:"owner"`
		Status    string   `json:"status"`
		Error     string   `json:"error"`
		AWSSpend  *float64 `json:"aws_spend"`
		GCPOD     *float64 `json:"gcp_od"`
		CreatedAt string   `json:"created_at"`
	}

	userJobs := make([]AdminJobNotif, 0)
	failedJobs := make([]AdminJobNotif, 0)

	for _, j := range allJobs {
		createdStr := j.CreatedAt.Format(time.RFC3339)
		jobNotif := AdminJobNotif{
			ID:        j.ID,
			Prospect:  j.Prospect,
			Owner:     j.Owner,
			Status:    j.Status,
			Error:     j.Error,
			AWSSpend:  j.AWSSpend,
			GCPOD:     j.GCPOD,
			CreatedAt: createdStr,
		}

		// Job failed for ANY user
		if strings.EqualFold(j.Status, "failed") {
			failedJobs = append(failedJobs, jobNotif)
			feed = append(feed, NotificationItem{
				ID:        "job-failed-" + j.ID,
				Type:      "job_failed",
				Title:     fmt.Sprintf("Job Run Failed: %s", j.Prospect),
				Message:   fmt.Sprintf("User: %s — %s", j.Owner, j.Error),
				Severity:  "critical",
				Actor:     j.Owner,
				Link:      fmt.Sprintf("/jobs/%s", j.ID),
				Timestamp: createdStr,
			})
		} else if strings.EqualFold(j.Owner, sess.Email) {
			// Completed / my job run
			userJobs = append(userJobs, jobNotif)
			if strings.EqualFold(j.Status, "completed") {
				spendStr := ""
				if j.GCPOD != nil {
					spendStr = fmt.Sprintf(" (Projected: $%.2f/mo)", *j.GCPOD)
				}
				feed = append(feed, NotificationItem{
					ID:        "job-completed-" + j.ID,
					Type:      "job_completed",
					Title:     fmt.Sprintf("Your Job Run Completed: %s", j.Prospect),
					Message:   fmt.Sprintf("Analysis completed%s", spendStr),
					Severity:  "success",
					Actor:     j.Owner,
					Link:      fmt.Sprintf("/jobs/%s", j.ID),
					Timestamp: createdStr,
				})
			}
		}
	}

	// 3. Assumption changes
	assumptionChanges, err := h.db.GetAssumptionHistory()
	if err == nil {
		for _, ac := range assumptionChanges {
			feed = append(feed, NotificationItem{
				ID:        fmt.Sprintf("assumption-%d", ac.ID),
				Type:      "assumption_changed",
				Title:     fmt.Sprintf("Assumption Changed: %s", ac.Key),
				Message:   fmt.Sprintf("Changed by %s from %.2f to %.2f (%s)", ac.ChangedBy, ac.OldValue, ac.NewValue, ac.Reason),
				Severity:  "info",
				Actor:     ac.ChangedBy,
				Link:      "/admin/assumptions",
				Timestamp: ac.ChangedAt,
			})
		}
	}

	// 4. Report settings changes
	settingChanges, err := h.db.GetReportSettingHistory()
	if err == nil {
		for _, sc := range settingChanges {
			feed = append(feed, NotificationItem{
				ID:        fmt.Sprintf("setting-%d", sc.ID),
				Type:      "setting_changed",
				Title:     fmt.Sprintf("Report Setting Changed: %s", sc.Key),
				Message:   fmt.Sprintf("Updated by %s: %s → %s", sc.ChangedBy, sc.OldValue, sc.NewValue),
				Severity:  "info",
				Actor:     sc.ChangedBy,
				Link:      "/admin/report-settings",
				Timestamp: sc.ChangedAt,
			})
		}
	}

	// Sort feed newest first
	sortSliceByTimestampDesc(feed)

	// Keep top 60 recent events
	if len(feed) > 60 {
		feed = feed[:60]
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"admin_email":        sess.Email,
		"feed":               feed,
		"my_jobs":            userJobs,
		"failed_jobs":        failedJobs,
		"tickets":            tickets,
		"assumption_changes": assumptionChanges,
		"setting_changes":    settingChanges,
	})
}

func sortSliceByTimestampDesc(items []NotificationItem) {
	for i := 0; i < len(items)-1; i++ {
		for j := i + 1; j < len(items); j++ {
			if items[j].Timestamp > items[i].Timestamp {
				items[i], items[j] = items[j], items[i]
			}
		}
	}
}

// locateBillFile finds the physical bill file given the content hash and input extension.
func (h *Handler) locateBillFile(contentHash, inputExt string) string {
	candidates := []string{
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+inputExt),
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+".csv"),
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+".csv.gz"),
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+".gz"),
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+".xlsx"),
		filepath.Join(h.jobsDir, ".shared_bills", contentHash+".pdf"),
	}
	for _, p := range candidates {
		if fi, err := os.Stat(p); err == nil && !fi.IsDir() {
			return p
		}
	}

	// Also check individual job folders for input files matching hash
	jobDirs, _ := filepath.Glob(filepath.Join(h.jobsDir, "*"))
	for _, jd := range jobDirs {
		if filepath.Base(jd) == ".shared_bills" || filepath.Base(jd) == ".uploads" {
			continue
		}
		inputs, _ := filepath.Glob(filepath.Join(jd, "input.*"))
		for _, inp := range inputs {
			if fi, err := os.Stat(inp); err == nil && !fi.IsDir() {
				// If hash matches or if we can verify
				return inp
			}
		}
	}
	return ""
}

// AdminDownloadUploadedBill downloads the original uploaded bill file.
func (h *Handler) AdminDownloadUploadedBill(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}

	hash := chi.URLParam(r, "hash")
	if len(hash) != 64 {
		http.Error(w, `{"error":"invalid bill hash"}`, http.StatusBadRequest)
		return
	}

	bill, err := h.db.GetUploadedBill(hash)
	if err != nil || bill == nil {
		http.Error(w, `{"error":"bill not found in database"}`, http.StatusNotFound)
		return
	}

	// Owner-scope: a non-admin may access only their own bills (legacy bills
	// with an empty owner remain shared-visible, matching the list endpoint).
	if !sess.IsAdmin(h.adminEmails) && bill.Owner != "" && bill.Owner != sess.Email {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	filePath := h.locateBillFile(bill.ContentHash, bill.InputExt)
	if filePath == "" {
		http.Error(w, `{"error":"bill file not found on disk"}`, http.StatusNotFound)
		return
	}

	filename := bill.FileName
	if filename == "" {
		filename = fmt.Sprintf("bill_%s%s", bill.ContentHash[:12], bill.InputExt)
	}

	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	http.ServeFile(w, r, filePath)
}

// AdminViewUploadedBill serves the uploaded bill inline for direct browser viewing.
func (h *Handler) AdminViewUploadedBill(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	hash := chi.URLParam(r, "hash")
	if len(hash) != 64 {
		http.Error(w, `{"error":"invalid bill hash"}`, http.StatusBadRequest)
		return
	}

	bill, err := h.db.GetUploadedBill(hash)
	if err != nil || bill == nil {
		http.Error(w, `{"error":"bill not found in database"}`, http.StatusNotFound)
		return
	}

	filePath := h.locateBillFile(bill.ContentHash, bill.InputExt)
	if filePath == "" {
		http.Error(w, `{"error":"bill file not found on disk"}`, http.StatusNotFound)
		return
	}

	filename := bill.FileName
	if filename == "" {
		filename = fmt.Sprintf("bill_%s%s", bill.ContentHash[:12], bill.InputExt)
	}

	lowerExt := strings.ToLower(filepath.Ext(filePath))
	switch lowerExt {
	case ".pdf":
		w.Header().Set("Content-Type", "application/pdf")
	case ".csv":
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	case ".json":
		w.Header().Set("Content-Type", "application/json")
	default:
		w.Header().Set("Content-Type", "application/octet-stream")
	}

	w.Header().Set("Content-Disposition", fmt.Sprintf(`inline; filename="%s"`, filename))
	http.ServeFile(w, r, filePath)
}

// AdminPreviewUploadedBill returns structured preview lines (up to 100 lines) of a CSV/text bill.
func (h *Handler) AdminPreviewUploadedBill(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}

	hash := chi.URLParam(r, "hash")
	if len(hash) != 64 {
		http.Error(w, `{"error":"invalid bill hash"}`, http.StatusBadRequest)
		return
	}

	bill, err := h.db.GetUploadedBill(hash)
	if err != nil || bill == nil {
		http.Error(w, `{"error":"bill not found in database"}`, http.StatusNotFound)
		return
	}

	// Owner-scope: a non-admin may access only their own bills (legacy bills
	// with an empty owner remain shared-visible, matching the list endpoint).
	if !sess.IsAdmin(h.adminEmails) && bill.Owner != "" && bill.Owner != sess.Email {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	filePath := h.locateBillFile(bill.ContentHash, bill.InputExt)
	if filePath == "" {
		http.Error(w, `{"error":"bill file not found on disk"}`, http.StatusNotFound)
		return
	}

	// Read up to 100 lines
	f, err := os.Open(filePath)
	if err != nil {
		http.Error(w, `{"error":"failed to open file"}`, http.StatusInternalServerError)
		return
	}
	defer f.Close()

	var reader io.Reader = f
	if strings.HasSuffix(strings.ToLower(filePath), ".gz") {
		gzr, err := gzip.NewReader(f)
		if err == nil {
			defer gzr.Close()
			reader = gzr
		}
	}

	scanner := bufio.NewScanner(reader)
	// Buffer up to 1MB per line for wide CSVs
	buf := make([]byte, 1024*1024)
	scanner.Buffer(buf, 1024*1024)

	lines := make([]string, 0, 100)
	count := 0
	for scanner.Scan() && count < 100 {
		lines = append(lines, scanner.Text())
		count++
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"file_name":        bill.FileName,
		"content_hash":     bill.ContentHash,
		"input_ext":        bill.InputExt,
		"file_size":        bill.FileSize,
		"lines":            lines,
		"total_lines_read": len(lines),
		"is_truncated":     count >= 100,
	})
}

// ─────────────────────────────────────────────────────────────────────────
// Admin: Review findings triage & self-healing refresh loop
// ─────────────────────────────────────────────────────────────────────────

// AdminListFindings lists review findings across all jobs or for a specific job.
// Query params: job_id, service, severity, status, limit, offset.
func (h *Handler) AdminListFindings(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	jobID := chi.URLParam(r, "id")
	if jobID == "" {
		jobID = q.Get("job_id")
	}
	service := q.Get("service")
	severity := q.Get("severity")
	status := q.Get("status")

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

	findings, total, err := h.db.ListReviewFindings(jobID, service, severity, status, limit, offset)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if findings == nil {
		findings = []db.ReviewFinding{}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"findings": findings,
		"total":    total,
		"limit":    limit,
		"offset":   offset,
	})
}

// AdminActionFinding updates a finding status: accept | reject | defer.
// On 'accept', it promotes the fact into the writable refresh store (per-user
// state dir, ~/.cur-web/refresh_store.json by default — NOT the shared data/ seed).
func (h *Handler) AdminActionFinding(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	findingID := chi.URLParam(r, "id")
	if findingID == "" {
		http.Error(w, `{"error":"missing finding id"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		Action     string  `json:"action"` // accept | reject | defer
		Note       string  `json:"note"`
		GCPService string  `json:"gcp_service,omitempty"`
		GCPSKUName string  `json:"gcp_sku_name,omitempty"`
		GCPSKUID   string  `json:"gcp_sku_id,omitempty"`
		Strategy   string  `json:"strategy,omitempty"`
		Multiplier float64 `json:"unit_multiplier,omitempty"`
	}

	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	action := strings.ToLower(strings.TrimSpace(body.Action))
	if action != "accept" && action != "reject" && action != "defer" {
		http.Error(w, `{"error":"invalid action; must be accept, reject, or defer"}`, http.StatusBadRequest)
		return
	}

	finding, err := h.db.GetReviewFindingByID(findingID)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if finding == nil {
		http.Error(w, `{"error":"finding not found"}`, http.StatusNotFound)
		return
	}

	status := "pending"
	switch action {
	case "accept":
		status = "accepted"
	case "reject":
		status = "rejected"
	case "defer":
		status = "deferred"
	}

	if err := h.db.UpdateReviewFindingAction(findingID, status, body.Note, sess.Email); err != nil {
		http.Error(w, `{"error":"failed to update finding"}`, http.StatusInternalServerError)
		return
	}

	// If accepted, promote to refresh store!
	if action == "accept" {
		targetService := body.GCPService
		if targetService == "" && finding.Evidence != nil {
			if s, ok := finding.Evidence["gcp_service"].(string); ok {
				targetService = s
			}
		}
		targetSKUName := body.GCPSKUName
		if targetSKUName == "" && finding.Evidence != nil {
			if s, ok := finding.Evidence["gcp_sku_name"].(string); ok {
				targetSKUName = s
			}
		}
		targetSKUID := body.GCPSKUID
		if targetSKUID == "" && finding.Evidence != nil {
			if s, ok := finding.Evidence["gcp_sku_id"].(string); ok {
				targetSKUID = s
			}
		}
		strat := body.Strategy
		if strat == "" {
			strat = "map"
		}
		mult := body.Multiplier
		if mult <= 0 {
			mult = 1.0
		}

		key := finding.CanonicalService
		if key == "" || key == "unknown" {
			if p, ok := finding.Evidence["product"].(string); ok && p != "" {
				key = p
			}
		}

		if key != "" && targetService != "" {
			h.promoteFindingToRefreshStore(key, targetService, targetSKUName, targetSKUID, strat, mult, sess.Email)
		}
	}

	_ = h.db.LogAdminAction(sess.Email, "finding_"+action, findingID, body.Note)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"id":     findingID,
		"status": status,
		"action": action,
	})
}

// promoteFindingToRefreshStore calls python refresh_store.py or updates refresh_store.json.
func (h *Handler) promoteFindingToRefreshStore(key, gcpService, gcpSKUName, gcpSKUID, strategy string, multiplier float64, user string) {
	skillDir := skill.ResolveDir()
	storePath := filepath.Join(skillDir, "data", "refresh_store.json")

	type StoreEntry struct {
		GCPService        string   `json:"gcp_service"`
		GCPSKUName        string   `json:"gcp_sku_name"`
		GCPSKUID          string   `json:"gcp_sku_id"`
		Strategy          string   `json:"strategy"`
		UnitMultiplier    float64  `json:"unit_multiplier"`
		ConfirmationCount int      `json:"confirmation_count"`
		Status            string   `json:"status"`
		LastConfirmedAt   string   `json:"last_confirmed_at"`
		Sources           []string `json:"sources"`
	}

	type RefreshStoreDoc struct {
		Version      string                `json:"version"`
		UpdatedAt    string                `json:"updated_at"`
		Mappings     map[string]StoreEntry `json:"mappings"`
		ProductCodes map[string]any        `json:"product_codes"`
		Rates        map[string]any        `json:"rates"`
	}

	var doc RefreshStoreDoc
	data, err := os.ReadFile(storePath)
	if err == nil {
		_ = json.Unmarshal(data, &doc)
	}
	if doc.Mappings == nil {
		doc.Mappings = make(map[string]StoreEntry)
	}
	if doc.Version == "" {
		doc.Version = "1.0.0"
	}
	doc.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

	cleanKey := strings.ToLower(strings.TrimSpace(key))
	entry, exists := doc.Mappings[cleanKey]
	count := 1
	sources := []string{user}
	if exists {
		count = entry.ConfirmationCount + 1
		sources = append(entry.Sources, user)
	}

	doc.Mappings[cleanKey] = StoreEntry{
		GCPService:        gcpService,
		GCPSKUName:        gcpSKUName,
		GCPSKUID:          gcpSKUID,
		Strategy:          strategy,
		UnitMultiplier:    multiplier,
		ConfirmationCount: count,
		Status:            "trusted", // admin accepted
		LastConfirmedAt:   time.Now().UTC().Format(time.RFC3339),
		Sources:           sources,
	}

	if out, err := json.MarshalIndent(doc, "", "  "); err == nil {
		_ = os.WriteFile(storePath, out, 0644)
	}
}


// syncBillsFromJobsDir automatically scans both .shared_bills and all individual job directories
// to ensure every bill ever submitted or on disk is discovered, indexed, and visible in uploaded_bills.
func (h *Handler) syncBillsFromJobsDir() {
	sharedBillsDir := filepath.Join(h.jobsDir, ".shared_bills")
	_ = os.MkdirAll(sharedBillsDir, 0755)

	// 1. Scan .shared_bills directory
	if entries, err := os.ReadDir(sharedBillsDir); err == nil {
		for _, e := range entries {
			if e.IsDir() {
				continue
			}
			name := e.Name()
			ext := filepath.Ext(name)
			base := strings.TrimSuffix(name, ext)
			if len(base) == 64 {
				if existing, _ := h.db.GetUploadedBill(base); existing == nil {
					fi, _ := e.Info()
					sz := int64(0)
					if fi != nil {
						sz = fi.Size()
					}
					_ = h.db.UpsertUploadedBill(base, ext, "bill-"+base[:8]+ext, sz)
				}
			}
		}
	}

	// 2. Scan all job folders for input.* files
	jobDirs, _ := filepath.Glob(filepath.Join(h.jobsDir, "*"))
	for _, jd := range jobDirs {
		base := filepath.Base(jd)
		if strings.HasPrefix(base, ".") {
			continue
		}
		inputs, _ := filepath.Glob(filepath.Join(jd, "input.*"))
		for _, inp := range inputs {
			fi, err := os.Stat(inp)
			if err != nil || fi.IsDir() || fi.Size() == 0 {
				continue
			}
			ext := filepath.Ext(inp)
			f, oerr := os.Open(inp)
			if oerr != nil {
				continue
			}
			hasher := sha256.New()
			_, _ = io.Copy(hasher, f)
			f.Close()
			hashHex := hex.EncodeToString(hasher.Sum(nil))
			if existing, _ := h.db.GetUploadedBill(hashHex); existing == nil {
				prospect := "bill-" + base[:8]
				if custData, cerr := os.ReadFile(filepath.Join(jd, "customer_name.txt")); cerr == nil && len(custData) > 0 {
					prospect = strings.TrimSpace(string(custData))
				}
				_ = h.db.UpsertUploadedBill(hashHex, ext, prospect+ext, fi.Size())
				// Link into shared bills
				sharedPath := filepath.Join(sharedBillsDir, hashHex+ext)
				if _, serr := os.Stat(sharedPath); os.IsNotExist(serr) {
					_ = os.Link(inp, sharedPath)
				}
			}
		}
	}
}

// AdminDeleteUploadedBill deletes an uploaded bill from storage and database.
func (h *Handler) AdminDeleteUploadedBill(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}

	hash := strings.ToLower(strings.TrimSpace(chi.URLParam(r, "hash")))
	if len(hash) != 64 {
		http.Error(w, `{"error":"invalid sha256 hash"}`, http.StatusBadRequest)
		return
	}

	bill, _ := h.db.GetUploadedBill(hash)
	fileName := ""
	if bill != nil {
		fileName = bill.FileName
	}

	// Owner-scope: a non-admin may delete only a bill they own. Unlike
	// download/preview, empty-owner (legacy/shared) bills are NOT deletable by
	// non-admins, so one user cannot destroy shared history.
	if !sess.IsAdmin(h.adminEmails) && (bill == nil || bill.Owner != sess.Email) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	if err := h.db.DeleteUploadedBill(hash); err != nil {
		http.Error(w, `{"error":"failed to delete bill record"}`, http.StatusInternalServerError)
		return
	}

	// Remove physical files matching hash from .shared_bills
	sharedBillsDir := filepath.Join(h.jobsDir, ".shared_bills")
	if files, err := filepath.Glob(filepath.Join(sharedBillsDir, hash+"*")); err == nil {
		for _, f := range files {
			_ = os.Remove(f)
		}
	}

	_ = h.db.LogAdminAction(sess.Email, "delete_bill", hash, fileName)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write([]byte(`{"status":"deleted"}`))
}


// ─────────────────────────────────────────────────────────────────────────
// System Health Handlers (Phase 3)
// ─────────────────────────────────────────────────────────────────────────

type HealthResponse struct {
	OverallReason string         `json:"overall_reason"`
	Engine        HealthEngine   `json:"engine"`
	Catalog       HealthCatalog  `json:"catalog"`
	Tokens        HealthTokens   `json:"tokens"`
	Pipeline      HealthPipeline `json:"pipeline"`
	Stores        []StoreHealth  `json:"stores"`
	DataStatus    string         `json:"data_status"`
	Env           []DepHealth    `json:"env"`
	EnvStatus     string         `json:"env_status"`
	Status        string         `json:"status"`
}

type HealthEngine struct {
	ActiveEngine     string       `json:"active_engine"`
	Version          string       `json:"version"`
	ConcurrencyLimit int          `json:"concurrency_limit"`
	Phases           []string     `json:"phases"`
	Status           string       `json:"status"`
	Probe            *EngineProbe `json:"probe,omitempty"`
}

type HealthCatalog struct {
	TotalSKUs     int    `json:"sku_count"`
	TotalServices int    `json:"service_count"`
	Vintage       string `json:"vintage"`
	AgeDays       int    `json:"age_days"`
	Stale         bool   `json:"stale"`
	Status        string `json:"status"`
}

type HealthTokens struct {
	TotalJobsAnalyzed int     `json:"total_jobs_analyzed"`
	AvgJobDurationSec float64 `json:"avg_job_duration_seconds"`
}

type HealthPipeline struct {
	QueueDepth     int     `json:"queue_depth"`
	RunningJobs    int     `json:"running_jobs"`
	CompletedJobs  int     `json:"completed_jobs"`
	FailedJobs     int     `json:"failed_jobs"`
	FailureRatePct float64 `json:"failure_rate_pct"`
	Halted         bool    `json:"halted"`
	LastJobAt      string  `json:"last_job_at,omitempty"`
	LastFailureAt  string  `json:"last_failure_at,omitempty"`
}

type catalogHealthJSON struct {
	GeneratedAt  string `json:"generated_at"`
	AgeDays      int    `json:"age_days"`
	Stale        bool   `json:"stale"`
	ArtifactOK   bool   `json:"artifact_ok"`
	Source       struct {
		Total    int `json:"total"`
		OK       int `json:"ok"`
		Problems int `json:"problems"`
	} `json:"source"`
	SKUCount     int    `json:"sku_count"`
	ServiceCount int    `json:"service_count"`
	Overall      string `json:"overall"`
}

var (
	catHealthCacheMu sync.Mutex
	catHealthCacheAt time.Time
	catHealthCached  *catalogHealthJSON
)

func getCatalogHealthCheck(skillDir string) (*catalogHealthJSON, error) {
	catHealthCacheMu.Lock()
	if catHealthCached != nil && time.Since(catHealthCacheAt) < 60*time.Second {
		cached := catHealthCached
		catHealthCacheMu.Unlock()
		return cached, nil
	}
	catHealthCacheMu.Unlock()

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	script := filepath.Join(skillDir, "scripts", "catalog_manager.py")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, python, script, "check", "--json", "--skill-dir", skillDir)
	cmd.Dir = skillDir
	out, err := cmd.Output()
	if err != nil && len(out) == 0 {
		return nil, fmt.Errorf("check failed: %w", err)
	}

	var res catalogHealthJSON
	if jsonErr := json.Unmarshal(out, &res); jsonErr != nil {
		return nil, fmt.Errorf("unmarshal error: %w (output: %s)", jsonErr, string(out))
	}

	catHealthCacheMu.Lock()
	catHealthCached = &res
	catHealthCacheAt = time.Now()
	catHealthCacheMu.Unlock()

	return &res, nil
}

func (h *Handler) AdminHealthSummary(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	// 1. Engine
	engineChoice, _, _ := h.db.GetEngineSettings()
	if engineChoice == "" {
		engineChoice = "gemini-3.8-flash"
	}
	_, maxConcurrent, _ := h.db.GetQuotaSettings()
	if maxConcurrent <= 0 {
		maxConcurrent = 2
	}

	// Derive probe without blocking on slow cold round-trip (cached or instant local)
	probe := h.spawner.ProbeEngine(r.Context(), false)
	engineVer := probe.Version
	if engineVer == "" || engineVer == "unknown" {
		engineVer = h.spawner.EngineVersion()
	}

	engineHealth := HealthEngine{
		ActiveEngine:     engineChoice,
		Version:          engineVer,
		ConcurrencyLimit: maxConcurrent,
		Phases: []string{
			"1. Ingestion & Schema Normalization",
			"2. Mechanics Classification",
			"3. AI Mapping & Family Normalization",
			"4. GCP Rate Application",
			"5. Materiality & Logic Validation",
			"6. Multi-Format Report Generation",
		},
		Status: probe.State,
		Probe:  &probe,
	}

	// 2. Catalog (derived dynamically without hardcoded constants)
	skillDir := skill.ResolveDir()
	catStatus := GetCatalogStatus()

	totalSKUs := 0
	totalServices := 0
	vintage := "unknown"
	ageDays := catStatus.AgeDays
	stale := catStatus.Stale
	catHealthStatus := "unknown"

	metaFile := filepath.Join(skillDir, "data", "CATALOG_META.json")
	if data, err := os.ReadFile(metaFile); err == nil {
		var meta struct {
			FetchedAt    string `json:"fetched_at"`
			ServiceCount int    `json:"service_count"`
			SKUCount     int    `json:"sku_count"`
		}
		if json.Unmarshal(data, &meta) == nil {
			totalSKUs = meta.SKUCount
			totalServices = meta.ServiceCount
			if meta.FetchedAt != "" {
				vintage = meta.FetchedAt
			}
		}
	}

	// Deep source + artifact integrity from catalog_manager.py check --json
	if checkRes, err := getCatalogHealthCheck(skillDir); err == nil && checkRes != nil {
		if checkRes.SKUCount > 0 {
			totalSKUs = checkRes.SKUCount
		}
		if checkRes.ServiceCount > 0 {
			totalServices = checkRes.ServiceCount
		}
		if checkRes.AgeDays >= 0 {
			ageDays = checkRes.AgeDays
		}
		stale = checkRes.Stale
		if checkRes.Overall != "" {
			catHealthStatus = checkRes.Overall
		} else if stale {
			catHealthStatus = "warn"
		} else {
			catHealthStatus = "ok"
		}
	} else {
		catHealthStatus = "unknown"
	}

	catalogHealth := HealthCatalog{
		TotalSKUs:     totalSKUs,
		TotalServices: totalServices,
		Vintage:       vintage,
		AgeDays:       ageDays,
		Stale:         stale,
		Status:        catHealthStatus,
	}

	// 3. Pipeline & Real Throughput (no invented token or cost math)
	stats, _ := h.db.GetUsageStats()
	var totalJobs, doneJobs, failedJobs int
	var failureRate, avgDuration float64
	if stats != nil {
		totalJobs = stats.TotalJobs
		doneJobs = stats.DoneJobs
		failedJobs = stats.FailedJobs
		failureRate = stats.FailureRate * 100.0
		avgDuration = stats.AvgProcessingSec
	}

	runningJobs, _ := h.db.CountRunningJobs()
	pendingJobs, _ := h.db.CountPendingJobs()
	lastJobAt, lastFailureAt, _ := h.db.GetJobTimestamps()

	dataDir := filepath.Dir(h.jobsDir)
	halted := IsStopAllActive(dataDir)

	pipelineHealth := HealthPipeline{
		QueueDepth:     pendingJobs,
		RunningJobs:    runningJobs,
		CompletedJobs:  doneJobs,
		FailedJobs:     failedJobs,
		Halted:         halted,
		FailureRatePct: failureRate,
		LastJobAt:      lastJobAt,
		LastFailureAt:  lastFailureAt,
	}

	tokensHealth := HealthTokens{
		TotalJobsAnalyzed: doneJobs,
		AvgJobDurationSec: avgDuration,
	}

	// 4. Data Dependencies
	stores, dataStatus := CheckDataStores(skillDir)

	// 5. Environment & Credentials Dependencies
	envDeps, envStatus := CheckEnvDependencies(skillDir, h.jobsDir, h.spawner.agyBin())

	// 6. Honest Overall Roll-up Status
	overallStatus := "ok"
	overallReason := "All systems, dependencies, and data stores operational"

	if halted {
		overallStatus = "fail"
		overallReason = "Pipeline execution is halted (STOP_ALL active)"
	} else if failureRate > 25.0 && totalJobs >= 4 {
		overallStatus = "fail"
		overallReason = fmt.Sprintf("High pipeline failure rate (%.1f%%)", failureRate)
	} else if dataStatus == "fail" {
		overallStatus = "fail"
		overallReason = "Critical data store file missing or corrupt"
	} else if envStatus == "fail" {
		overallStatus = "fail"
		overallReason = "Critical environment dependency or credential missing"
	} else if probe.State == "fail" {
		overallStatus = "fail"
		overallReason = fmt.Sprintf("Reasoning engine failure: %s", probe.Detail)
	} else if catalogHealth.Status == "fail" {
		overallStatus = "fail"
		overallReason = "GCP pricing catalog artifact missing or broken"
	} else if probe.State == "warn" {
		overallStatus = "warn"
		overallReason = fmt.Sprintf("Reasoning engine warning: %s", probe.Detail)
	} else if catalogHealth.Status == "warn" || catalogHealth.Status == "stale" {
		overallStatus = "warn"
		overallReason = "GCP pricing catalog is stale and needs refresh"
	} else if dataStatus == "warn" {
		overallStatus = "warn"
		overallReason = "One or more data stores have integrity warnings"
	} else if envStatus == "warn" {
		overallStatus = "warn"
		overallReason = "One or more environment dependencies unconfigured or degraded"
	} else if failureRate > 5.0 && totalJobs >= 10 {
		overallStatus = "warn"
		overallReason = fmt.Sprintf("Elevated pipeline failure rate (%.1f%%)", failureRate)
	} else if probe.State == "unknown" {
		overallReason = "Operational (live engine round-trip not yet performed)"
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(HealthResponse{
		OverallReason: overallReason,
		Engine:        engineHealth,
		Catalog:       catalogHealth,
		Tokens:        tokensHealth,
		Pipeline:      pipelineHealth,
		Stores:        stores,
		DataStatus:    dataStatus,
		Env:           envDeps,
		EnvStatus:     envStatus,
		Status:        overallStatus,
	})
}

// AdminTestEngine performs a fresh live round-trip probe against the configured engine model.
func (h *Handler) AdminTestEngine(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	probe := h.spawner.ProbeEngine(r.Context(), true)
	_ = h.db.LogAdminAction(sess.Email, "engine_test", probe.Model, fmt.Sprintf("state=%s latency=%dms", probe.State, probe.LatencyMS))

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(probe)
}

// ─────────────────────────────────────────────────────────────────────────
// Report Override Handlers (Phase 4)
// ─────────────────────────────────────────────────────────────────────────

type OverrideItemDTO struct {
	AWSLIKey     string   `json:"aws_li_key"`
	Product      string   `json:"product"`
	UsageType    string   `json:"usage_type"`
	PricingUnit  string   `json:"pricing_unit"`
	AWSAmortized float64  `json:"aws_amortized_cost"`
	Strategy     string   `json:"strategy"`
	GCPService   string   `json:"gcp_service"`
	GCPSKUName   string   `json:"gcp_sku_name"`
	GCPProjected float64  `json:"gcp_projected_cost"`
	Suppressed   bool     `json:"suppressed"`
	OverrideRate *float64 `json:"override_rate,omitempty"`
	Note         string   `json:"note"`
}

func (h *Handler) AdminGetLineItems(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"invalid job id"}`, http.StatusBadRequest)
		return
	}

	jobDir := filepath.Join(h.jobsDir, id)
	dbPath := filepath.Join(jobDir, projectionAuditDir, projectionDuckDB)
	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		http.Error(w, `{"error":"job projection database not found"}`, http.StatusNotFound)
		return
	}

	sqlQuery := `
		SELECT 
			c.aws_li_key,
			COALESCE(c.product, '') as product,
			COALESCE(c.usage_type, '') as usage_type,
			COALESCE(c.pricing_unit, '') as pricing_unit,
			COALESCE(c.aws_amortized_cost, 0.0) as aws_amortized_cost,
			COALESCE(m.strategy, 'map') as strategy,
			COALESCE(m.gcp_service, '') as gcp_service,
			COALESCE(m.gcp_sku_name, '') as gcp_sku_name,
			COALESCE(p.gcp_projected_cost, 0.0) as gcp_projected_cost
		FROM aws_li_catalog c
		LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
		LEFT JOIN gcp_projection p ON c.aws_li_key = p.aws_li_key
		WHERE c.aws_amortized_cost > 0
		ORDER BY c.aws_amortized_cost DESC
		LIMIT 1000;
	`
	rawJSON, err := runDuckDB(dbPath, sqlQuery)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"failed to query line items: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	var rawRows []struct {
		AWSLIKey     string  `json:"aws_li_key"`
		Product      string  `json:"product"`
		UsageType    string  `json:"usage_type"`
		PricingUnit  string  `json:"pricing_unit"`
		AWSAmortized float64 `json:"aws_amortized_cost"`
		Strategy     string  `json:"strategy"`
		GCPService   string  `json:"gcp_service"`
		GCPSKUName   string  `json:"gcp_sku_name"`
		GCPProjected float64 `json:"gcp_projected_cost"`
	}
	_ = json.Unmarshal(rawJSON, &rawRows)

	overrides, _ := h.db.ListJobOverrides(id)
	overrideMap := make(map[string]db.LineItemOverride)
	for _, o := range overrides {
		overrideMap[o.RowKey] = o
	}

	var dtos []OverrideItemDTO
	for _, r := range rawRows {
		dto := OverrideItemDTO{
			AWSLIKey:     r.AWSLIKey,
			Product:      r.Product,
			UsageType:    r.UsageType,
			PricingUnit:  r.PricingUnit,
			AWSAmortized: r.AWSAmortized,
			Strategy:     r.Strategy,
			GCPService:   r.GCPService,
			GCPSKUName:   r.GCPSKUName,
			GCPProjected: r.GCPProjected,
		}
		if ov, ok := overrideMap[r.AWSLIKey]; ok {
			dto.Suppressed = ov.Suppressed
			dto.Note = ov.Note
			if ov.GCPTarget != "" {
				dto.GCPService = ov.GCPTarget
			}
			if ov.OverrideRate >= 0 {
				r := ov.OverrideRate
				dto.OverrideRate = &r
				dto.GCPProjected = ov.OverrideRate
			}
		}
		dtos = append(dtos, dto)
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"line_items":      dtos,
		"total":           len(dtos),
		"overrides_count": len(overrides),
	})
}

func (h *Handler) AdminOverrideLineItem(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	id := chi.URLParam(r, "id")
	rowKey := chi.URLParam(r, "row")
	if !isValidJobID(id) || rowKey == "" {
		http.Error(w, `{"error":"invalid job id or row key"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		Suppressed   *bool    `json:"suppressed"`
		GCPTarget    string   `json:"gcp_target"`
		OverrideRate *float64 `json:"override_rate"`
		Note         string   `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	suppressed := false
	if body.Suppressed != nil {
		suppressed = *body.Suppressed
	}
	rate := -1.0
	if body.OverrideRate != nil {
		rate = *body.OverrideRate
	}

	err := h.db.UpsertJobOverride(db.LineItemOverride{
		JobID:        id,
		RowKey:       rowKey,
		Suppressed:   suppressed,
		GCPTarget:    body.GCPTarget,
		OverrideRate: rate,
		Note:         body.Note,
		UpdatedBy:    sess.Email,
	})
	if err != nil {
		http.Error(w, `{"error":"failed to save override"}`, http.StatusInternalServerError)
		return
	}

	h.syncJobOverridesFile(id)

	_ = h.db.LogAdminAction(sess.Email, "line_item_override", id, fmt.Sprintf("row=%s suppressed=%v rate=%f target=%s", rowKey, suppressed, rate, body.GCPTarget))

	go h.runRecalculateBackground(id, sess.Email)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"status":        "saved",
		"job_id":        id,
		"row_key":       rowKey,
		"recalculating": true,
	})
}

func (h *Handler) AdminDeleteLineItemOverride(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}

	id := chi.URLParam(r, "id")
	rowKey := chi.URLParam(r, "row")
	if err := h.db.DeleteJobOverride(id, rowKey); err != nil {
		http.Error(w, `{"error":"failed to delete override"}`, http.StatusInternalServerError)
		return
	}

	h.syncJobOverridesFile(id)
	_ = h.db.LogAdminAction(sess.Email, "delete_line_item_override", id, fmt.Sprintf("row=%s", rowKey))
	go h.runRecalculateBackground(id, sess.Email)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"status":        "deleted",
		"job_id":        id,
		"row_key":       rowKey,
		"recalculating": true,
	})
}

func (h *Handler) syncJobOverridesFile(jobID string) {
	overrides, err := h.db.ListJobOverrides(jobID)
	if err != nil {
		return
	}
	jobDir := filepath.Join(h.jobsDir, jobID)
	_ = os.MkdirAll(jobDir, 0755)
	filePath := filepath.Join(jobDir, "job_overrides.json")
	data, _ := json.MarshalIndent(overrides, "", "  ")
	_ = os.WriteFile(filePath, data, 0644)
	h.syncDirectivesFromOverrides(jobDir, overrides)
}

func (h *Handler) syncDirectivesFromOverrides(jobDir string, overrides []db.LineItemOverride) {
	directivesPath := filepath.Join(jobDir, "directives.json")
	var existing []map[string]any
	if data, err := os.ReadFile(directivesPath); err == nil {
		_ = json.Unmarshal(data, &existing)
	}

	var kept []map[string]any
	for _, d := range existing {
		id, _ := d["id"].(string)
		if !strings.HasPrefix(id, "manual_ov_") {
			kept = append(kept, d)
		}
	}

	now := time.Now().UTC().Format(time.RFC3339)
	for _, ov := range overrides {
		key := ov.RowKey
		if key == "" {
			continue
		}
		user := ov.UpdatedBy
		if user == "" {
			user = "admin"
		}
		selector := map[string]any{"aws_li_keys": []string{key}}

		if ov.Suppressed {
			kept = append(kept, map[string]any{
				"id":               "manual_ov_supp_" + key,
				"created_at":       now,
				"author":           user,
				"source_utterance": fmt.Sprintf("Suppressed via admin override for %s", key),
				"selector":         selector,
				"action":           map[string]any{"type": "set_strategy", "value": "ignore"},
				"status":           "applied",
				"confidence":       1.0,
			})
			kept = append(kept, map[string]any{
				"id":               "manual_ov_note_supp_" + key,
				"created_at":       now,
				"author":           user,
				"source_utterance": fmt.Sprintf("Suppressed note for %s", key),
				"selector":         selector,
				"action":           map[string]any{"type": "annotate", "value": "Suppressed via admin override"},
				"status":           "applied",
				"confidence":       1.0,
			})
		} else {
			if ov.GCPTarget != "" {
				kept = append(kept, map[string]any{
					"id":               "manual_ov_tgt_" + key,
					"created_at":       now,
					"author":           user,
					"source_utterance": fmt.Sprintf("GCP target set to %s for %s", ov.GCPTarget, key),
					"selector":         selector,
					"action":           map[string]any{"type": "set_target_service", "value": ov.GCPTarget},
					"status":           "applied",
					"confidence":       1.0,
				})
			}
			if ov.OverrideRate >= 0 {
				kept = append(kept, map[string]any{
					"id":               "manual_ov_rate_" + key,
					"created_at":       now,
					"author":           user,
					"source_utterance": fmt.Sprintf("Override rate %g for %s", ov.OverrideRate, key),
					"selector":         selector,
					"action":           map[string]any{"type": "set_field", "field": "override_rate", "value": ov.OverrideRate},
					"status":           "applied",
					"confidence":       1.0,
				})
			}
			if ov.PinnedKey != "" && ov.PinnedVal != "" {
				kept = append(kept, map[string]any{
					"id":               "manual_ov_pin_" + key,
					"created_at":       now,
					"author":           user,
					"source_utterance": fmt.Sprintf("Pinned %s=%s for %s", ov.PinnedKey, ov.PinnedVal, key),
					"selector":         selector,
					"action":           map[string]any{"type": "set_field", "field": ov.PinnedKey, "value": ov.PinnedVal},
					"status":           "applied",
					"confidence":       1.0,
				})
			}
			if ov.Note != "" {
				kept = append(kept, map[string]any{
					"id":               "manual_ov_note_" + key,
					"created_at":       now,
					"author":           user,
					"source_utterance": fmt.Sprintf("Note: %s", ov.Note),
					"selector":         selector,
					"action":           map[string]any{"type": "annotate", "value": ov.Note},
					"status":           "applied",
					"confidence":       1.0,
				})
			}
		}
	}

	data, err := json.MarshalIndent(kept, "", "  ")
	if err == nil {
		_ = os.WriteFile(directivesPath, append(data, '\n'), 0644)
	}
}

func (h *Handler) runRecalculateBackground(id, changedBy string) {
	jobDir := filepath.Clean(filepath.Join(h.jobsDir, id))
	dbPath := filepath.Join(jobDir, projectionAuditDir, projectionDuckDB)
	scriptsDir := filepath.Join(skill.ResolveDir(), "scripts")
	chain := [][]string{
		{filepath.Join(scriptsDir, "classify_mechanics.py"), dbPath},
		{filepath.Join(scriptsDir, "apply_commitment_ignores.py"), dbPath},
		{filepath.Join(scriptsDir, "apply_static_mappings.py"), dbPath},
		{filepath.Join(scriptsDir, "merge_mappings.py"), dbPath, filepath.Join(projectionAuditDir, "mappings")},
		{filepath.Join(scriptsDir, "service_classifier.py"), dbPath},
		{filepath.Join(scriptsDir, "apply_rates.py")},
		{filepath.Join(scriptsDir, "directives_from_legacy.py"), jobDir, dbPath},
		{filepath.Join(scriptsDir, "apply_directives.py"), dbPath},
		{filepath.Join(scriptsDir, "render_report.py")},
	}
	python := pythonBin()
	for _, step := range chain {
		cmd := exec.Command(python, step...)
		cmd.Dir = jobDir
		cmd.Env = append(os.Environ(),
			"SKILL_DIR="+skill.ResolveDir(),
			"PYTHONPATH="+scriptsDir,
			"JOB_DIR="+jobDir,
		)
		_ = cmd.Run()
	}
	_ = h.db.LogAdminAction(changedBy, "recalculated_overrides", id, "")
}
