package db

import (
	"database/sql"
)

// JobLogDigest represents a compact post-mortem summary of a job run's logs,
// retained permanently in SQLite after ephemeral job directories expire.
type JobLogDigest struct {
	JobID      string `json:"job_id"`
	RunID      string `json:"run_id"`
	ErrorCount int    `json:"error_count"`
	WarnCount  int    `json:"warn_count"`
	FirstError string `json:"first_error"`
	LastLines  string `json:"last_lines"`
	RecordedAt string `json:"recorded_at"`
}

// SaveJobLogDigest persists a compact digest into job_log_digest.
func (d *DB) SaveJobLogDigest(digest JobLogDigest) error {
	_, err := d.conn.Exec(`
		INSERT OR REPLACE INTO job_log_digest
			(job_id, run_id, error_count, warn_count, first_error, last_lines, recorded_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
	`, digest.JobID, digest.RunID, digest.ErrorCount, digest.WarnCount, digest.FirstError, digest.LastLines)
	return err
}

// GetJobLogDigest returns the persisted log digest for a given job and run.
// Returns nil, nil if no digest exists.
func (d *DB) GetJobLogDigest(jobID, runID string) (*JobLogDigest, error) {
	row := d.conn.QueryRow(`
		SELECT job_id, run_id, error_count, warn_count, COALESCE(first_error, ''), COALESCE(last_lines, ''), COALESCE(recorded_at, '')
		FROM job_log_digest
		WHERE job_id = ? AND run_id = ?
	`, jobID, runID)

	var res JobLogDigest
	if err := row.Scan(&res.JobID, &res.RunID, &res.ErrorCount, &res.WarnCount, &res.FirstError, &res.LastLines, &res.RecordedAt); err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	return &res, nil
}
