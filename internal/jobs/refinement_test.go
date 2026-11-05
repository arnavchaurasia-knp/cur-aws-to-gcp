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

func TestRefinementHandlers_Lifecycle(t *testing.T) {
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
	jobID := "22222222-2222-2222-2222-222222222222"

	require.NoError(t, d.UpsertUserSeen(aliceEmail, "viewer"))
	require.NoError(t, d.UpsertUserSeen(bobEmail, "viewer"))
	require.NoError(t, d.CreateJob(jobID, aliceEmail, "AliceCorp", ".csv", "sess-1"))
	require.NoError(t, d.UpdateJobDone(jobID, 100.0))

	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("dummy input"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "report.html"), []byte("<html>Original Report</html>"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "projection-audit", "projection.duckdb"), []byte("duckdb data"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "assumptions.json"), []byte("{}"), 0644))
	require.NoError(t, os.WriteFile(filepath.Join(jobDir, "directives.json"), []byte("[]"), 0644))

	t.Run("Access control: Bob cannot access Alice's refinement endpoints", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "GET", "/api/jobs/"+jobID+"/refinement/thread", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.GetRefinementThread(rec, req)
		assert.Equal(t, http.StatusNotFound, rec.Code)

		body := `{"utterance": "map RDS to Cloud SQL"}`
		reqProp, _ := http.NewRequestWithContext(ctxWithFullSession(bobEmail, "Bob", "viewer"), "POST", "/api/jobs/"+jobID+"/refinement/propose", bytes.NewBufferString(body))
		reqProp = reqProp.WithContext(withChiID(reqProp.Context(), jobID))
		recProp := httptest.NewRecorder()
		h.ProposeRefinement(recProp, reqProp)
		assert.Equal(t, http.StatusNotFound, recProp.Code)
	})

	t.Run("Get initial empty thread and directives", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "GET", "/api/jobs/"+jobID+"/refinement/thread", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.GetRefinementThread(rec, req)
		assert.Equal(t, http.StatusOK, rec.Code)

		var resp struct {
			JobID              string `json:"job_id"`
			Messages           []any  `json:"messages"`
			OpenClarifications []any  `json:"open_clarifications"`
			ProposedDirectives []any  `json:"proposed_directives"`
			Directives         []any  `json:"directives"`
		}
		require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &resp))
		assert.Equal(t, jobID, resp.JobID)
		assert.Empty(t, resp.Messages)
		assert.Empty(t, resp.Directives)
	})

	t.Run("Apply refinement snapshots baselines", func(t *testing.T) {
		req, _ := http.NewRequestWithContext(ctxWithFullSession(aliceEmail, "Alice", "viewer"), "POST", "/api/jobs/"+jobID+"/refinement/apply", nil)
		req = req.WithContext(withChiID(req.Context(), jobID))
		rec := httptest.NewRecorder()
		h.ApplyRefinement(rec, req)
		assert.Equal(t, http.StatusOK, rec.Code)

		// Check baseline files were created
		assert.FileExists(t, filepath.Join(jobDir, "report.baseline.html"))
		assert.FileExists(t, filepath.Join(jobDir, "projection-audit", "projection.baseline.duckdb"))
		assert.FileExists(t, filepath.Join(jobDir, "assumptions.baseline.json"))
		assert.FileExists(t, filepath.Join(jobDir, "directives.baseline.json"))
	})
}
