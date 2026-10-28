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

func TestPhase5_ExportsMultiFormatAndSecurity(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	spawner := jobs.NewSpawner(jobs.SpawnerConfig{})
	spawner.SetDB(d)
	h := jobs.NewHandler(d, jobsDir, spawner, nil, jobs.NotifyConfig{}, adminEmails)

	aliceEmail := "alice@company.com"
	bobEmail := "bob@competitor.com"

	require.NoError(t, d.UpsertUserSeen(aliceEmail, "viewer"))
	require.NoError(t, d.UpsertUserSeen(bobEmail, "viewer"))

	jobID := "00000000-0000-0000-0000-000000000055"
	require.NoError(t, d.CreateJob(jobID, aliceEmail, "AliceCorp", ".csv", "sess-55"))
	require.NoError(t, d.UpdateJobDone(jobID, 120.50))

	// Set up job directory with dummy html report and dummy input
	jobPath := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(jobPath, 0755))
	htmlReportPath := filepath.Join(jobPath, "report.html")
	require.NoError(t, os.WriteFile(htmlReportPath, []byte("<html><body>AliceCorp GCP Report</body></html>"), 0644))
	billPath := filepath.Join(jobPath, "input.csv")
	require.NoError(t, os.WriteFile(billPath, []byte("lineItem/ProductCode,lineItem/UnblendedCost\nAWS,120.50\n"), 0644))

	t.Run("Test 1: Exporting a job you do not own returns 404 Not Found", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "GET", "/api/jobs/"+jobID+"/download?format=html", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.Download(rec, req)

		assert.Equal(t, http.StatusNotFound, rec.Code)
		var resp map[string]interface{}
		_ = json.Unmarshal(rec.Body.Bytes(), &resp)
		assert.Equal(t, "not found", resp["error"])

		// Also for json format
		reqJSON, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "GET", "/api/jobs/"+jobID+"/download?format=json", nil)
		reqJSON = reqJSON.WithContext(withChiID(reqJSON.Context(), jobID))
		recJSON := httptest.NewRecorder()
		h.Download(recJSON, reqJSON)

		assert.Equal(t, http.StatusNotFound, recJSON.Code)
	})

	t.Run("Test 2: Owner can download HTML report", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "GET", "/api/jobs/"+jobID+"/download", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.Download(rec, req)

		assert.Equal(t, http.StatusOK, rec.Code)
		assert.Contains(t, rec.Header().Get("Content-Type"), "text/html")
		assert.Contains(t, rec.Header().Get("Content-Disposition"), "AliceCorp-gcp-estimate.html")
		assert.Contains(t, rec.Body.String(), "AliceCorp GCP Report")
	})

	t.Run("Test 3: Owner can download original bill file", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "GET", "/api/jobs/"+jobID+"/download?format=bill", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.Download(rec, req)

		assert.Equal(t, http.StatusOK, rec.Code)
		assert.Contains(t, rec.Header().Get("Content-Disposition"), "AliceCorp-original-bill.csv")
		assert.Contains(t, rec.Body.String(), "lineItem/ProductCode")
	})
}
