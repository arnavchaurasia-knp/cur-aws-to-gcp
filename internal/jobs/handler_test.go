// internal/jobs/handler_test.go
package jobs_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func ctxWithSession(email, name string) context.Context {
	return context.WithValue(context.Background(), auth.ExportedCtxKey{}, &auth.Session{Email: email, Name: name})
}

func withChiID(ctx context.Context, id string) context.Context {
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", id)
	return context.WithValue(ctx, chi.RouteCtxKey, rctx)
}

func withChiIDAndToken(ctx context.Context, id, token string) context.Context {
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", id)
	rctx.URLParams.Add("token", token)
	return context.WithValue(ctx, chi.RouteCtxKey, rctx)
}

func TestListJobs_Empty(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	req, _ := http.NewRequestWithContext(ctxWithSession("rep@google.com", "Rep"), "GET", "/api/jobs", nil)
	w := httptest.NewRecorder()
	h.List(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var result []interface{}
	json.Unmarshal(w.Body.Bytes(), &result)
	assert.Len(t, result, 0)
}

func TestGetJob_NotOwner(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "other@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/jobs/"+id, nil)
	w := httptest.NewRecorder()
	h.GetByID(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestRuns_EmptyForLegacyJob(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/jobs/"+id+"/runs", nil)
	w := httptest.NewRecorder()
	h.Runs(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var result []interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &result))
	assert.Len(t, result, 0)
}

func TestRuns_NotOwner(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "other@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/jobs/"+id+"/runs", nil)
	w := httptest.NewRecorder()
	h.Runs(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestSummary_404WhenMissing(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/jobs/"+id+"/summary", nil)
	w := httptest.NewRecorder()
	h.Summary(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestGetJob_Owner(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme Corp", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/jobs/"+id, nil)
	w := httptest.NewRecorder()
	h.GetByID(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var job map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &job)
	assert.Equal(t, "Acme Corp", job["prospect"])
}

func TestCancel_Owner(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme Corp", ".csv", "")
	dir := t.TempDir()
	jobDir := filepath.Join(dir, id)
	os.MkdirAll(jobDir, 0755)

	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	w := httptest.NewRecorder()
	h.Cancel(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.True(t, jobs.IsCancelled(jobDir))

	job, err := d.GetJob(id)
	require.NoError(t, err)
	assert.Equal(t, "cancelled", job.Status)
}

func TestCancel_NotOwnerForbidden(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "other@google.com", "Acme Corp", ".csv", "")
	dir := t.TempDir()

	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	w := httptest.NewRecorder()
	h.Cancel(w, req)

	assert.Equal(t, http.StatusForbidden, w.Code)
}

func TestCancel_Idempotency(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme Corp", ".csv", "")
	dir := t.TempDir()
	jobDir := filepath.Join(dir, id)
	os.MkdirAll(jobDir, 0755)

	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	w := httptest.NewRecorder()
	h.Cancel(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	w2 := httptest.NewRecorder()
	req2, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	h.Cancel(w2, req2)
	assert.Equal(t, http.StatusOK, w2.Code)

	idDone := uuid.New().String()
	d.CreateJob(idDone, "rep@google.com", "Acme Corp", ".csv", "")
	_ = d.UpdateJobDone(idDone, 123)
	ctxDone := withChiID(ctxWithSession("rep@google.com", "Rep"), idDone)
	reqDone, _ := http.NewRequestWithContext(ctxDone, "POST", "/api/jobs/"+idDone+"/cancel", nil)
	wDone := httptest.NewRecorder()
	h.Cancel(wDone, reqDone)
	assert.Equal(t, http.StatusOK, wDone.Code)
}

func TestJobDiagnostics_NotAvailable(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/admin/jobs/"+id+"/diagnostics", nil)
	w := httptest.NewRecorder()
	h.AdminJobDiagnostics(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.False(t, resp["available"].(bool))
}

func TestJobDiagnostics_AvailableFromDB(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	summaryJSON := `{"workload_usd":10738.09,"error_usd":0.0,"error_pct_of_workload":0.0,"error_count":0,"warn_count":3,"checks_not_evaluated":0,"by_code":{"unit_price_outlier":{"level":"warn","count":1,"rows":1,"usd":6409.41,"sources":["ingest.py"]}}}`
	require.NoError(t, d.SaveJobDiagnostics(id, "initial", []byte(summaryJSON)))

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/admin/jobs/"+id+"/diagnostics", nil)
	w := httptest.NewRecorder()
	h.AdminJobDiagnostics(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.True(t, resp["available"].(bool))
	summary := resp["summary"].(map[string]interface{})
	assert.Equal(t, float64(3), summary["warn_count"])
	byCode := summary["by_code"].(map[string]interface{})
	upi := byCode["unit_price_outlier"].(map[string]interface{})
	assert.Equal(t, float64(1), upi["count"])
	assert.InDelta(t, 6409.41, upi["usd"], 0.01)
}

func TestJobDiagnostics_FilesMissing(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, id, "projection-audit"), 0755)
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/admin/jobs/"+id+"/diagnostics", nil)
	w := httptest.NewRecorder()
	h.AdminJobDiagnostics(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.False(t, resp["available"].(bool))
}

func TestJobDiagnosticRecords_SkipsMalformedLine(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme", ".csv", "")
	dir := t.TempDir()
	jobDir := filepath.Join(dir, id)
	os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755)
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)

	diagPath := filepath.Join(jobDir, "projection-audit", "diagnostics.jsonl")
	os.WriteFile(diagPath, []byte(`{"level":"warn","code":"unmapped_misc_spend","source":"classify_mechanics.py","message":"test","rows":1,"usd":42.0}
{bad json
{"level":"error","code":"capacity_check_skipped","source":"reconcile_capacity.py","message":"test2","rows":2,"usd":30.0}`), 0644)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/admin/jobs/"+id+"/diagnostics/records", nil)
	w := httptest.NewRecorder()
	h.AdminJobDiagnosticRecords(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.True(t, resp["available"].(bool))
	records := resp["records"].([]interface{})
	assert.Len(t, records, 2)
	assert.Equal(t, "unmapped_misc_spend", records[0].(map[string]interface{})["code"])
	assert.Equal(t, "capacity_check_skipped", records[1].(map[string]interface{})["code"])
}

// ---- Share (report link) tests ----

func newShareTestDB(t *testing.T) (*db.DB, string, *jobs.Handler) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	t.Cleanup(func() { d.Close() })
	id := uuid.New().String()
	d.CreateJob(id, "rep@google.com", "Acme Corp", ".csv", "")
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, id, "projection-audit"), 0755)
	h := jobs.NewHandler(d, dir, nil, nil, jobs.NotifyConfig{}, nil)
	return d, id, h
}

// TestShareExpiredTokenTreatedAsNotFound: expired token -> same "not found" as unknown token.
func TestShareExpiredTokenTreatedAsNotFound(t *testing.T) {
	d, id, h := newShareTestDB(t)
	token := "expiredtoken1234567890"
	past := time.Now().Add(-24 * time.Hour)
	err := d.CreateReportShare(token, id, "rep@google.com", past)
	require.NoError(t, err)

	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/reports/"+token, nil)
	w := httptest.NewRecorder()
	h.GetSharedReport(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
	assert.Equal(t, "not found", resp["error"])
}

// TestShareNullExpiresAtStillWorks: NULL expires_at means no expiry -> 200.
func TestShareNullExpiresAtStillWorks(t *testing.T) {
	d, id, h := newShareTestDB(t)
	token := "nullexpirestoken123"
	_, err := d.ExecForTest(
		`INSERT INTO report_shares (token, job_id, created_by, expires_at) VALUES (?, ?, ?, NULL)`,
		token, id, "rep@google.com",
	)
	require.NoError(t, err)

	ctx := withChiIDAndToken(ctxWithSession("rep@google.com", "Rep"), id, token)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/reports/"+token, nil)
	w := httptest.NewRecorder()
	h.GetSharedReport(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
}

// TestShareRevokedTokenStopsWorking: after DELETE /api/admin/shares/{token}, GET /api/reports/{token} -> 404.
func TestShareRevokedTokenStopsWorking(t *testing.T) {
	d, id, h := newShareTestDB(t)
	token := "revoketoken1234567890"
	err := d.CreateReportShare(token, id, "rep@google.com", time.Now().Add(90*24*time.Hour))
	require.NoError(t, err)

	ctx := withChiIDAndToken(ctxWithSession("admin@google.com", "Admin"), id, token)
	req, _ := http.NewRequestWithContext(ctx, "DELETE", "/api/admin/shares/"+token, nil)
	w := httptest.NewRecorder()
	h.AdminDeleteReportShare(w, req)
	assert.Equal(t, http.StatusNoContent, w.Code)

	req2, _ := http.NewRequestWithContext(ctx, "GET", "/api/reports/"+token, nil)
	w2 := httptest.NewRecorder()
	h.GetSharedReport(w2, req2)
	assert.Equal(t, http.StatusNotFound, w2.Code)
	var resp map[string]interface{}
	require.NoError(t, json.Unmarshal(w2.Body.Bytes(), &resp))
	assert.Equal(t, "not found", resp["error"])
}

// TestShareRevokeIsAudited: revoking a share writes an admin audit row.
func TestShareRevokeIsAudited(t *testing.T) {
	d, id, h := newShareTestDB(t)
	token := "revoketokenaudited12"
	err := d.CreateReportShare(token, id, "rep@google.com", time.Now().Add(90*24*time.Hour))
	require.NoError(t, err)

	ctx := withChiIDAndToken(ctxWithSession("admin@google.com", "Admin"), id, token)
	req, _ := http.NewRequestWithContext(ctx, "DELETE", "/api/admin/shares/"+token, nil)
	w := httptest.NewRecorder()
	h.AdminDeleteReportShare(w, req)
	assert.Equal(t, http.StatusNoContent, w.Code)

	entries, err := d.ListAuditLog(10, 0, "", "", nil, nil)
	require.NoError(t, err)
	require.NotEmpty(t, entries)
	found := false
	for _, e := range entries {
		if e.Action == "revoke_share" && e.TargetID == token && e.ActorEmail == "admin@google.com" {
			found = true
		}
	}
	assert.True(t, found, "expected a revoke_share audit row for %s", token)
}

// TestShareListMasksTokens: list endpoint masks tokens (first 6 chars only).
func TestShareListMasksTokens(t *testing.T) {
	d, id, h := newShareTestDB(t)
	token := "abcdef1234567890abcdef"
	err := d.CreateReportShare(token, id, "rep@google.com", time.Now().Add(90*24*time.Hour))
	require.NoError(t, err)

	ctx := withChiID(ctxWithSession("admin@google.com", "Admin"), id)
	req, _ := http.NewRequestWithContext(ctx, "GET", "/api/admin/shares", nil)
	w := httptest.NewRecorder()
	h.AdminListReportShares(w, req)
	assert.Equal(t, http.StatusOK, w.Code)
	var rows []map[string]interface{}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	require.Len(t, rows, 1)
	assert.Equal(t, "abcdef…", rows[0]["token"])
	assert.Equal(t, id, rows[0]["job_id"])
}

func TestCancelAll_And_ResumeAll(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	id1 := uuid.New().String()
	id2 := uuid.New().String()
	id3 := uuid.New().String()

	d.CreateJob(id1, "rep1@google.com", "Acme 1", ".csv", "")
	d.CreateJob(id2, "rep2@google.com", "Acme 2", ".csv", "")
	d.CreateJob(id3, "rep3@google.com", "Acme 3", ".csv", "")

	_ = d.UpdateJobRunning(id1)
	_ = d.UpdateJobRunning(id2)
	_ = d.UpdateJobRunning(id3)

	dataDir := t.TempDir()
	jobsDir := filepath.Join(dataDir, "jobs")
	os.MkdirAll(filepath.Join(jobsDir, id1), 0755)
	os.MkdirAll(filepath.Join(jobsDir, id2), 0755)
	os.MkdirAll(filepath.Join(jobsDir, id3), 0755)

	h := jobs.NewHandler(d, jobsDir, nil, nil, jobs.NotifyConfig{}, []string{"admin@google.com"})

	req, _ := http.NewRequestWithContext(ctxWithSession("admin@google.com", "Admin"), "POST", "/api/admin/jobs/cancel-all", nil)
	w := httptest.NewRecorder()
	h.CancelAll(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var cancelAllResp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &cancelAllResp)
	assert.True(t, cancelAllResp["stop_all"].(bool))

	assert.True(t, jobs.IsStopAllActive(dataDir))

	for _, id := range []string{id1, id2, id3} {
		j, err := d.GetJob(id)
		require.NoError(t, err)
		assert.Equal(t, "cancelled", j.Status)
		assert.True(t, jobs.IsCancelled(filepath.Join(jobsDir, id)))
	}

	reqState, _ := http.NewRequest("GET", "/api/admin/system/state", nil)
	wState := httptest.NewRecorder()
	h.SystemState(wState, reqState)
	assert.Equal(t, http.StatusOK, wState.Code)
	var stateResp map[string]interface{}
	json.Unmarshal(wState.Body.Bytes(), &stateResp)
	assert.True(t, stateResp["halted"].(bool))

	reqResume, _ := http.NewRequestWithContext(ctxWithSession("admin@google.com", "Admin"), "POST", "/api/admin/jobs/resume-all", nil)
	wResume := httptest.NewRecorder()
	h.ResumeAll(wResume, reqResume)

	assert.Equal(t, http.StatusOK, wResume.Code)
	assert.False(t, jobs.IsStopAllActive(dataDir))
}