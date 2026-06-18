package jobs_test

import (
	"os"
	"path/filepath"
	"testing"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeClaude writes a minimal report.html and exits 0
const fakeClaude = `#!/bin/bash
echo "fake claude running"
echo '<div id="aws-total-spend">12345.00</div>' > report.html
exit 0`

func TestSpawner_Success(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-1")
	os.MkdirAll(jobDir, 0755)
	// write fake input file
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	// write fake claude script
	claudePath := filepath.Join(dir, "claude")
	os.WriteFile(claudePath, []byte(fakeClaude), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		ClaudePath: claudePath,
	})

	result := s.Run(jobDir, ".csv")
	require.NoError(t, result.Err)
	assert.InDelta(t, 12345.0, result.AWSSpend, 0.01)

	// report.html should exist
	_, err := os.Stat(filepath.Join(jobDir, "report.html"))
	assert.NoError(t, err)
}

func TestSpawner_Failure(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-fail")
	os.MkdirAll(jobDir, 0755)

	// claude that exits with error
	claudePath := filepath.Join(dir, "claude-fail")
	os.WriteFile(claudePath, []byte("#!/bin/bash\necho 'error' >&2\nexit 1"), 0755)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		ClaudePath: claudePath,
	})

	result := s.Run(jobDir, ".csv")
	assert.Error(t, result.Err)
}
