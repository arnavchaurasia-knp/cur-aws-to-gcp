// internal/db/users.go
package db

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"strings"
)

func hashPassword(password string) string {
	salt := make([]byte, 16)
	_, _ = rand.Read(salt)
	h := sha256.Sum256(append(salt, []byte(password)...))
	return hex.EncodeToString(salt) + ":" + hex.EncodeToString(h[:])
}

func verifyPassword(password, stored string) bool {
	parts := strings.Split(stored, ":")
	if len(parts) != 2 {
		return false
	}
	salt, err := hex.DecodeString(parts[0])
	if err != nil {
		return false
	}
	expectedHash, err := hex.DecodeString(parts[1])
	if err != nil {
		return false
	}
	actualHash := sha256.Sum256(append(salt, []byte(password)...))
	return subtle.ConstantTimeCompare(actualHash[:], expectedHash) == 1
}

// AuthenticateUser verifies or creates credentials for email.
// If the user does not exist, it registers them with the given password.
// If the user exists without password, it associates this password.
// If the user exists with password, it checks password matching.
func (d *DB) AuthenticateUser(email, password string) (bool, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	if email == "" || password == "" {
		return false, nil
	}

	var storedHash sql.NullString
	var role string
	err := d.conn.QueryRow(`SELECT password_hash, role FROM users WHERE email = ?`, email).Scan(&storedHash, &role)
	if err == sql.ErrNoRows {
		isFacets := strings.HasSuffix(email, "@facets.cloud") || strings.HasSuffix(email, "@facets.io") || strings.Contains(email, "@facets.")
		newRole := "viewer"
		if isFacets {
			newRole = "admin"
		}
		newHash := hashPassword(password)
		_, err := d.conn.Exec(`INSERT INTO users (email, role, password_hash) VALUES (?, ?, ?)`, email, newRole, newHash)
		if err != nil {
			return false, err
		}
		return true, nil
	}
	if err != nil {
		return false, err
	}

	if !storedHash.Valid || storedHash.String == "" {
		newHash := hashPassword(password)
		_, err := d.conn.Exec(`UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE email = ?`, newHash, email)
		if err != nil {
			return false, err
		}
		return true, nil
	}

	if !verifyPassword(password, storedHash.String) {
		return false, nil
	}

	_, _ = d.conn.Exec(`UPDATE users SET updated_at = datetime('now') WHERE email = ?`, email)
	return true, nil
}

// UpsertUserSeen records that email successfully logged in, creating a
// row with role on first sight and bumping updated_at on every
// subsequent login. Never touches an existing row's role — promotion/
// demotion only happens via SetUserRole.
func (d *DB) UpsertUserSeen(email, role string) error {
	_, err := d.conn.Exec(
		`INSERT INTO users (email, role) VALUES (?, ?)
		 ON CONFLICT(email) DO UPDATE SET updated_at=datetime('now')`,
		email, role,
	)
	return err
}

// GetUserRole returns the role stored for email, or ("", nil) if no row
// exists yet (caller should fall back to the admin-email-list bootstrap).
func (d *DB) GetUserRole(email string) (string, error) {
	var role string
	err := d.conn.QueryRow(`SELECT role FROM users WHERE email = ?`, email).Scan(&role)
	if err == sql.ErrNoRows {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return role, nil
}

// SetUserRole creates or updates the role for email.
func (d *DB) SetUserRole(email, role string) error {
	_, err := d.conn.Exec(
		`INSERT INTO users (email, role) VALUES (?, ?)
		 ON CONFLICT(email) DO UPDATE SET role=excluded.role, updated_at=datetime('now')`,
		email, role,
	)
	return err
}

type User struct {
	Email     string `json:"email"`
	Role      string `json:"role"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

// ListUsers returns every known user, newest-seen first.
func (d *DB) ListUsers() ([]*User, error) {
	rows, err := d.conn.Query(`SELECT email, role, created_at, updated_at FROM users ORDER BY updated_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var users []*User
	for rows.Next() {
		var u User
		if err := rows.Scan(&u.Email, &u.Role, &u.CreatedAt, &u.UpdatedAt); err != nil {
			return nil, err
		}
		users = append(users, &u)
	}
	return users, nil
}
