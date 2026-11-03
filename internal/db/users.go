package db

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"fmt"
	"strings"

	"golang.org/x/crypto/bcrypt"
)

// isFacetsEmail reports whether email belongs to a Facets-owned domain. It is
// duplicated here (canonical: internal/auth.IsFacetsEmail) so that db does not
// import auth, which would create an import cycle via the auth token middleware
// importing db. Keep the two domain lists in sync.
func isFacetsEmail(email string) bool {
	lower := strings.ToLower(strings.TrimSpace(email))
	at := strings.LastIndex(lower, "@")
	if at < 0 || at == len(lower)-1 {
		return false
	}
	domain := lower[at+1:]
	return domain == "facets.cloud" || domain == "facets.io"
}

// hashPassword hashes password using bcrypt with standard default cost.
func hashPassword(password string) (string, error) {
	bytes, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", fmt.Errorf("bcrypt hash: %w", err)
	}
	return string(bytes), nil
}

// verifyPassword verifies a plaintext password against a stored hash.
// Supports both modern bcrypt hashes ($2a$, $2b$, $2y$) and legacy salted SHA-256 (salt:hash).
func verifyPassword(password, stored string) bool {
	if stored == "" {
		return false
	}
	// Check for bcrypt hash
	if strings.HasPrefix(stored, "$2a$") || strings.HasPrefix(stored, "$2b$") || strings.HasPrefix(stored, "$2y$") {
		return bcrypt.CompareHashAndPassword([]byte(stored), []byte(password)) == nil
	}

	// Legacy salted SHA-256 fallback (salt:hash)
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

// AuthenticateUser verifies credentials for an existing registered user.
// Returns (false, nil) if the user does not exist or password does not match.
// SEC-1: Does NOT auto-provision unknown users on password login.
// SEC-4: Automatically upgrades legacy SHA-256 hashes to bcrypt on successful verification.
func (d *DB) AuthenticateUser(email, password string) (bool, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	if email == "" || password == "" {
		return false, nil
	}

	var storedHash sql.NullString
	var role string
	err := d.conn.QueryRow(`SELECT password_hash, role FROM users WHERE email = ?`, email).Scan(&storedHash, &role)
	if err == sql.ErrNoRows {
		// SEC-1: User does not exist. Do NOT auto-provision.
		return false, nil
	}
	if err != nil {
		return false, err
	}

	if !storedHash.Valid || storedHash.String == "" {
		// User was invited or created via admin without password. Associate password.
		newHash, err := hashPassword(password)
		if err != nil {
			return false, err
		}
		_, err = d.conn.Exec(`UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE email = ?`, newHash, email)
		if err != nil {
			return false, err
		}
		return true, nil
	}

	if !verifyPassword(password, storedHash.String) {
		return false, nil
	}

	// SEC-4: If stored hash is legacy SHA-256, upgrade to bcrypt on login
	if !strings.HasPrefix(storedHash.String, "$2") {
		if upgraded, err := hashPassword(password); err == nil {
			_, _ = d.conn.Exec(`UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE email = ?`, upgraded, email)
		}
	}

	return true, nil
}

// SetUserPassword updates the password for a user using bcrypt.
func (d *DB) SetUserPassword(email, password string) error {
	email = strings.ToLower(strings.TrimSpace(email))
	hash, err := hashPassword(password)
	if err != nil {
		return err
	}
	_, err = d.conn.Exec(`UPDATE users SET password_hash = ?, updated_at = datetime('now') WHERE email = ?`, hash, email)
	return err
}

// UpsertUserSeen records that email logged in, updating updated_at on
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

// UserRoleInfo holds role and granter metadata for session resolution.
type UserRoleInfo struct {
	Role           string
	AdminGrantedBy string
}

// GetUserRole returns the role stored for email, or ("", nil) if no row
// exists yet (caller should fall back to the admin-email-list bootstrap).
func (d *DB) GetUserRole(email string) (string, error) {
	info, err := d.GetUserRoleInfo(email)
	if err != nil {
		return "", err
	}
	return info.Role, nil
}

// GetUserRoleInfo returns both the role and admin_granted_by stored for email.
func (d *DB) GetUserRoleInfo(email string) (UserRoleInfo, error) {
	var role string
	var grantedBy sql.NullString
	err := d.conn.QueryRow(`SELECT role, admin_granted_by FROM users WHERE email = ?`, email).Scan(&role, &grantedBy)
	if err == sql.ErrNoRows {
		return UserRoleInfo{}, nil
	}
	if err != nil {
		return UserRoleInfo{}, err
	}
	return UserRoleInfo{
		Role:           role,
		AdminGrantedBy: grantedBy.String,
	}, nil
}

// GetUserRoleAndGrant returns both the role and admin_granted_by stored for email.
func (d *DB) GetUserRoleAndGrant(email string) (string, string, error) {
	info, err := d.GetUserRoleInfo(email)
	if err != nil {
		return "", "", err
	}
	return info.Role, info.AdminGrantedBy, nil
}

// SetUserRole assigns role to email, creating the row if it does not exist.
func (d *DB) SetUserRole(email, role string) error {
	return d.SetUserRoleWithGrant(email, role, "")
}

// SetUserRoleWithGrant assigns role to email, creating the row if it does not exist,
// and records which admin granted the role (or clears it if viewer).
func (d *DB) SetUserRoleWithGrant(email, role, granterEmail string) error {
	email = strings.ToLower(strings.TrimSpace(email))
	role = strings.ToLower(strings.TrimSpace(role))
	if role != "admin" && role != "viewer" {
		role = "viewer"
	}
	granterEmail = strings.ToLower(strings.TrimSpace(granterEmail))

	if role == "admin" && granterEmail != "" {
		_, err := d.conn.Exec(`
			INSERT INTO users (email, role, admin_granted_by, admin_granted_at) VALUES (?, ?, ?, datetime('now'))
			ON CONFLICT(email) DO UPDATE SET role=excluded.role, admin_granted_by=excluded.admin_granted_by, admin_granted_at=excluded.admin_granted_at, updated_at=datetime('now')
		`, email, role, granterEmail)
		return err
	}

	_, err := d.conn.Exec(`
		INSERT INTO users (email, role, admin_granted_by, admin_granted_at) VALUES (?, ?, NULL, NULL)
		ON CONFLICT(email) DO UPDATE SET role=excluded.role, admin_granted_by=NULL, admin_granted_at=NULL, updated_at=datetime('now')
	`, email, role)
	return err
}

// User represents a row in the users table for admin management.
type User struct {
	Email               string  `json:"email"`
	Role                string  `json:"role"`
	AdminGrantedBy      *string `json:"admin_granted_by,omitempty"`
	AdminGrantedAt      *string `json:"admin_granted_at,omitempty"`
	CreatedAt           string  `json:"created_at"`
	UpdatedAt           string  `json:"updated_at"`
	EmailNotifications  bool    `json:"email_notifications"`
	SlackNotifications  bool    `json:"slack_notifications"`
	SlackWebhookURL     string  `json:"slack_webhook_url"`
	SlackChannel        string  `json:"slack_channel"`
	HasAPIToken         bool    `json:"has_api_token"`
	DefaultExportFormat string  `json:"default_export_format"`
	DefaultEngine       string  `json:"default_engine"`
}

// ListUsers returns all users in the system ordered by email.
func generateToken() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return "fct_live_" + hex.EncodeToString(b)
}

