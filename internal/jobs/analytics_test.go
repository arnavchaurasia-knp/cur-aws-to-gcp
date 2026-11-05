package jobs_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func buildAnalyticsTestServer(t *testing.T, database *db.DB, jobsDir string) http.Handler {
	t.Helper()
	h := jobs.NewHandler(database, jobsDir, nil, nil, jobs.NotifyConfig{}, []string{"admin@facets.cloud"})
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			ctx := context.WithValue(req.Context(), auth.ExportedCtxKey{}, &auth.Session{
				Email: "admin@facets.cloud",
				Role:  "admin",
			})
			next.ServeHTTP(w, req.WithContext(ctx))
		})
	})
	r.Get("/api/admin/analytics", h.AdminAnalytics)
	r.Get("/api/admin/analytics/composition", h.AdminAnalyticsComposition)
	r.Get("/api/admin/analytics/quality", h.AdminAnalyticsQuality)
	r.Get("/api/admin/analytics/methodology", h.AdminAnalyticsMethodology)
	r.Post("/api/admin/analytics/rebuild", h.AdminAnalyticsRebuild)
	r.Post("/api/admin/analytics/jobs/{id}/refresh", h.AdminAnalyticsRefreshJob)
	return r
}

func createAnalyticsFixtureJob(t *testing.T, jobsDir, jobID string, includeDuckDB bool) string {
	t.Helper()
	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobDir, "projection-audit"), 0755))

	if includeDuckDB {
		dbPath := filepath.Join(jobDir, "projection-audit", "projection.duckdb")
		sql := `
CREATE TABLE aws_li_catalog (
  aws_li_key VARCHAR PRIMARY KEY,
  product VARCHAR,
  canonical_service VARCHAR,
  usage_type VARCHAR,
  aws_amortized_cost DOUBLE,
  is_workload BOOLEAN,
  public_on_demand_cost DOUBLE,
  sp_ri_discount_pct DOUBLE,
  aws_region VARCHAR,
  gcp_region VARCHAR
);
INSERT INTO aws_li_catalog VALUES
  ('k1', 'Amazon Elastic Compute Cloud', 'ec2', 'BoxUsage:t3.micro', 500.0, true, 600.0, 0.16, 'us-east-1', 'us-east1'),
  ('k2', 'Amazon Simple Storage Service', 's3', 'TimedStorage-ByteHrs', 100.0, true, 100.0, 0.0, 'us-east-1', 'us-east1'),
  ('k3', 'AWS Support', 'support', 'SupportFee', 50.0, false, 50.0, 0.0, 'global', 'global');

CREATE TABLE aws_li_to_gcp_li (
  aws_li_key VARCHAR,
  strategy VARCHAR,
  gcp_service VARCHAR,
  decided_by VARCHAR
);
INSERT INTO aws_li_to_gcp_li VALUES
  ('k1', 'map', 'Compute Engine', 'apply_static_mappings.py'),
  ('k2', 'map', 'Cloud Storage', 'apply_static_mappings.py'),
  ('k3', 'ignore', 'Support', 'apply_static_mappings.py');

CREATE VIEW gcp_projection AS
SELECT 'k1' AS aws_li_key, 400.0 AS gcp_projected_cost, 300.0 AS gcp_cost_1yr_cud, 200.0 AS gcp_cost_3yr_cud, true AS is_workload
UNION ALL
SELECT 'k2' AS aws_li_key, 80.0 AS gcp_projected_cost, 70.0 AS gcp_cost_1yr_cud, 60.0 AS gcp_cost_3yr_cud, true AS is_workload
UNION ALL
SELECT 'k3' AS aws_li_key, 0.0 AS gcp_projected_cost, 0.0 AS gcp_cost_1yr_cud, 0.0 AS gcp_cost_3yr_cud, false AS is_workload;

CREATE TABLE run_results (
  run_id TEXT PRIMARY KEY,
  ts_utc TIMESTAMP,
  run_type TEXT,
  instruction TEXT,
  aws_total DOUBLE,
  gcp_od DOUBLE,
  gcp_1yr_cud DOUBLE,
  gcp_3yr_cud DOUBLE,
  report_html TEXT,
  report_md TEXT,
  summary_md TEXT,
  mapped_rows INTEGER,
  passthroughs INTEGER,
  confidence TEXT
);
INSERT INTO run_results VALUES
  ('r1', '2026-11-01 00:00:00', 'initial', NULL, 650.0, 480.0, 370.0, 260.0, 'report.html', 'report.md', NULL, 2, 0, 'high');
`
		pyScript := `import duckdb, sys
c = duckdb.connect(sys.argv[1])
c.execute(sys.argv[2])
c.close()
`
		cmd := exec.Command("python", "-c", pyScript, dbPath, sql)
		out, err := cmd.CombinedOutput()
		require.NoErrorf(t, err, "duckdb fixture creation failed: %s", string(out))
	}

	return jobDir
}

