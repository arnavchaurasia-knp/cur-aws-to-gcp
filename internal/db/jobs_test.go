package db_test

import (
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func newTestDB(t *testing.T) *db.DB {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	t.Cleanup(func() { d.Close() })
	return d
}

func TestCreateAndGetJob(t *testing.T) {
	d := newTestDB(t)
	err := d.CreateJob("job-1", "rep@google.com", "Acme Corp", ".csv", "")
	require.NoError(t, err)

	job, err := d.GetJob("job-1")
	require.NoError(t, err)
	assert.Equal(t, "job-1", job.ID)
	assert.Equal(t, "rep@google.com", job.Owner)
	assert.Equal(t, "Acme Corp", job.Prospect)
	assert.Equal(t, "pending", job.Status)
	assert.Equal(t, ".csv", job.InputExt)
}

func TestListJobsByOwner(t *testing.T) {
	d := newTestDB(t)
	d.CreateJob("j1", "alice@google.com", "Acme", ".csv", "")
	d.CreateJob("j2", "bob@google.com", "Globex", ".zip", "")
	d.CreateJob("j3", "alice@google.com", "Initech", ".csv", "")

	jobs, err := d.ListJobsByOwner("alice@google.com")
	require.NoError(t, err)
	assert.Len(t, jobs, 2)
}

func TestUpdateJobStatus(t *testing.T) {
	d := newTestDB(t)
	d.CreateJob("j1", "rep@google.com", "Acme", ".csv", "")
	err := d.UpdateJobDone("j1", 48320.50)
	require.NoError(t, err)

	job, _ := d.GetJob("j1")
	assert.Equal(t, "done", job.Status)
	assert.InDelta(t, 48320.50, *job.AWSSpend, 0.01)
}

func TestUpdateJobFailed(t *testing.T) {
	d := newTestDB(t)
	d.CreateJob("j1", "rep@google.com", "Acme", ".csv", "")
	err := d.UpdateJobFailed("j1", "process exited with code 1")
	require.NoError(t, err)

	job, _ := d.GetJob("j1")
	assert.Equal(t, "failed", job.Status)
	assert.Equal(t, "process exited with code 1", job.Error)
}

func TestUpdateJobCancelled(t *testing.T) {
	d := newTestDB(t)
	d.CreateJob("j1", "rep@google.com", "Acme", ".csv", "")
	err := d.UpdateJobCancelled("j1")
	require.NoError(t, err)

	job, _ := d.GetJob("j1")
	assert.Equal(t, "cancelled", job.Status)
}

func TestListNonTerminalJobs_ExcludesCancelled(t *testing.T) {
	d := newTestDB(t)
	d.CreateJob("j-pending", "rep@google.com", "Acme", ".csv", "")
	d.CreateJob("j-running", "rep@google.com", "Acme", ".csv", "")
	d.CreateJob("j-done", "rep@google.com", "Acme", ".csv", "")
	d.CreateJob("j-failed", "rep@google.com", "Acme", ".csv", "")
	d.CreateJob("j-cancelled", "rep@google.com", "Acme", ".csv", "")

	_ = d.UpdateJobRunning("j-running")
	_ = d.UpdateJobDone("j-done", 100)
	_ = d.UpdateJobFailed("j-failed", "err")
	_ = d.UpdateJobCancelled("j-cancelled")

	nonTerm, err := d.ListNonTerminalJobs()
	require.NoError(t, err)
	assert.Len(t, nonTerm, 2)
	ids := map[string]bool{}
	for _, j := range nonTerm {
		ids[j.ID] = true
	}
	assert.True(t, ids["j-pending"])
	assert.True(t, ids["j-running"])
	assert.False(t, ids["j-cancelled"])
	assert.False(t, ids["j-done"])
	assert.False(t, ids["j-failed"])
}
