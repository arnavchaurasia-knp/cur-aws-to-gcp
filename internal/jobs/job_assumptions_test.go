package jobs_test

import (
	"bytes"
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

func TestJobAssumptions_GetAndRerun(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	spawner := jobs.NewSpawner(jobs.SpawnerConfig{})
	spawner.SetDB(d)
	h := jobs.NewHandler(d, jobsDir, spawner, nil, jobs.NotifyConfig{}, adminEmails)

	aliceEmail := "alice@example.com"
	bobEmail := "bob@example.com"
	jobID := "11111111-1111-1111-1111-111111111111"

	require.NoError(t, d.UpsertUserSeen(aliceEmail, "viewer"))
	require.NoError(t, d.UpsertUserSeen(bobEmail, "viewer"))
	require.NoError(t, d.CreateJob(jobID, aliceEmail, "AliceCorp", ".csv", "sess-1"))
	require.NoError(t, d.UpdateJobDone(jobID, 100.0))

	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("dummy input"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "report.html"), []byte("<html>Original Report</html>"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "projection-audit", "projection.duckdb"), []byte("duckdb data"), 0644))

	t.Run("1. Ownership: Bob cannot GET or POST assumptions on Alice's job", func(t *testing.T) {
		// GET
		req, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "GET", "/api/jobs/"+jobID+"/assumptions", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.GetJobAssumptions(rec, req)
		assert.Equal(t, http.StatusNotFound, rec.Code)

		// POST
		body := `{"overrides": {"lambda_mb_per_vcpu": 2048}}`
		req2, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "POST", "/api/jobs/"+jobID+"/assumptions/rerun", bytes.NewBufferString(body))
		req2 = req2.WithContext(withChiID(req2.Context(), jobID))
		rec2 := httptest.NewRecorder()
		h.RerunJobAssumptions(rec2, req2)
		assert.Equal(t, http.StatusNotFound, rec2.Code)
	})

	t.Run("2. GET returns initial defaults and no overrides", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "GET", "/api/jobs/"+jobID+"/assumptions", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.GetJobAssumptions(rec, req)
		assert.Equal(t, http.StatusOK, rec.Code)

		var resp struct {
			Assumptions    []jobs.JobAssumptionItem `json:"assumptions"`
			Categories     []string                 `json:"categories"`
			OverriddenKeys []string                 `json:"overridden_keys"`
			Version        string                   `json:"version"`
		}
		require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &resp))
		assert.NotEmpty(t, resp.Assumptions)
		assert.NotEmpty(t, resp.Categories)
		assert.Empty(t, resp.OverriddenKeys)
		for _, a := range resp.Assumptions {
			assert.False(t, a.IsOverridden)
			assert.NotEmpty(t, a.Key)
		}
	})

	t.Run("3. Clamp rejection: rejects unknown key or out-of-range value", func(t *testing.T) {
		// Unknown key
		bodyUnknown := `{"overrides": {"nonexistent_key_123": 42.0}}`
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "POST", "/api/jobs/"+jobID+"/assumptions/rerun", bytes.NewBufferString(bodyUnknown))
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.RerunJobAssumptions(rec, req)
		assert.Equal(t, http.StatusBadRequest, rec.Code)
		assert.Contains(t, rec.Body.String(), "unknown assumption key")

		// Out of range (min_value is 0.0, max_value is 0.99 for discount pct)
		bodyOutOfRange := `{"overrides": {"aws_spot_discount_pct": 5.50}}`
		req2, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "POST", "/api/jobs/"+jobID+"/assumptions/rerun", bytes.NewBufferString(bodyOutOfRange))
		req2 = req2.WithContext(withChiID(req2.Context(), jobID))
		rec2 := httptest.NewRecorder()
		h.RerunJobAssumptions(rec2, req2)
		assert.Equal(t, http.StatusBadRequest, rec2.Code)
		assert.Contains(t, rec2.Body.String(), "out of allowed range")
	})

	t.Run("4. Valid rerun: writes merged assumptions.json, preserves baseline, launches rerun", func(t *testing.T) {
		body := `{
			"overrides": {
				"lambda_mb_per_vcpu": 2048,
				"aws_spot_discount_pct": 0.85
			},
			"reason": "Test custom rerun"
		}`
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "POST", "/api/jobs/"+jobID+"/assumptions/rerun", bytes.NewBufferString(body))
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.RerunJobAssumptions(rec, req)
		assert.Equal(t, http.StatusAccepted, rec.Code)

		var resp struct {
			ID      string `json:"id"`
			Status  string `json:"status"`
			PID     int    `json:"pid"`
			Version string `json:"version"`
		}
		require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &resp))
		assert.Equal(t, jobID, resp.ID)
		assert.Equal(t, "running", resp.Status)
		assert.Contains(t, resp.Version, "custom")

		// Verify baseline files were preserved
		assert.FileExists(t, filepath.Join(jobDir, "report.baseline.html"))
		assert.FileExists(t, filepath.Join(jobDir, "projection-audit", "projection.baseline.duckdb"))
		baselineHTML, _ := os.ReadFile(filepath.Join(jobDir, "report.baseline.html"))
		assert.Contains(t, string(baselineHTML), "Original Report")

		// Verify assumptions.json was written with merged values
		snapshotBytes, err := os.ReadFile(filepath.Join(jobDir, "assumptions.json"))
		require.NoError(t, err)
		var snapshot struct {
			Version        string             `json:"version"`
			OverriddenKeys []string           `json:"overridden_keys"`
			Values         map[string]float64 `json:"values"`
			Reason         string             `json:"reason"`
			ChangedBy      string             `json:"changed_by"`
		}
		require.NoError(t, json.Unmarshal(snapshotBytes, &snapshot))
		assert.Equal(t, "Test custom rerun", snapshot.Reason)
		assert.Equal(t, aliceEmail, snapshot.ChangedBy)
		assert.Contains(t, snapshot.OverriddenKeys, "lambda_mb_per_vcpu")
		assert.Contains(t, snapshot.OverriddenKeys, "aws_spot_discount_pct")
		assert.Equal(t, 2048.0, snapshot.Values["lambda_mb_per_vcpu"])
		assert.Equal(t, 0.85, snapshot.Values["aws_spot_discount_pct"])
		// Unmodified keys remain at their global defaults
		assert.Equal(t, 4.0, snapshot.Values["glue_dpu_vcpu"])

		// Concurrent re-run rejection: job is now "running"
		reqConcurrent, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "POST", "/api/jobs/"+jobID+"/assumptions/rerun", bytes.NewBufferString(body))
		reqConcurrent = reqConcurrent.WithContext(withChiID(reqConcurrent.Context(), jobID))
		recConcurrent := httptest.NewRecorder()
		h.RerunJobAssumptions(recConcurrent, reqConcurrent)
		assert.Equal(t, http.StatusConflict, recConcurrent.Code)

		// Verify GET /assumptions now marks overridden keys
		reqGet, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "GET", "/api/jobs/"+jobID+"/assumptions", nil)
		reqGet = reqGet.WithContext(withChiID(reqGet.Context(), jobID))
		recGet := httptest.NewRecorder()
		h.GetJobAssumptions(recGet, reqGet)
		assert.Equal(t, http.StatusOK, recGet.Code)

		var getResp struct {
			Assumptions    []jobs.JobAssumptionItem `json:"assumptions"`
			OverriddenKeys []string                 `json:"overridden_keys"`
		}
		require.NoError(t, json.Unmarshal(recGet.Body.Bytes(), &getResp))
		assert.ElementsMatch(t, []string{"aws_spot_discount_pct", "lambda_mb_per_vcpu"}, getResp.OverriddenKeys)
		for _, a := range getResp.Assumptions {
			if a.Key == "lambda_mb_per_vcpu" {
				assert.True(t, a.IsOverridden)
				assert.Equal(t, 2048.0, a.Value)
			}
			if a.Key == "aws_spot_discount_pct" {
				assert.True(t, a.IsOverridden)
				assert.Equal(t, 0.85, a.Value)
			}
			if a.Key == "glue_dpu_vcpu" {
				assert.False(t, a.IsOverridden)
				assert.Equal(t, 4.0, a.Value)
			}
		}
	})
}
