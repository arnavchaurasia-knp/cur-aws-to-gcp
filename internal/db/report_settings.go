// internal/db/report_settings.go
package db

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"fmt"
	"strings"
)

// ReportSetting represents a single report toggle row.
type ReportSetting struct {
	Key          string `json:"key"`
	Value        string `json:"value"`
	ValueType    string `json:"value_type"`
	DefaultValue string `json:"default_value"`
	MinValue     *float64 `json:"min_value,omitempty"`
	MaxValue     *float64 `json:"max_value,omitempty"`
	Title        string `json:"title"`
	Description  string `json:"description"`
	GroupName    string `json:"group_name"`
	SortOrder    int    `json:"sort_order"`
	UpdatedBy    string `json:"updated_by,omitempty"`
	UpdatedAt    string `json:"updated_at,omitempty"`
}

// ReportSettingChange represents a single row in the report_setting_changes audit table.
type ReportSettingChange struct {
	ID         int    `json:"id"`
	Key        string `json:"key"`
	OldValue   string `json:"old_value"`
	NewValue   string `json:"new_value"`
	ChangedBy  string `json:"changed_by"`
	ChangedAt  string `json:"changed_at"`
}

// GetReportSettings returns all report setting rows, ordered by group_name, sort_order, key.
func (d *DB) GetReportSettings() ([]*ReportSetting, error) {
	rows, err := d.conn.Query(`
		SELECT key, value, value_type, default_value, min_value, max_value, title, description, group_name, sort_order,
		       COALESCE(updated_by,''), COALESCE(updated_at,'')
		FROM report_settings
		ORDER BY group_name, sort_order, key
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var settings []*ReportSetting
	for rows.Next() {
		var s ReportSetting
		var minV, maxV sql.NullFloat64
		if err := rows.Scan(&s.Key, &s.Value, &s.ValueType, &s.DefaultValue, &minV, &maxV,
			&s.Title, &s.Description, &s.GroupName, &s.SortOrder, &s.UpdatedBy, &s.UpdatedAt); err != nil {
			return nil, err
		}
		if minV.Valid {
			s.MinValue = &minV.Float64
		}
		if maxV.Valid {
			s.MaxValue = &maxV.Float64
		}
		settings = append(settings, &s)
	}
	return settings, nil
}

// GetReportSettingHistory returns report setting changes, newest first.
func (d *DB) GetReportSettingHistory() ([]*ReportSettingChange, error) {
	rows, err := d.conn.Query(`
		SELECT id, key, old_value, new_value, changed_by, changed_at
		FROM report_setting_changes
		ORDER BY changed_at DESC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var changes []*ReportSettingChange
	for rows.Next() {
		var c ReportSettingChange
		if err := rows.Scan(&c.ID, &c.Key, &c.OldValue, &c.NewValue, &c.ChangedBy, &c.ChangedAt); err != nil {
			return nil, err
		}
		changes = append(changes, &c)
	}
	return changes, nil
}

// UpdateReportSetting validates and updates a single report setting value,
// records the change in report_setting_changes, and returns the new version hash.
// All in one transaction.
func (d *DB) UpdateReportSetting(key, value, changedBy string) (string, error) {
	// Load the row to validate value_type and bounds
	var valueType string
	var minV, maxV sql.NullFloat64
	err := d.conn.QueryRow(`
		SELECT value_type, min_value, max_value FROM report_settings WHERE key = ?`,
		key).Scan(&valueType, &minV, &maxV)
	if err == sql.ErrNoRows {
		return "", fmt.Errorf("report setting not found: %s", key)
	}
	if err != nil {
		return "", err
	}

	// Validate based on value_type
	if valueType == "bool" {
		switch strings.ToLower(value) {
		case "true", "false", "1", "0", "yes", "no":
			// valid
		default:
			return "", fmt.Errorf("value %q is not a valid bool (use true/false)", value)
		}
		// Normalize to true/false
		switch strings.ToLower(value) {
		case "true", "1", "yes":
			value = "true"
		default:
			value = "false"
		}
	} else if valueType == "number" {
		var fv float64
		if _, err := fmt.Sscanf(value, "%f", &fv); err != nil {
			return "", fmt.Errorf("value %q is not a valid number", value)
		}
		if minV.Valid && fv < minV.Float64 {
			return "", fmt.Errorf("value %g out of range [min=%g]", fv, minV.Float64)
		}
		if maxV.Valid && fv > maxV.Float64 {
			return "", fmt.Errorf("value %g out of range [max=%g]", fv, maxV.Float64)
		}
	} else {
		return "", fmt.Errorf("unknown value_type %q for setting %s", valueType, key)
	}

	tx, err := d.conn.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	var oldValue string
	if err := tx.QueryRow(`SELECT value FROM report_settings WHERE key = ?`, key).Scan(&oldValue); err != nil {
		return "", err
	}

	if _, err := tx.Exec(`UPDATE report_settings SET value = ?, updated_by = ?, updated_at = datetime('now') WHERE key = ?`,
		value, changedBy, key); err != nil {
		return "", err
	}
	if _, err := tx.Exec(`INSERT INTO report_setting_changes (key, old_value, new_value, changed_by)
		VALUES (?, ?, ?, ?)`, key, oldValue, value, changedBy); err != nil {
		return "", err
	}

	if err := tx.Commit(); err != nil {
		return "", err
	}

	return d.ReportSettingsVersionHash()
}

// ResetReportSettings resets all settings to their default values.
func (d *DB) ResetReportSettings(changedBy string) (string, error) {
	tx, err := d.conn.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	rows, err := tx.Query(`SELECT key, default_value FROM report_settings`)
	if err != nil {
		return "", err
	}
	type kv struct{ key, val string }
	var pairs []kv
	for rows.Next() {
		var k, v string
		if err := rows.Scan(&k, &v); err != nil {
			rows.Close()
			return "", err
		}
		pairs = append(pairs, kv{k, v})
	}
	rows.Close()

	for _, p := range pairs {
		if _, err := tx.Exec(`UPDATE report_settings SET value = ?, updated_by = ?, updated_at = datetime('now') WHERE key = ?`,
			p.val, changedBy, p.key); err != nil {
			return "", err
		}
		if _, err := tx.Exec(`INSERT INTO report_setting_changes (key, old_value, new_value, changed_by)
			VALUES (?, ?, ?, ?)`, p.key, p.val, p.val, changedBy); err != nil {
			return "", err
		}
	}

	if err := tx.Commit(); err != nil {
		return "", err
	}

	return d.ReportSettingsVersionHash()
}

// ReportSettingsVersionHash returns SHA-256 of sorted key=value pairs, first 12 hex chars.
func (d *DB) ReportSettingsVersionHash() (string, error) {
	rows, err := d.conn.Query(`SELECT key, value FROM report_settings ORDER BY key`)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var parts []string
	for rows.Next() {
		var key, value string
		if err := rows.Scan(&key, &value); err != nil {
			return "", err
		}
		parts = append(parts, key+"="+value)
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, ";")))
	return hex.EncodeToString(sum[:])[:12], nil
}
