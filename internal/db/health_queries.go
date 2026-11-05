package db

import "database/sql"

// JobBrief is a small, display-only view of a job for System Health.
type JobBrief struct {
	ID        string `json:"id"`
	Prospect  string `json:"prospect"`
	Owner     string `json:"owner"`
	Status    string `json:"status"`
	Error     string `json:"error,omitempty"`
	UpdatedAt string `json:"updated_at"`
}

// ListRecentJobsByStatus returns the most recently updated jobs with status,
// newest first.
func (d *DB) ListRecentJobsByStatus(status string, limit int) ([]JobBrief, error) {
	rows, err := d.conn.Query(`
		SELECT id, prospect, owner, status, COALESCE(error, ''), COALESCE(updated_at, '')
		FROM jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ?`, status, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []JobBrief
	for rows.Next() {
		var j JobBrief
		if err := rows.Scan(&j.ID, &j.Prospect, &j.Owner, &j.Status, &j.Error, &j.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, rows.Err()
}

// LastJobUpdateAt returns the updated_at of the newest job with status, or "".
func (d *DB) LastJobUpdateAt(status string) (string, error) {
	var ts sql.NullString
	if err := d.conn.QueryRow(`SELECT MAX(updated_at) FROM jobs WHERE status = ?`, status).Scan(&ts); err != nil {
		return "", err
	}
	return ts.String, nil
}

// RecentOutcomes counts done vs failed among the last `window` finished jobs.
// Health uses this instead of the all-time rate so a bad week a month ago does
// not keep the page red after the cause is fixed.
func (d *DB) RecentOutcomes(window int) (done, failed int, err error) {
	rows, err := d.conn.Query(`
		SELECT status FROM jobs WHERE status IN ('done', 'failed')
		ORDER BY updated_at DESC LIMIT ?`, window)
	if err != nil {
		return 0, 0, err
	}
	defer rows.Close()
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return 0, 0, err
		}
		if s == "done" {
			done++
		} else {
			failed++
		}
	}
	return done, failed, rows.Err()
}
