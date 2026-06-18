// internal/jobs/handler.go
package jobs

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/notify"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type NotifyConfig struct {
	Email    notify.EmailConfig
	SlackURL string
}

type Handler struct {
	db          *db.DB
	jobsDir     string
	spawner     *Spawner
	watcher     *Watcher
	notify      NotifyConfig
	adminEmails []string
}

func NewHandler(d *db.DB, jobsDir string, spawner *Spawner, watcher *Watcher, nc NotifyConfig, adminEmails []string) *Handler {
	return &Handler{db: d, jobsDir: jobsDir, spawner: spawner, watcher: watcher, notify: nc, adminEmails: adminEmails}
}

// canAccess returns true if sess owns the job OR is in the admin list.
// Centralized so every endpoint applies the same rule.
func (h *Handler) canAccess(sess *auth.Session, job *db.Job) bool {
	if sess == nil || job == nil {
		return false
	}
	return job.Owner == sess.Email || sess.IsAdmin(h.adminEmails)
}

func (h *Handler) Create(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	r.ParseMultipartForm(200 << 20) // 200 MB
	prospect := r.FormValue("prospect_name")
	if prospect == "" {
		http.Error(w, `{"error":"prospect_name required"}`, http.StatusBadRequest)
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		http.Error(w, `{"error":"file required"}`, http.StatusBadRequest)
		return
	}
	defer file.Close()

	jobID := uuid.New().String()
	ext := filepath.Ext(header.Filename)
	jobDir := filepath.Join(h.jobsDir, jobID)
	os.MkdirAll(jobDir, 0755)

	dst, err := os.Create(filepath.Join(jobDir, "input"+ext))
	if err != nil {
		http.Error(w, `{"error":"storage error"}`, http.StatusInternalServerError)
		return
	}
	io.Copy(dst, file)
	dst.Close()

	h.db.CreateJob(jobID, sess.Email, prospect, ext, jobID)

	go notify.PostJobSubmitted(h.notify.SlackURL, prospect, sess.Email, jobID)
	go h.runJob(jobID, jobDir, ext, sess)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"id": jobID, "status": "pending"})
}

func (h *Handler) runJob(jobID, jobDir, ext string, sess *auth.Session) {
	h.db.UpdateJobRunning(jobID)
	pid, err := h.spawner.Start(jobDir, ext, jobID)
	if err != nil {
		h.db.UpdateJobFailed(jobID, "spawn failed: "+err.Error())
		go notify.SendJobFailed(h.notify.Email, sess.Email, sess.Name, "")
		go notify.PostJobFailed(h.notify.SlackURL, "", sess.Email, jobID)
		return
	}
	h.db.UpdateJobPID(jobID, pid)
	h.watcher.Watch(jobID, pid)
}

