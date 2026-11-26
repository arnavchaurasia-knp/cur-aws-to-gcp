package jobs_test

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWatcher_WallClockCeiling_Exceeded(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test.db")
	database, err := db.Open(dbPath)
	require.NoError(t, err)
	defer database.Close()

	jobsDir := filepath.Join(tmpDir, "jobs")
	jobID := "job-ceiling-exceeded"
	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(jobDir, 0755))

	err = database.CreateJob(jobID, "tester@facets.cloud", "Ceiling Test Corp", ".csv", "sess-1")
	require.NoError(t, err)
	require.NoError(t, database.UpdateJobRunning(jobID))

	watcher := jobs.NewWatcher(database, nil, jobsDir, jobs.NotifyConfig{})
	watcher.SetWallClockLimit(10 * time.Minute)
	watcher.SetPollInterval(10 * time.Millisecond)

	var mu sync.Mutex
	fakeTime := time.Date(2026, 11, 26, 10, 0, 0, 0, time.UTC)
	watcher.SetClock(func() time.Time {
		mu.Lock()
		defer mu.Unlock()
		return fakeTime
	})

	isAlive := true
	watcher.SetPidAliveFunc(func(pid int) bool {
		mu.Lock()
		defer mu.Unlock()
		// First check: job start at 10:00.
		// Next check: jump time past 10 min ceiling (to 10:15)
		if fakeTime.Sub(time.Date(2026, 11, 26, 10, 0, 0, 0, time.UTC)) < 15*time.Minute {
			fakeTime = fakeTime.Add(15 * time.Minute)
		} else {
			// After ceiling triggers and killGroup is called, the process dies
			isAlive = false
		}
		return isAlive
	})

	// WatchOnce blocks until watcher terminates and finalizes
	watcher.WatchOnce(jobID, 12345)

	// Verify failure.txt exists
	failurePath := filepath.Join(jobDir, "failure.txt")
	require.FileExists(t, failurePath)
	data, err := os.ReadFile(failurePath)
	require.NoError(t, err)
	assert.Contains(t, string(data), "Job exceeded maximum wall-clock ceiling of 10m0s")

	// Verify DB status
	job, err := database.GetJob(jobID)
	require.NoError(t, err)
	assert.Equal(t, "failed", job.Status)
	assert.Contains(t, job.Error, "Job exceeded maximum wall-clock ceiling")
}

func TestWatcher_WallClockCeiling_NotExceeded(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "test.db")
	database, err := db.Open(dbPath)
	require.NoError(t, err)
	defer database.Close()

	jobsDir := filepath.Join(tmpDir, "jobs")
	jobID := "job-ceiling-ok"
	jobDir := filepath.Join(jobsDir, jobID)
	require.NoError(t, os.MkdirAll(jobDir, 0755))

	err = database.CreateJob(jobID, "tester@facets.cloud", "Ceiling OK Corp", ".csv", "sess-1")
	require.NoError(t, err)
	require.NoError(t, database.UpdateJobRunning(jobID))

	// Create a dummy report to simulate clean success
	htmlReport := filepath.Join(jobDir, "report.html")
	require.NoError(t, os.WriteFile(htmlReport, []byte("<div id='aws-total-spend'>$1,234.50</div>"), 0644))

	watcher := jobs.NewWatcher(database, nil, jobsDir, jobs.NotifyConfig{})
	watcher.SetWallClockLimit(90 * time.Minute)
	watcher.SetPollInterval(10 * time.Millisecond)

	fakeTime := time.Date(2026, 11, 26, 10, 0, 0, 0, time.UTC)
	watcher.SetClock(func() time.Time {
		return fakeTime
	})

	checks := 0
	watcher.SetPidAliveFunc(func(pid int) bool {
		checks++
		if checks >= 2 {
			return false // Process finished quickly
		}
		return true
	})

	watcher.WatchOnce(jobID, 12346)

	// Verify failure.txt was NOT written
	failurePath := filepath.Join(jobDir, "failure.txt")
	assert.NoFileExists(t, failurePath)

	// Verify DB status is done, not failed
	job, err := database.GetJob(jobID)
	require.NoError(t, err)
	assert.Equal(t, "done", job.Status)
	assert.Empty(t, job.Error)
}
