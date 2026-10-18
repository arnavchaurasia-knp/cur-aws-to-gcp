package jobs_test

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeAGY records its args, writes a minimal report.html, and exits 0
const fakeAGY = `#!/bin/bash
echo "$@" > args.txt
echo "fake agy running"
echo '<div id="aws-total-spend">12345.00</div>' > report.html
exit 0`

func writeFakeAGY(t *testing.T, dir string) string {
	if runtime.GOOS == "windows" {
		p := filepath.Join(dir, "agy.bat")
		content := "@echo off\r\necho %* > args.txt\r\necho fake agy running\r\necho ^<div id=\"aws-total-spend\"^>12345.00^</div^> > report.html\r\nexit /b 0\r\n"
		require.NoError(t, os.WriteFile(p, []byte(content), 0755))
		return p
	}
	p := filepath.Join(dir, "agy")
	require.NoError(t, os.WriteFile(p, []byte(fakeAGY), 0755))
	return p
}

func writeFailAGY(t *testing.T, dir string) string {
	if runtime.GOOS == "windows" {
		p := filepath.Join(dir, "agy-fail.bat")
		require.NoError(t, os.WriteFile(p, []byte("@echo off\r\necho error 1>&2\r\nexit /b 1\r\n"), 0755))
		return p
	}
	p := filepath.Join(dir, "agy-fail")
	require.NoError(t, os.WriteFile(p, []byte("#!/bin/bash\necho 'error' >&2\nexit 1"), 0755))
	return p
}

func TestSpawner_Success(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-1")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	agyPath := writeFakeAGY(t, dir)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		AGYPath: agyPath,
	})

	result := s.Run(jobDir, ".csv")
	require.NoError(t, result.Err)
	assert.InDelta(t, 12345.0, result.AWSSpend, 0.01)

	_, err := os.Stat(filepath.Join(jobDir, "report.html"))
	assert.NoError(t, err)
}

// AGY does not accept a caller-supplied session ID for fresh starts
// (--conversation is resume-only). Verify the session ID is accepted by the
// API but not forwarded to the agy binary as --conversation.
func TestSpawner_SessionIDNotForwardedOnFreshRun(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-sid")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	agyPath := writeFakeAGY(t, dir)

	s := jobs.NewSpawner(jobs.SpawnerConfig{AGYPath: agyPath})

	const sid = "11111111-2222-3333-4444-555555555555"
	result := s.RunWithSession(jobDir, ".csv", sid)
	require.NoError(t, result.Err)

	args, err := os.ReadFile(filepath.Join(jobDir, "args.txt"))
	require.NoError(t, err)
	assert.NotContains(t, string(args), "--conversation")
	assert.NotContains(t, string(args), sid)
}

func TestSpawner_NoConversationFlagOnFreshRun(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-nosid")
	os.MkdirAll(jobDir, 0755)
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	agyPath := writeFakeAGY(t, dir)

	s := jobs.NewSpawner(jobs.SpawnerConfig{AGYPath: agyPath})

	result := s.Run(jobDir, ".csv")
	require.NoError(t, result.Err)

	args, err := os.ReadFile(filepath.Join(jobDir, "args.txt"))
	require.NoError(t, err)
	assert.NotContains(t, string(args), "--conversation")
}

func TestSpawner_Failure(t *testing.T) {
	dir := t.TempDir()
	jobDir := filepath.Join(dir, "job-fail")
	os.MkdirAll(jobDir, 0755)

	agyPath := writeFailAGY(t, dir)

	s := jobs.NewSpawner(jobs.SpawnerConfig{
		AGYPath: agyPath,
	})

	result := s.Run(jobDir, ".csv")
	assert.Error(t, result.Err)
}

// A global STOP_ALL drain flag must block NEW spawns, not just refinement.
// Uses the production dir layout (DataDir/jobs/<id>) because the STOP_ALL
// sentinel lives in DataDir — a shallower lookup (DataDir/jobs/STOP_ALL) would
// never find it, which was the bug this test guards against.
func TestSpawner_StopAllBlocksStart(t *testing.T) {
	dataDir := t.TempDir()
	jobsDir := filepath.Join(dataDir, "jobs")
	jobDir := filepath.Join(jobsDir, "job-stopall")
	require.NoError(t, os.MkdirAll(jobDir, 0755))
	os.WriteFile(filepath.Join(jobDir, "input.csv"), []byte("header,data"), 0644)

	agyPath := writeFakeAGY(t, dataDir)
	s := jobs.NewSpawner(jobs.SpawnerConfig{AGYPath: agyPath})

	// Without STOP_ALL the guard must NOT fire (no "halted" error short-circuit).
	require.False(t, jobs.IsStopAllActive(dataDir))

	// Set the drain flag in DataDir, then a new Start must be refused.
	require.NoError(t, os.WriteFile(filepath.Join(dataDir, "STOP_ALL"), []byte("halt"), 0644))
	require.True(t, jobs.IsStopAllActive(dataDir))

	_, err := s.Start(jobDir, ".csv", "")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "STOP_ALL")
}
