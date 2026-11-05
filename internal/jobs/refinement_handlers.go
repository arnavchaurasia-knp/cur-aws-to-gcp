package jobs

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/skill"
	"github.com/go-chi/chi/v5"
)

func sanitizeUserFilename(s string) string {
	if s == "" {
		return ""
	}
	var b strings.Builder
	for _, r := range s {
		if r == '/' || r == '\\' || r == ':' || r == '*' || r == '?' || r == '"' || r == '<' || r == '>' || r == '|' || r <= ' ' {
			b.WriteRune('_')
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// GetRefinementThread returns the conversational refinement thread for a job.
func (h *Handler) GetRefinementThread(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	userEmail := "user"
	if sess != nil && sess.Email != "" {
		userEmail = sess.Email
	}
	safeUser := sanitizeUserFilename(userEmail)

	threadPath := filepath.Join(jobDir, "refinement_threads", safeUser+".json")
	if _, err := os.Stat(threadPath); err != nil {
		threadPath = filepath.Join(jobDir, "refinement_thread.json")
	}
	var threadMap map[string]any
	if data, err := os.ReadFile(threadPath); err == nil {
		_ = json.Unmarshal(data, &threadMap)
	}
	if threadMap == nil {
		threadMap = map[string]any{
			"job_id":              id,
			"user_id":             userEmail,
			"messages":            []any{},
			"open_clarifications": []any{},
			"proposed_directives": []any{},
		}
	}

	directivesPath := filepath.Join(jobDir, "directives", safeUser+".json")
	if _, err := os.Stat(directivesPath); err != nil {
		directivesPath = filepath.Join(jobDir, "directives.json")
	}
	var directives []any
	if data, err := os.ReadFile(directivesPath); err == nil {
		_ = json.Unmarshal(data, &directives)
	}
	if directives == nil {
		directives = []any{}
	}
	threadMap["directives"] = directives

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(threadMap)
}

// ProposeRefinement runs the directive translator to turn natural language into proposed directives.
func (h *Handler) ProposeRefinement(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		Utterance        string   `json:"utterance"`
		SelectedRowKeys  []string `json:"selected_row_keys"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	utterance := strings.TrimSpace(body.Utterance)
	if utterance == "" {
		http.Error(w, `{"error":"utterance cannot be empty"}`, http.StatusBadRequest)
		return
	}

	userEmail := "user"
	if sess != nil && sess.Email != "" {
		userEmail = sess.Email
	}

	scriptsDir := filepath.Join(skill.ResolveDir(), "scripts")
	scriptPath := filepath.Join(scriptsDir, "directive_translator.py")
	args := []string{scriptPath, jobDir, utterance, "--user-id", userEmail}
	if len(body.SelectedRowKeys) > 0 {
		args = append(args, "--selected-keys", strings.Join(body.SelectedRowKeys, ","))
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = jobDir
	cmd.Env = append(os.Environ(),
		"SKILL_DIR="+skill.ResolveDir(),
		"PYTHONPATH="+scriptsDir,
		"JOB_DIR="+jobDir,
		"USER_ID="+userEmail,
	)

	out, err := cmd.CombinedOutput()
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"translator failed: %s"}`, jsonEscape(string(out))), http.StatusInternalServerError)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write(out)
}

// ConfirmRefinement confirms a proposed directive and saves it into the ledger.
func (h *Handler) ConfirmRefinement(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		DirectiveID string `json:"directive_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.DirectiveID == "" {
		http.Error(w, `{"error":"directive_id required"}`, http.StatusBadRequest)
		return
	}

	userEmail := "user"
	if sess != nil && sess.Email != "" {
		userEmail = sess.Email
	}

	scriptsDir := filepath.Join(skill.ResolveDir(), "scripts")
	scriptPath := filepath.Join(scriptsDir, "refinement_thread.py")
	cmd := exec.Command(pythonBin(), scriptPath, jobDir, "confirm", body.DirectiveID, "--user-id", userEmail)
	cmd.Dir = jobDir
	cmd.Env = append(os.Environ(),
		"SKILL_DIR="+skill.ResolveDir(),
		"PYTHONPATH="+scriptsDir,
		"JOB_DIR="+jobDir,
		"USER_ID="+userEmail,
	)

	out, err := cmd.CombinedOutput()
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"confirm failed: %s"}`, jsonEscape(string(out))), http.StatusInternalServerError)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write(out)
}

