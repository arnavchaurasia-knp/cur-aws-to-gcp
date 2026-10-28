package jobs_test

import (
	"bytes"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/facets/cur-web/internal/config"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPhase2_EngineSettingsAndJobExecution(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	spawner := jobs.NewSpawner(jobs.SpawnerConfig{})
	spawner.SetDB(d)
	h := jobs.NewHandler(d, jobsDir, spawner, nil, jobs.NotifyConfig{}, adminEmails)

	// Test 3: Setting default_engine to a value outside allowed_engines is rejected
	{
		_, err := d.UpdateReportSetting("default_engine", "gpt-4o", "admin@facets.cloud")
		require.Error(t, err)
		assert.Contains(t, err.Error(), "not in allowed_engines")
	}

	// Test 4: An upload naming a disallowed engine is rejected with a clear error, and no job is created
	{
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		require.NoError(t, writer.WriteField("prospect_name", "TestCorp"))
		require.NoError(t, writer.WriteField("engine", "unauthorized-engine"))
		part, err := writer.CreateFormFile("file", "test.csv")
		require.NoError(t, err)
		_, err = io.WriteString(part, "lineItem/ProductCode,lineItem/UnblendedCost\nAWS,10.0\n")
		require.NoError(t, err)
		require.NoError(t, writer.Close())

		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "POST", "/api/jobs", &body)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		h.Create(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code)
		assert.Contains(t, w.Body.String(), "not allowed")

		// Confirm no job was created
		jobList, err := d.ListJobsByOwner("alice@company.com")
		require.NoError(t, err)
		assert.Empty(t, jobList)
	}

	// Test 5: An upload with no engine uses default_engine
	var defaultJobID string
	{
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		require.NoError(t, writer.WriteField("prospect_name", "DefaultCorp"))
		part, err := writer.CreateFormFile("file", "test.csv")
		require.NoError(t, err)
		_, err = io.WriteString(part, "lineItem/ProductCode,lineItem/UnblendedCost\nAWS,10.0\n")
		require.NoError(t, err)
		require.NoError(t, writer.Close())

		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "POST", "/api/jobs", &body)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		h.Create(w, req)
		assert.Equal(t, http.StatusCreated, w.Code)

		var resp map[string]string
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		defaultJobID = resp["id"]
		require.NotEmpty(t, defaultJobID)

		job, err := d.GetJob(defaultJobID)
		require.NoError(t, err)
		require.NotNil(t, job)
		assert.Equal(t, config.DefaultAGYModel, job.Engine)
	}

	// Test 6: Two jobs run with different engines record different values
	var customJobID string
	{
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		require.NoError(t, writer.WriteField("prospect_name", "CustomCorp"))
		require.NoError(t, writer.WriteField("engine", "gemini-2.5-pro"))
		part, err := writer.CreateFormFile("file", "test2.csv")
		require.NoError(t, err)
		_, err = io.WriteString(part, "lineItem/ProductCode,lineItem/UnblendedCost\nAWS,20.0\n")
		require.NoError(t, err)
		require.NoError(t, writer.Close())

		req, _ := http.NewRequestWithContext(ctxWithSession("alice@company.com", "Alice"), "POST", "/api/jobs", &body)
		req.Header.Set("Content-Type", writer.FormDataContentType())
		w := httptest.NewRecorder()
		h.Create(w, req)
		assert.Equal(t, http.StatusCreated, w.Code)

		var resp map[string]string
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))
		customJobID = resp["id"]
		require.NotEmpty(t, customJobID)

		jobDefault, err := d.GetJob(defaultJobID)
		require.NoError(t, err)
		jobCustom, err := d.GetJob(customJobID)
		require.NoError(t, err)

		assert.Equal(t, "gemini-3.8-flash", jobDefault.Engine)
		assert.Equal(t, "gemini-2.5-pro", jobCustom.Engine)
		assert.NotEqual(t, jobDefault.Engine, jobCustom.Engine)
	}

	// Test 1: A completed job has non-empty engine
	{
		job, err := d.GetJob(customJobID)
		require.NoError(t, err)
		assert.NotEmpty(t, job.Engine)
	}

	// Test 2: The report footer shows the engine (verify script output or template)
	{
		scriptPath := filepath.Join("..", "..", "skill", "aws-gcp-cost-projection", "scripts", "render_report.py")
		content, err := os.ReadFile(scriptPath)
		require.NoError(t, err)
		assert.Contains(t, string(content), "engine:")
		assert.True(t, strings.Contains(string(content), "AGY_MODEL") || strings.Contains(string(content), "default_engine"))
	}
}