func TestAnalytics_EndToEnd(t *testing.T) {
	tempDir := t.TempDir()
	jobsDir := filepath.Join(tempDir, "jobs")
	require.NoError(t, os.MkdirAll(jobsDir, 0755))

	database, err := db.Open(filepath.Join(tempDir, "test.db"))
	require.NoError(t, err)
	t.Cleanup(func() { _ = database.Close() })

	jobID := "analytics-e2e-job-1"
	require.NoError(t, database.CreateJob(jobID, "dev@facets.cloud", "Test Prospect", "csv", "sess-1"))
	gcpCost := 480.0
	require.NoError(t, database.UpdateJobDoneWithGCPOD(jobID, 650.0, &gcpCost))

	jobDir := createAnalyticsFixtureJob(t, jobsDir, jobID, true)

	// 1. Compute and upsert rollup
	job, err := database.GetJob(jobID)
	require.NoError(t, err)
	rollup, breakdowns, err := jobs.ComputeJobAnalytics(job, jobDir, database)
	require.NoError(t, err)
	require.NotNil(t, rollup)
	assert.InDelta(t, 650.0, rollup.AWSSpend, 0.001)
	require.NotNil(t, rollup.GCPOD)
	assert.InDelta(t, 480.0, *rollup.GCPOD, 0.001)
	assert.Equal(t, "high", rollup.ConfidenceBand)
	assert.NotEmpty(t, breakdowns)

	require.NoError(t, database.UpsertJobAnalytics(rollup, breakdowns))

	// 2. Test API endpoints
	handler := buildAnalyticsTestServer(t, database, jobsDir)

	// GET /api/admin/analytics (Lens A)
	req := httptest.NewRequest("GET", "/api/admin/analytics?window=30d", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	var summary db.AnalyticsSummary
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &summary))
	assert.Equal(t, 1, summary.AnalyzedJobs)
	assert.InDelta(t, 650.0, summary.TotalAWSSpend, 0.001)
	assert.InDelta(t, 480.0, summary.TotalGCPOD, 0.001)
	assert.InDelta(t, 170.0, summary.TotalSavings, 0.001)

	// GET /api/admin/analytics/composition (Lens B)
	req = httptest.NewRequest("GET", "/api/admin/analytics/composition?dimension=service", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	var comp db.CompositionResult
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &comp))
	assert.Equal(t, "service", comp.Dimension)
	assert.NotEmpty(t, comp.Items)

	// GET /api/admin/analytics/quality (Lens C)
	req = httptest.NewRequest("GET", "/api/admin/analytics/quality", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	var quality db.QualityResult
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &quality))
	assert.Equal(t, 3, quality.TotalRows)
	assert.Equal(t, 2, quality.MappedRows)

	// GET /api/admin/analytics/methodology (Lens D)
	req = httptest.NewRequest("GET", "/api/admin/analytics/methodology", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
	var meth db.MethodologyResult
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &meth))
	assert.InDelta(t, 500.0, meth.SPRIExposedUSD, 0.001) // k1 has public_on_demand_cost > aws_amortized_cost

	// POST /api/admin/analytics/jobs/{id}/refresh
	req = httptest.NewRequest("POST", "/api/admin/analytics/jobs/"+jobID+"/refresh", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)

	// POST /api/admin/analytics/rebuild
	req = httptest.NewRequest("POST", "/api/admin/analytics/rebuild", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}
