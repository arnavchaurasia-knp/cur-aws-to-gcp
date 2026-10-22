package db

import (
	"fmt"
	"time"
)

// EquivalenceUsageRecord represents a single equivalence claim usage by a job.
type EquivalenceUsageRecord struct {
	JobID       string  `json:"job_id"`
	Attribute   string  `json:"attribute"`
	AWSValue    string  `json:"aws_value"`
	GCPValue    string  `json:"gcp_value"`
	Scope       string  `json:"scope"`
	RowsUsed    int     `json:"rows_used"`
	USDExposed  float64 `json:"usd_exposed"`
	RecordedAt  string  `json:"recorded_at"`
}

// RecordEquivalenceUsage writes rows of equivalence usage for a completed job.
func (d *DB) RecordEquivalenceUsage(records []EquivalenceUsageRecord) error {
	if len(records) == 0 {
		return nil
	}
	tx, err := d.conn.Begin()
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	stmt, err := tx.Prepare(`INSERT INTO equivalence_usage (
		job_id, attribute, aws_value, gcp_value, scope, rows_used, usd_exposed, recorded_at
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return fmt.Errorf("prepare insert equivalence_usage: %w", err)
	}
	defer stmt.Close()

	for _, r := range records {
		recAt := r.RecordedAt
		if recAt == "" {
			recAt = time.Now().UTC().Format(time.RFC3339)
		}
		if _, err := stmt.Exec(r.JobID, r.Attribute, r.AWSValue, r.GCPValue, r.Scope, r.RowsUsed, r.USDExposed, recAt); err != nil {
			return fmt.Errorf("insert equivalence_usage: %w", err)
		}
	}

	return tx.Commit()
}

// GetEquivalenceUsage returns all recorded equivalence usage rows ordered by recorded_at desc.
func (d *DB) GetEquivalenceUsage() ([]EquivalenceUsageRecord, error) {
	rows, err := d.conn.Query(`SELECT job_id, attribute, aws_value, gcp_value, scope, rows_used, usd_exposed, recorded_at FROM equivalence_usage ORDER BY recorded_at DESC`)
	if err != nil {
		return nil, fmt.Errorf("query equivalence_usage: %w", err)
	}
	defer rows.Close()

	var records []EquivalenceUsageRecord
	for rows.Next() {
		var r EquivalenceUsageRecord
		if err := rows.Scan(&r.JobID, &r.Attribute, &r.AWSValue, &r.GCPValue, &r.Scope, &r.RowsUsed, &r.USDExposed, &r.RecordedAt); err != nil {
			return nil, fmt.Errorf("scan equivalence_usage: %w", err)
		}
		records = append(records, r)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return records, nil
}
