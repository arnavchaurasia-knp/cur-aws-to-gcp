// Package db — runtime_env layer: admin overrides for operational environment
// variables (see internal/envmgr). Secret values are AES-256-GCM encrypted with
// the same key and helpers as integration_settings; they are never returned to
// a client and never logged.
package db

import (
	"database/sql"
	"errors"
)

// RuntimeEnvEntry is the masked, client-safe view of one stored override.
type RuntimeEnvEntry struct {
	Key       string
	IsSecret  bool
	UpdatedBy string
	UpdatedAt string
}

// SetRuntimeEnv upserts an override. Secrets are encrypted; if no encryption
// key is available it returns ErrNoEncryptionKey and stores nothing.
func (d *DB) SetRuntimeEnv(key, value string, isSecret bool, updatedBy string) error {
	if isSecret {
		enc, err := encryptSecret(value)
		if err != nil {
			return err
		}
		_, err = d.conn.Exec(`
			INSERT INTO runtime_env (key, value, secret_enc, is_secret, updated_by, updated_at)
			VALUES (?, NULL, ?, 1, ?, datetime('now'))
			ON CONFLICT(key) DO UPDATE SET value=NULL, secret_enc=excluded.secret_enc, is_secret=1, updated_by=excluded.updated_by, updated_at=datetime('now')
		`, key, enc, updatedBy)
		return err
	}
	_, err := d.conn.Exec(`
		INSERT INTO runtime_env (key, value, secret_enc, is_secret, updated_by, updated_at)
		VALUES (?, ?, NULL, 0, ?, datetime('now'))
		ON CONFLICT(key) DO UPDATE SET value=excluded.value, secret_enc=NULL, is_secret=0, updated_by=excluded.updated_by, updated_at=datetime('now')
	`, key, value, updatedBy)
	return err
}

// DeleteRuntimeEnv removes an override (the server value applies again).
func (d *DB) DeleteRuntimeEnv(key string) error {
	_, err := d.conn.Exec(`DELETE FROM runtime_env WHERE key = ?`, key)
	return err
}

// RuntimeEnvValue is a decrypted override, for server-side use only.
type RuntimeEnvValue struct {
	Key   string
	Value string
	// Err is set when a secret can't be decrypted (usually: the encryption key
	// changed since it was saved). Value is empty in that case.
	Err error
}

// LoadRuntimeEnv returns every stored override, decrypted. Server-side only —
// never send the result to a client.
func (d *DB) LoadRuntimeEnv() ([]RuntimeEnvValue, error) {
	rows, err := d.conn.Query(`SELECT key, value, secret_enc, is_secret FROM runtime_env ORDER BY key`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []RuntimeEnvValue
	for rows.Next() {
		var key string
		var value sql.NullString
		var enc []byte
		var isSecret int
		if err := rows.Scan(&key, &value, &enc, &isSecret); err != nil {
			return nil, err
		}
		item := RuntimeEnvValue{Key: key}
		if isSecret == 1 {
			if len(enc) == 0 {
				item.Err = errors.New("stored secret is empty")
			} else if pt, derr := decryptSecret(enc); derr != nil {
				item.Err = derr
			} else {
				item.Value = pt
			}
		} else {
			item.Value = value.String
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

// ListRuntimeEnv returns metadata (who/when) for every stored override.
func (d *DB) ListRuntimeEnv() (map[string]RuntimeEnvEntry, error) {
	rows, err := d.conn.Query(`SELECT key, is_secret, updated_by, updated_at FROM runtime_env`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]RuntimeEnvEntry{}
	for rows.Next() {
		var e RuntimeEnvEntry
		var isSecret int
		if err := rows.Scan(&e.Key, &isSecret, &e.UpdatedBy, &e.UpdatedAt); err != nil {
			return nil, err
		}
		e.IsSecret = isSecret == 1
		out[e.Key] = e
	}
	return out, rows.Err()
}
