package jobs_test

import (
	"encoding/json"
	"fmt"
	"strings"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPhase1_JobLogs_AudienceAndStreams(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	h := jobs.NewHandler(d, jobsDir, nil, nil, jobs.NotifyConfig{}, adminEmails)

	// Create test job owned by alice@company.com
	jobID := "00000000-0000-0000-0000-000000000001"
	err = d.CreateJob(jobID, "alice@company.com", "Acme", ".csv", "")
	require.NoError(t, err)

	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755))

	// Write mock logs
	agyLog := "2026-10-28T10:00:00 [agy] Starting engine...\n2026-10-28T10:01:00 [agy] Phase 1 done\n"
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "agy.log"), []byte(agyLog), 0644))

	debugLog := "2026-10-28T10:00:00  INFO      ingest.py  Reading CSV\n" +
		"2026-10-28T10:00:05  WARNING   rates.py   Missing SKU fallback used\n" +
		"2026-10-28T10:00:10  ERROR     report.py  Syntax warning in table\n" +
		"Unparseable custom stack trace line here\n"
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "projection-audit", "debug.log"), []byte(debugLog), 0644))

	diagJSONL := `{"code":"W001","level":"warning","message":"Missing rate"}` + "\n" +
		`{"code":"E001","level":"error","message":"Out of bounds"}` + "\n"
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "projection-audit", "diagnostics.jsonl"), []byte(diagJSONL), 0644))

	// Test 1: Owner reads own job's logs -> 200
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=pipeline", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		err := json.Unmarshal(w.Body.Bytes(), &resp)
		require.NoError(t, err)
		assert.True(t, resp.Available)
		assert.Equal(t, "live", resp.Source)
		assert.Equal(t, 4, resp.Total)
		assert.Len(t, resp.Entries, 4)
	}

	// Test 2: Non-owner, non-admin reads another user's logs -> 404, identical body to nonexistent
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("bob@other.com", "Bob"), "GET", "/api/jobs/"+jobID+"/logs", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusNotFound, w.Code)
		assert.JSONEq(t, `{"error":"not found"}`, w.Body.String())

		// Verify nonexistent job returns identical response
		nonExistentID := "00000000-0000-0000-0000-999999999999"
		reqMissing, _ := http.NewRequestWithContext(ctxWithSession("bob@other.com", "Bob"), "GET", "/api/jobs/"+nonExistentID+"/logs", nil)
		reqMissing = reqMissing.WithContext(withChiID(reqMissing.Context(), nonExistentID))
		wMissing := httptest.NewRecorder()
		h.JobLogs(wMissing, reqMissing)
		assert.Equal(t, http.StatusNotFound, wMissing.Code)
		assert.Equal(t, w.Body.String(), wMissing.Body.String())
	}

	// Test 3: Admin reads any job's logs -> 200
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("admin@facets.cloud", "Admin"), "GET", "/api/jobs/"+jobID+"/logs", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)
	}

	// Test 4: A facets employee who is NOT owner and NOT in adminEmails -> 404
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("charlie@facets.cloud", "Charlie"), "GET", "/api/jobs/"+jobID+"/logs", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusNotFound, w.Code)
		assert.JSONEq(t, `{"error":"not found"}`, w.Body.String())
	}

	// Test 5: ?stream=diagnostics returns parsed records; missing stream returns available:false with status 200
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=diagnostics", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		assert.True(t, resp.Available)
		assert.Equal(t, 2, resp.Total)
		assert.Len(t, resp.Records, 2)

		// Delete diagnostics file to verify missing stream handling
		require.NoError(t, os.Remove(filepath.Join(jobDir, "projection-audit", "diagnostics.jsonl")))
		w2 := httptest.NewRecorder()
		h.JobLogs(w2, req)
		assert.Equal(t, http.StatusOK, w2.Code)
		var resp2 jobs.LogResponse
		require.NoError(t, json.Unmarshal(w2.Body.Bytes(), &resp2))
		assert.False(t, resp2.Available)
	}

	// Test 6: ?level=error returns only errors; an unparseable line still appears
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=pipeline&level=error", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		assert.True(t, resp.Available)
		// Should include the ERROR line + the unparseable line
		assert.Equal(t, 2, resp.Total)
		hasError := false
		hasUnknown := false
		for _, e := range resp.Entries {
			if e.Level == "error" {
				hasError = true
			}
			if e.Level == "unknown" {
				hasUnknown = true
			}
		}
		assert.True(t, hasError, "expected error entry")
		assert.True(t, hasUnknown, "expected unknown/unparseable entry")
	}

	// Test 7: After job directory is deleted, the endpoint still returns the digest with source: "digest"
	{
		// First write a digest to DB
		digest := db.JobLogDigest{
			JobID:      jobID,
			RunID:      "initial",
			ErrorCount: 1,
			WarnCount:  2,
			FirstError: "Report failure",
			LastLines:  "Last line 1\nLast line 2",
		}
		require.NoError(t, d.SaveJobLogDigest(digest))

		// Delete job directory
		require.NoError(t, os.RemoveAll(jobDir))

		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		assert.True(t, resp.Available)
		assert.Equal(t, "digest", resp.Source)
		assert.True(t, resp.Truncated)
		assert.Equal(t, 2, resp.Total)
		assert.Equal(t, []string{"Last line 1", "Last line 2"}, resp.Lines)
	}
}