// AnswerClarification answers an open clarification and confirms the corresponding directive.
func (h *Handler) AnswerClarification(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		ClarificationID string `json:"clarification_id"`
		Answer          string `json:"answer"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.ClarificationID == "" || body.Answer == "" {
		http.Error(w, `{"error":"clarification_id and answer required"}`, http.StatusBadRequest)
		return
	}

	userEmail := "user"
	if sess != nil && sess.Email != "" {
		userEmail = sess.Email
	}

	scriptsDir := filepath.Join(skill.ResolveDir(), "scripts")
	scriptPath := filepath.Join(scriptsDir, "refinement_thread.py")
	cmd := exec.Command(pythonBin(), scriptPath, jobDir, "clarify", body.ClarificationID, body.Answer, "--user-id", userEmail)
	cmd.Dir = jobDir
	cmd.Env = append(os.Environ(),
		"SKILL_DIR="+skill.ResolveDir(),
		"PYTHONPATH="+scriptsDir,
		"JOB_DIR="+jobDir,
		"USER_ID="+userEmail,
	)

	out, err := cmd.CombinedOutput()
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"clarify failed: %s"}`, jsonEscape(string(out))), http.StatusInternalServerError)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write(out)
}

// RevokeRefinement supersedes a confirmed directive in the ledger.
func (h *Handler) RevokeRefinement(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		DirectiveID string `json:"directive_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.DirectiveID == "" {
		http.Error(w, `{"error":"directive_id required"}`, http.StatusBadRequest)
		return
	}

	userEmail := "user"
	if sess != nil && sess.Email != "" {
		userEmail = sess.Email
	}

	scriptsDir := filepath.Join(skill.ResolveDir(), "scripts")
	scriptPath := filepath.Join(scriptsDir, "refinement_thread.py")
	cmd := exec.Command(pythonBin(), scriptPath, jobDir, "revoke", body.DirectiveID, "--user-id", userEmail)
	cmd.Dir = jobDir
	cmd.Env = append(os.Environ(),
		"SKILL_DIR="+skill.ResolveDir(),
		"PYTHONPATH="+scriptsDir,
		"JOB_DIR="+jobDir,
		"USER_ID="+userEmail,
	)

	out, err := cmd.CombinedOutput()
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"revoke failed: %s"}`, jsonEscape(string(out))), http.StatusInternalServerError)
		return
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.Write(out)
}

// ApplyRefinement triggers the refinement rerun pipeline.
func (h *Handler) ApplyRefinement(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir, ok := safeJobDir(h.jobsDir, id)
	if !ok {
		http.Error(w, `{"error":"invalid job directory"}`, http.StatusBadRequest)
		return
	}

	// Preserve baseline files before applying refinement changes
	copyFileIfNotExists(filepath.Join(jobDir, "report.html"), filepath.Join(jobDir, "report.baseline.html"))
	copyFileIfNotExists(
		filepath.Join(jobDir, projectionAuditDir, projectionDuckDB),
		filepath.Join(jobDir, projectionAuditDir, "projection.baseline.duckdb"),
	)
	copyFileIfNotExists(filepath.Join(jobDir, "assumptions.json"), filepath.Join(jobDir, "assumptions.baseline.json"))
	copyFileIfNotExists(filepath.Join(jobDir, "directives.json"), filepath.Join(jobDir, "directives.baseline.json"))

	email := "user"
	if sess != nil && sess.Email != "" {
		email = sess.Email
	}
	_ = h.db.LogAdminAction(email, "apply_refinement", id, "")
	go h.runRecalculateBackground(id, email)

	w.Header().Set(contentTypeHeader, contentTypeJSON)
	json.NewEncoder(w).Encode(map[string]any{
		"status":        "applied",
		"job_id":        id,
		"recalculating": true,
	})
}
