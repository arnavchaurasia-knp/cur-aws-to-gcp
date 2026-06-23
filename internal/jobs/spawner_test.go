package jobs_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeGemini records its args, writes a minimal report.html, and exits 0
const fakeGemini = `#!/bin/bash
echo "$@" > args.txt
echo "fake gemini running"
echo '<div id="aws-total-spend">12345.00</div>' > report.html
exit 0`

func TestSpawner_Success(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-1")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	geminiPath := filepath.Join(dir, "gemini")
	os.WriteFile(geminiPath, []byte(fakeGemini), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		GeminiPath: geminiPath,
	})

	result := s.Run(jobDir, ".csv")
	require.NoError(t, result.Err)
	assert.InDelta(t, 12345.0, result.AWSSpend, 0.01)

	_, err := os.Stat(filepath.Join(jobDir, "report.html"))
	assert.NoError(t, err)
}

func TestSpawner_PassesSessionID(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-sid")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	geminiPath := filepath.Join(dir, "gemini")
	os.WriteFile(geminiPath, []byte(fakeGemini), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{GeminiPath: geminiPath})

	const sid = "11111111-2222-3333-4444-555555555555"
	result := s.RunWithSession(jobDir, ".csv", sid)
	require.NoError(t, result.Err)

	args, err := os.ReadFile(filepath.Join(jobDir, "args.txt"))
	require.NoError(t, err)
	assert.Contains(t, string(args), "--session-id "+sid)
}

func TestSpawner_NoSessionIDFlagWhenEmpty(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-nosid")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	geminiPath := filepath.Join(dir, "gemini")
	os.WriteFile(geminiPath, []byte(fakeGemini), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{GeminiPath: geminiPath})

	result := s.Run(jobDir, ".csv") // empty session id
	require.NoError(t, result.Err)

	args, err := os.ReadFile(filepath.Join(jobDir, "args.txt"))
	require.NoError(t, err)
	assert.NotContains(t, string(args), "--session-id")
}

func TestSpawner_Failure(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-fail")
	os.MkdirAll(jobDir, 0755)

	geminiPath := filepath.Join(dir, "gemini-fail")
	os.WriteFile(geminiPath, []byte("#!/bin/bash\necho 'error' >&2\nexit 1"), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		GeminiPath: geminiPath,
	})

	result := s.Run(jobDir, ".csv")
	assert.Error(t, result.Err)
}