func TestWatcher_RecordJobLogDigest_DualStream(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	w := jobs.NewWatcher(d, nil, t.TempDir(), jobs.NotifyConfig{})
	jobID := "00000000-0000-0000-0000-000000000099"
	err = d.CreateJob(jobID, "alice@company.com", "Acme", ".csv", "")
	require.NoError(t, err)

	jobDir := t.TempDir()

	auditDir := filepath.Join(jobDir, "projection-audit")
	require.NoError(t, os.MkdirAll(auditDir, 0755))

	// 1. Write diagnostics.jsonl with 1 error and 1 warning
	diagContent := `{"ts":"2026-10-28T10:00:00","level":"error","code":"mapper_error","source":"apply_static_mappings.py","message":"mapper crashed"}
{"ts":"2026-10-28T10:00:01","level":"warn","code":"unmapped_misc","source":"classify_mechanics.py","message":"misc spend high"}
`
	require.NoError(t, os.WriteFile(filepath.Join(auditDir, "diagnostics.jsonl"), []byte(diagContent), 0644))

	// 2. Write debug.log with 1 warning and 1 info
	debugContent := `2026-10-28T10:00:02  INFO      ingest.py  Processing started
2026-10-28T10:00:03  WARNING   apply_static_mappings.py  SKU not found in cache
`
	require.NoError(t, os.WriteFile(filepath.Join(auditDir, "debug.log"), []byte(debugContent), 0644))

	// 3. Write agy.log with an unhandled fatal error and orchestrator output
	agyContent := `[orchestrator] starting phase 1
2026-10-28T10:00:02  INFO      ingest.py  Processing started
2026-10-28T10:00:03  WARNING   apply_static_mappings.py  SKU not found in cache
[diag] error mapper_error src=apply_static_mappings.py rows=1 usd=$10.00 :: mapper crashed
[SCRIPT] FATAL: Script failed (exit 1): python3 fail.py
`
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "agy.log"), []byte(agyContent), 0644))

	w.RecordJobLogDigest(jobID, "initial", jobDir)

	digest, err := d.GetJobLogDigest(jobID, "initial")
	require.NoError(t, err)
	require.NotNil(t, digest)

	// Errors: 1 from diagnostics.jsonl + 1 from agy.log ([SCRIPT] FATAL) = 2
	assert.Equal(t, 2, digest.ErrorCount)
	// Warnings: 1 from diagnostics.jsonl + 1 from debug.log = 2
	assert.Equal(t, 2, digest.WarnCount)
	// First error should be captured from diagnostics (earliest structured record)
	assert.Contains(t, digest.FirstError, "mapper crashed")
	// Last lines should be from the tail of agy.log
	assert.Contains(t, digest.LastLines, "[SCRIPT] FATAL")
}


func TestJobLogs_TailPaginationAndDownload(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	h := jobs.NewHandler(d, jobsDir, nil, nil, jobs.NotifyConfig{}, adminEmails)

	jobID := "00000000-0000-0000-0000-000000000002"
	err = d.CreateJob(jobID, "alice@company.com", "Acme", ".csv", "")
	require.NoError(t, err)

	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755))

	// Write 100 log lines to agy.log
	var engineLines []string
	for i := 1; i <= 100; i++ {
		engineLines = append(engineLines, fmt.Sprintf("Engine log line %03d", i))
	}
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "agy.log"), []byte(strings.Join(engineLines, "\n")), 0644))

	// 1. Test tail=true (limit 10): should return the last 10 lines (91-100), offset = 90, total = 100
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=engine&limit=10&tail=true", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		assert.Equal(t, 100, resp.Total)
		assert.Equal(t, 90, resp.Offset)
		assert.Equal(t, 10, len(resp.Lines))
		assert.Equal(t, "Engine log line 091", resp.Lines[0])
		assert.Equal(t, "Engine log line 100", resp.Lines[9])
	}

	// 2. Test pagination with explicit offset=20&limit=10
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=engine&limit=10&offset=20", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var resp jobs.LogResponse
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		assert.Equal(t, 100, resp.Total)
		assert.Equal(t, 20, resp.Offset)
		assert.Equal(t, 10, len(resp.Lines))
		assert.Equal(t, "Engine log line 021", resp.Lines[0])
	}

	// 3. Test download=true streams the raw file with Content-Disposition
	{
		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "GET", "/api/jobs/"+jobID+"/logs?stream=engine&download=true", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		w := httptest.NewRecorder()
		h.JobLogs(w, req)
		assert.Equal(t, http.StatusOK, w.Code)
		assert.Contains(t, w.Header().Get("Content-Disposition"), "attachment; filename=\"job-00000000-0000-0000-0000-000000000002-engine.log\"")
		assert.Equal(t, "text/plain; charset=utf-8", w.Header().Get("Content-Type"))
		assert.Contains(t, w.Body.String(), "Engine log line 001")
		assert.Contains(t, w.Body.String(), "Engine log line 100")
	}
}
