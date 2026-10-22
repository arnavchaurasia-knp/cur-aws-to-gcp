// internal/db/job_diagnostics.go
package db

import (
	"database/sql"
	"encoding/json"
	"fmt"
)

// JobDiagnosticsSummary represents the persisted run_diagnostics.json summary.
type JobDiagnosticsSummary struct {
	WorkloadUsd           float64            `json:"workload_usd"`
	ErrorUsd              float64            `json:"error_usd"`
	ErrorPctOfWorkload    float64            `json:"error_pct_of_workload"`
	ErrorCount            int                `json:"error_count"`
	WarnCount             int                `json:"warn_count"`
	ChecksNotEvaluated    int                `json:"checks_not_evaluated"`
	ByCode                map[string]JobDiagnosticsByCode `json:"by_code"`
}

// JobDiagnosticsByCode represents per-code aggregation within the summary.
type JobDiagnosticsByCode struct {
	Level    string `json:"level"`
	Count    int    `json:"count"`
	Rows     int    `json:"rows"`
	Usd      float64 `json:"usd"`
	Sources  []string `json:"sources"`
}

// JobDiagnosticsRow is a persisted job_diagnostics row with computed fields.
type JobDiagnosticsRow struct {
	JobID        string                 `json:"job_id"`
	RunID        string                 `json:"run_id"`
	Summary      *JobDiagnosticsSummary `json:"summary"`
	ErrorCount   int                    `json:"error_count"`
	WarnCount    int                    `json:"warn_count"`
	ErrorUsd     float64                `json:"error_usd"`
	WorkloadUsd  float64                `json:"workload_usd"`
	RecordedAt   string                 `json:"recorded_at"`
}

// SaveJobDiagnostics persists the run_diagnostics.json summary for a job run.
// Uses INSERT OR REPLACE so re-saving the same (job_id, run_id) pair replaces
// rather than duplicates.
func (d *DB) SaveJobDiagnostics(jobID, runID string, summary []byte) error {
	var summaryObj JobDiagnosticsSummary
	if err := json.Unmarshal(summary, &summaryObj); err != nil {
		return fmt.Errorf("parse summary JSON: %w", err)
	}

	_, execErr := d.conn.Exec(`
		INSERT OR REPLACE INTO job_diagnostics
			(job_id, run_id, summary_json, error_count, warn_count, error_usd, workload_usd, recorded_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, jobID, runID, string(summary), summaryObj.ErrorCount, summaryObj.WarnCount,
		summaryObj.ErrorUsd, summaryObj.WorkloadUsd)
	return execErr
}

// GetJobDiagnostics returns the persisted diagnostics summary for a job run.
// Returns nil,nil when no row exists.
func (d *DB) GetJobDiagnostics(jobID, runID string) (*JobDiagnosticsRow, error) {
	row := d.conn.QueryRow(`
		SELECT job_id, run_id, summary_json, error_count, warn_count, error_usd,
		       workload_usd, recorded_at
		FROM job_diagnostics
		WHERE job_id = ? AND run_id = ?
	`, jobID, runID)

	var jdr JobDiagnosticsRow
	var summaryJSON string
	if err := row.Scan(&jdr.JobID, &jdr.RunID, &summaryJSON, &jdr.ErrorCount,
		&jdr.WarnCount, &jdr.ErrorUsd, &jdr.WorkloadUsd, &jdr.RecordedAt); err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	var summary JobDiagnosticsSummary
	if err := json.Unmarshal([]byte(summaryJSON), &summary); err != nil {
		return nil, fmt.Errorf("parse summary JSON: %w", err)
	}
	jdr.Summary = &summary
	return &jdr, nil
}
