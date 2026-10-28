package jobs_test

import (
	"encoding/json"
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
