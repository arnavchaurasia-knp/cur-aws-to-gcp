package db

import (
	"time"
)

// LineItemOverride represents a manual operator correction to a specific line item in a job.
type LineItemOverride struct {
	JobID        string    `json:"job_id"`
	RowKey       string    `json:"row_key"`
	Suppressed   bool      `json:"suppressed"`
	GCPTarget    string    `json:"gcp_target"`
	OverrideRate float64   `json:"override_rate"`
	PinnedKey    string    `json:"pinned_key"`
	PinnedVal    string    `json:"pinned_val"`
	Note         string    `json:"note"`
	UpdatedBy    string    `json:"updated_by"`
	UpdatedAt    time.Time `json:"updated_at"`
}

// ListJobOverrides returns all manual line-item overrides for a specific job.
func (d *DB) ListJobOverrides(jobID string) ([]LineItemOverride, error) {
	rows, err := d.conn.Query(`
		SELECT job_id, row_key, suppressed, COALESCE(gcp_target, ''), COALESCE(override_rate, -1),
		       COALESCE(pinned_key, ''), COALESCE(pinned_val, ''), COALESCE(note, ''),
		       COALESCE(updated_by, ''), COALESCE(updated_at, datetime('now'))
		FROM job_line_item_overrides
		WHERE job_id = ?
		ORDER BY updated_at DESC
	`, jobID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []LineItemOverride
	for rows.Next() {
		var o LineItemOverride
		var suppInt int
		if err := rows.Scan(&o.JobID, &o.RowKey, &suppInt, &o.GCPTarget, &o.OverrideRate, &o.PinnedKey, &o.PinnedVal, &o.Note, &o.UpdatedBy, &o.UpdatedAt); err != nil {
			return nil, err
		}
		o.Suppressed = (suppInt == 1)
		out = append(out, o)
	}
	return out, rows.Err()
}

// UpsertJobOverride adds or updates an override record.
func (d *DB) UpsertJobOverride(o LineItemOverride) error {
	suppInt := 0
	if o.Suppressed {
		suppInt = 1
	}
	_, err := d.conn.Exec(`
		INSERT INTO job_line_item_overrides (job_id, row_key, suppressed, gcp_target, override_rate, pinned_key, pinned_val, note, updated_by, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
		ON CONFLICT(job_id, row_key) DO UPDATE SET
			suppressed = excluded.suppressed,
			gcp_target = excluded.gcp_target,
			override_rate = excluded.override_rate,
			pinned_key = excluded.pinned_key,
			pinned_val = excluded.pinned_val,
			note = excluded.note,
			updated_by = excluded.updated_by,
			updated_at = excluded.updated_at
	`, o.JobID, o.RowKey, suppInt, o.GCPTarget, o.OverrideRate, o.PinnedKey, o.PinnedVal, o.Note, o.UpdatedBy)
	return err
}

// DeleteJobOverride deletes an override record.
func (d *DB) DeleteJobOverride(jobID, rowKey string) error {
	_, err := d.conn.Exec(`DELETE FROM job_line_item_overrides WHERE job_id = ? AND row_key = ?`, jobID, rowKey)
	return err
}

