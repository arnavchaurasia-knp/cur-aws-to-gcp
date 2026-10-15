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
	// A done job without a projection.duckdb (or with no run_results
	// table) must return an empty JSON array — not 404.
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

	// First cancel
	ctx := withChiID(ctxWithSession("rep@google.com", "Rep"), id)
	req, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	w := httptest.NewRecorder()
	h.Cancel(w, req)
	assert.Equal(t, http.StatusOK, w.Code)

	// Second cancel — idempotent no-op returns 200
	w2 := httptest.NewRecorder()
	req2, _ := http.NewRequestWithContext(ctx, "POST", "/api/jobs/"+id+"/cancel", nil)
	h.Cancel(w2, req2)
	assert.Equal(t, http.StatusOK, w2.Code)

	// Cancel when done — idempotent no-op returns 200
	idDone := uuid.New().String()
	d.CreateJob(idDone, "rep@google.com", "Acme Corp", ".csv", "")
	_ = d.UpdateJobDone(idDone, 123)
	ctxDone := withChiID(ctxWithSession("rep@google.com", "Rep"), idDone)
	reqDone, _ := http.NewRequestWithContext(ctxDone, "POST", "/api/jobs/"+idDone+"/cancel", nil)
	wDone := httptest.NewRecorder()
	h.Cancel(wDone, reqDone)
	assert.Equal(t, http.StatusOK, wDone.Code)
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

	// CancelAll
	req, _ := http.NewRequestWithContext(ctxWithSession("admin@google.com", "Admin"), "POST", "/api/admin/jobs/cancel-all", nil)
	w := httptest.NewRecorder()
	h.CancelAll(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	var cancelAllResp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &cancelAllResp)
	assert.True(t, cancelAllResp["stop_all"].(bool))

	// Verify STOP_ALL file exists
	assert.True(t, jobs.IsStopAllActive(dataDir))

	// Verify all three jobs are cancelled in DB and have sentinels
	for _, id := range []string{id1, id2, id3} {
		j, err := d.GetJob(id)
		require.NoError(t, err)
		assert.Equal(t, "cancelled", j.Status)
		assert.True(t, jobs.IsCancelled(filepath.Join(jobsDir, id)))
	}

	// Verify SystemState reports halted
	reqState, _ := http.NewRequest("GET", "/api/admin/system/state", nil)
	wState := httptest.NewRecorder()
	h.SystemState(wState, reqState)
	assert.Equal(t, http.StatusOK, wState.Code)
	var stateResp map[string]interface{}
	json.Unmarshal(wState.Body.Bytes(), &stateResp)
	assert.True(t, stateResp["halted"].(bool))

	// ResumeAll
	reqResume, _ := http.NewRequestWithContext(ctxWithSession("admin@google.com", "Admin"), "POST", "/api/admin/jobs/resume-all", nil)
	wResume := httptest.NewRecorder()
	h.ResumeAll(wResume, reqResume)

	assert.Equal(t, http.StatusOK, wResume.Code)
	assert.False(t, jobs.IsStopAllActive(dataDir))
}