func (d *DB) ListUsers() ([]*User, error) {
	rows, err := d.conn.Query(`
		SELECT email, role, admin_granted_by, admin_granted_at, created_at, updated_at,
		       email_notifications, slack_notifications, slack_webhook_url, slack_channel,
		       (api_token IS NOT NULL AND api_token != ''),
		       default_export_format, default_engine
		FROM users ORDER BY email ASC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var users []*User
	for rows.Next() {
		var u User
		var grantedBy, grantedAt sql.NullString
		var emailNotif, slackNotif int
		var webhookURL, channel, exportFmt, engine sql.NullString

		if err := rows.Scan(
			&u.Email, &u.Role, &grantedBy, &grantedAt, &u.CreatedAt, &u.UpdatedAt,
			&emailNotif, &slackNotif, &webhookURL, &channel,
			&u.HasAPIToken, &exportFmt, &engine,
		); err != nil {
			return nil, err
		}

		if grantedBy.Valid {
			u.AdminGrantedBy = &grantedBy.String
		}
		if grantedAt.Valid {
			u.AdminGrantedAt = &grantedAt.String
		}
		u.EmailNotifications = emailNotif != 0
		u.SlackNotifications = slackNotif != 0
		u.SlackWebhookURL = webhookURL.String
		u.SlackChannel = channel.String
		u.DefaultExportFormat = exportFmt.String
		if u.DefaultExportFormat == "" {
			u.DefaultExportFormat = "html"
		}
		u.DefaultEngine = engine.String

		users = append(users, &u)
	}
	return users, rows.Err()
}

func (d *DB) GetOrCreateAPIToken(email string) (string, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	var token sql.NullString
	err := d.conn.QueryRow(`SELECT api_token FROM users WHERE email = ?`, email).Scan(&token)
	if err == nil && token.Valid && token.String != "" {
		return token.String, nil
	}

	tok := generateToken()
	_, err = d.conn.Exec(`
		INSERT INTO users (email, api_token, updated_at) VALUES (?, ?, datetime('now'))
		ON CONFLICT(email) DO UPDATE SET api_token = excluded.api_token, updated_at = datetime('now')
	`, email, tok)
	if err != nil {
		return "", err
	}
	return tok, nil
}

func (d *DB) RegenerateAPIToken(email string) (string, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	tok := generateToken()
	_, err := d.conn.Exec(`
		INSERT INTO users (email, api_token, updated_at) VALUES (?, ?, datetime('now'))
		ON CONFLICT(email) DO UPDATE SET api_token = excluded.api_token, updated_at = datetime('now')
	`, email, tok)
	if err != nil {
		return "", err
	}
	return tok, nil
}

type UserPreferences struct {
	DefaultEngine       string `json:"default_engine"`
	DefaultExportFormat string `json:"default_export_format"`
	EmailNotifications  bool   `json:"email_notifications"`
}

func (d *DB) GetUserPreferences(email string) (*UserPreferences, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	var engine, exportFmt sql.NullString
	var emailNotif int
	err := d.conn.QueryRow(`
		SELECT default_engine, default_export_format, email_notifications
		FROM users WHERE email = ?
	`, email).Scan(&engine, &exportFmt, &emailNotif)
	if err == sql.ErrNoRows {
		return &UserPreferences{DefaultExportFormat: "html", EmailNotifications: true}, nil
	}
	if err != nil {
		return nil, err
	}
	pref := &UserPreferences{
		DefaultEngine:       engine.String,
		DefaultExportFormat: exportFmt.String,
		EmailNotifications:  emailNotif != 0,
	}
	if pref.DefaultExportFormat == "" {
		pref.DefaultExportFormat = "html"
	}
	return pref, nil
}

func (d *DB) UpdateUserPreferences(email string, defaultEngine, defaultExportFormat string, emailNotif bool) error {
	email = strings.ToLower(strings.TrimSpace(email))
	notifVal := 0
	if emailNotif {
		notifVal = 1
	}
	_, err := d.conn.Exec(`
		INSERT INTO users (email, default_engine, default_export_format, email_notifications, updated_at)
		VALUES (?, ?, ?, ?, datetime('now'))
		ON CONFLICT(email) DO UPDATE SET
			default_engine = excluded.default_engine,
			default_export_format = excluded.default_export_format,
			email_notifications = excluded.email_notifications,
			updated_at = datetime('now')
	`, email, defaultEngine, defaultExportFormat, notifVal)
	return err
}

func (d *DB) DeleteUser(email string) error {
	email = strings.ToLower(strings.TrimSpace(email))
	_, err := d.conn.Exec(`DELETE FROM users WHERE email = ?`, email)
	return err
}