func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	jobs, err := h.db.ListJobsByOwner(sess.Email)
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if jobs == nil {
		jobs = []*db.Job{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(jobs)
}

func (h *Handler) GetByID(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}

func (h *Handler) Retry(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if job.Status != "failed" {
		http.Error(w, `{"error":"can only retry a failed job"}`, http.StatusConflict)
		return
	}

	jobDir := filepath.Join(h.jobsDir, id)
	if err := wipeJobDir(jobDir); err != nil {
		http.Error(w, `{"error":"wipe failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}

	newSession := uuid.New().String()
	if err := h.db.ResetJobForRetry(id, newSession); err != nil {
		http.Error(w, `{"error":"db reset failed"}`, http.StatusInternalServerError)
		return
	}

	pid, err := h.spawner.Start(jobDir, job.InputExt, newSession)
	if err != nil {
		h.db.UpdateJobFailed(id, "retry spawn failed: "+err.Error())
		http.Error(w, `{"error":"spawn failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	h.db.UpdateJobPID(id, pid)
	go h.watcher.Watch(id, pid)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]any{"id": id, "status": "running", "pid": pid})
}

func (h *Handler) Refine(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if job.Status != "done" {
		http.Error(w, `{"error":"can only refine a completed job"}`, http.StatusConflict)
		return
	}
	if job.SessionID == "" {
		http.Error(w, `{"error":"job has no session_id; cannot resume"}`, http.StatusConflict)
		return
	}
	var body struct {
		Instruction string `json:"instruction"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	if len(body.Instruction) < 3 {
		http.Error(w, `{"error":"instruction must be at least 3 characters"}`, http.StatusBadRequest)
		return
	}

	jobDir := filepath.Join(h.jobsDir, id)
	pid, err := h.spawner.StartRefine(jobDir, job.SessionID, body.Instruction)
	if err != nil {
		http.Error(w, `{"error":"spawn failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	h.db.UpdateJobPID(id, pid)
	h.db.UpdateJobRunning(id)
	go h.watcher.WatchOnce(id, pid)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]any{"id": id, "status": "refining", "pid": pid})
}

func (h *Handler) Progress(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	jobDir := filepath.Join(h.jobsDir, id)
	p, _ := ReadProgress(jobDir, job.SessionID)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(p)
}

func (h *Handler) Download(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	if job.Status != "done" {
		http.Error(w, `{"error":"report not ready"}`, http.StatusConflict)
		return
	}

	jobDir := filepath.Join(h.jobsDir, id)
	runIDParam := r.URL.Query().Get("run_id")

	reportPath, runIDForFilename := resolveReportPath(jobDir, runIDParam)
	if reportPath == "" {
		http.Error(w, `{"error":"no report file found"}`, http.StatusNotFound)
		return
	}

	filename := fmt.Sprintf("%s-gcp-estimate.html", job.Prospect)
	if runIDForFilename != "" {
		filename = fmt.Sprintf("%s-gcp-estimate-%s.html", job.Prospect, runIDForFilename)
	}
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	w.Header().Set("Content-Type", "text/html")
	http.ServeFile(w, r, reportPath)
}

// resolveReportPath picks the report.html to serve for a Download request.
// Tries, in order:
//   1. Specific run_id from run_results (if runIDParam non-empty)
//   2. Latest row in run_results
//   3. projection-audit/report.html (legacy unsuffixed)
//   4. report.html at the job dir root (very old promotion-era jobs)
// Returns ("", "") if none of those exist on disk. The second return is
// the run_id used (empty for fallback paths) so the caller can embed it
// in Content-Disposition.
func resolveReportPath(jobDir, runIDParam string) (string, string) {
	dbPath := filepath.Join(jobDir, "projection-audit", "projection.duckdb")
	runs, _ := QueryRunResults(dbPath) // empty slice on missing db/table

	var pick *RunResult
	if runIDParam != "" {
		for i := range runs {
			if runs[i].RunID == runIDParam {
				pick = &runs[i]
				break
			}
		}
	} else if len(runs) > 0 {
		pick = &runs[0]
	}

	if pick != nil && pick.ReportHTML != nil && *pick.ReportHTML != "" {
		p := filepath.Join(jobDir, *pick.ReportHTML)
		if _, err := os.Stat(p); err == nil {
			return p, pick.RunID
		}
	}
	// Fallback chain for legacy jobs (none of which we expect to keep
	// hitting once BackfillRunResults has covered all existing jobs).
	if p := filepath.Join(jobDir, "projection-audit", "report.html"); fileExists(p) {
		return p, ""
	}
	if p := filepath.Join(jobDir, "report.html"); fileExists(p) {
		return p, ""
	}
	return "", ""
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// Runs returns the run_results history for a job, latest first. Returns
// an empty array (not 404) when the job has no run_results yet — the job
// itself still exists, there's just no run history (very fresh job or
// pre-backfill legacy).
func (h *Handler) Runs(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	dbPath := filepath.Join(h.jobsDir, id, "projection-audit", "projection.duckdb")
	runs, err := QueryRunResults(dbPath)
	if err != nil {
		http.Error(w, `{"error":"runs query failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	if runs == nil {
		runs = []RunResult{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(runs)
}

// Summary returns the markdown summary for a specific run (or the latest
// run if no run_id query param is provided). 404 when the row has a NULL
// summary_md (legacy/initial backfilled rows) or the file isn't on disk.
func (h *Handler) Summary(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	job, err := h.db.GetJob(id)
	if err != nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	jobDir := filepath.Join(h.jobsDir, id)
	dbPath := filepath.Join(jobDir, "projection-audit", "projection.duckdb")
	runs, err := QueryRunResults(dbPath)
	if err != nil {
		http.Error(w, `{"error":"runs query failed: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	runIDParam := r.URL.Query().Get("run_id")
	var pick *RunResult
	if runIDParam != "" {
		for i := range runs {
			if runs[i].RunID == runIDParam {
				pick = &runs[i]
				break
			}
		}
	} else if len(runs) > 0 {
		pick = &runs[0]
	}
	if pick == nil || pick.SummaryMD == nil || *pick.SummaryMD == "" {
		http.Error(w, `{"error":"summary not available"}`, http.StatusNotFound)
		return
	}
	p := filepath.Join(jobDir, *pick.SummaryMD)
	if _, err := os.Stat(p); err != nil {
		http.Error(w, `{"error":"summary file missing"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	http.ServeFile(w, r, p)
}

// AdminListAll returns every job across every owner. 403 for non-admins.
// Used by the /admin page in the UI.
func (h *Handler) AdminListAll(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if !sess.IsAdmin(h.adminEmails) {
		http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
		return
	}
	jobs, err := h.db.ListAllJobs()
	if err != nil {
		http.Error(w, `{"error":"db error"}`, http.StatusInternalServerError)
		return
	}
	if jobs == nil {
		jobs = []*db.Job{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(jobs)
}
