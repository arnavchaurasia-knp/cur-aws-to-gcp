// internal/db/tickets.go
package db

import (
	"database/sql"
	"fmt"
	"strings"
	"time"
)

// Ticket represents a support ticket raised by a user.
type Ticket struct {
	ID          string    `json:"id"`
	Title       string    `json:"title"`
	Description string    `json:"description"`
	Status      string    `json:"status"`
	Priority    string    `json:"priority"`
	Category    string    `json:"category"`
	CreatedBy   string    `json:"created_by"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	ResolvedAt  *time.Time `json:"resolved_at,omitempty"`
	ResolvedBy  string    `json:"resolved_by,omitempty"`
}

// CreateTicket inserts a new ticket.
func (d *DB) CreateTicket(t *Ticket) error {
	if t.ID == "" {
		return fmt.Errorf("ticket id is required")
	}
	if t.CreatedBy == "" {
		return fmt.Errorf("created_by is required")
	}
	_, err := d.conn.Exec(`
		INSERT INTO tickets (id, title, description, status, priority, category, created_by, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
	`, t.ID, t.Title, t.Description, t.Status, t.Priority, t.Category, t.CreatedBy)
	return err
}

// ListTickets returns tickets. If createdBy is non-empty, only that user's tickets are returned.
func (d *DB) ListTickets(createdBy string) ([]Ticket, error) {
	query := `
		SELECT id, title, description, status, priority, category, created_by,
		       created_at, updated_at, resolved_at, resolved_by
		FROM tickets
	`
	var args []interface{}
	if createdBy != "" {
		query += ` WHERE created_by = ?`
		args = append(args, createdBy)
	}
	query += ` ORDER BY created_at DESC`

	rows, err := d.conn.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tickets []Ticket
	for rows.Next() {
		var t Ticket
		var resolvedAt sql.NullString
		var resolvedBy sql.NullString
		err := rows.Scan(&t.ID, &t.Title, &t.Description, &t.Status, &t.Priority,
			&t.Category, &t.CreatedBy, &t.CreatedAt, &t.UpdatedAt, &resolvedAt, &resolvedBy)
		if err != nil {
			return nil, err
		}
		if resolvedAt.Valid {
			parsed, perr := time.Parse("2006-01-02 15:04:05", resolvedAt.String)
			if perr == nil {
				t.ResolvedAt = &parsed
			}
		}
		if resolvedBy.Valid {
			t.ResolvedBy = resolvedBy.String
		}
		tickets = append(tickets, t)
	}
	return tickets, rows.Err()
}

// GetTicket returns a single ticket by ID.
func (d *DB) GetTicket(id string) (*Ticket, error) {
	row := d.conn.QueryRow(`
		SELECT id, title, description, status, priority, category, created_by,
		       created_at, updated_at, resolved_at, resolved_by
		FROM tickets
		WHERE id = ?
	`, id)

	var t Ticket
	var resolvedAt sql.NullString
	var resolvedBy sql.NullString
	err := row.Scan(&t.ID, &t.Title, &t.Description, &t.Status, &t.Priority,
		&t.Category, &t.CreatedBy, &t.CreatedAt, &t.UpdatedAt, &resolvedAt, &resolvedBy)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	if resolvedAt.Valid {
		parsed, perr := time.Parse("2006-01-02 15:04:05", resolvedAt.String)
		if perr == nil {
			t.ResolvedAt = &parsed
		}
	}
	if resolvedBy.Valid {
		t.ResolvedBy = resolvedBy.String
	}
	return &t, nil
}

// UpdateTicketStatus updates a ticket's status and optional resolution fields.
func (d *DB) UpdateTicketStatus(id, status, resolvedBy string) error {
	_, err := d.conn.Exec(`
		UPDATE tickets
		SET status = ?, updated_at = datetime('now'),
		    resolved_at = CASE WHEN ? = 'resolved' THEN datetime('now') ELSE resolved_at END,
		    resolved_by = CASE WHEN ? IS NOT NULL AND ? != '' THEN ? ELSE resolved_by END
		WHERE id = ?
	`, status, status, resolvedBy, resolvedBy, resolvedBy, id)
	return err
}

// UpdateTicket updates a ticket's editable fields.
func (d *DB) UpdateTicket(id, title, description, priority, category string) error {
	_, err := d.conn.Exec(`
		UPDATE tickets
		SET title = ?, description = ?, priority = ?, category = ?, updated_at = datetime('now')
		WHERE id = ?
	`, title, description, priority, category, id)
	return err
}

// SanitizeTicketID creates a URL-safe ticket ID from a prefix and timestamp.
func SanitizeTicketID(prefix string) string {
	ts := time.Now().UTC().Format("20060102-150405")
	return strings.ToLower(fmt.Sprintf("%s-%s", prefix, ts))
}

// UserProfile represents extended user details.
type UserProfile struct {
	Email       string    `json:"email"`
	Name        string    `json:"name,omitempty"`
	Phone       string    `json:"phone,omitempty"`
	Company     string    `json:"company,omitempty"`
	Department  string    `json:"department,omitempty"`
	TimeZone    string    `json:"time_zone,omitempty"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// GetUserProfile returns a user's profile, or nil if none exists.
func (d *DB) GetUserProfile(email string) (*UserProfile, error) {
	row := d.conn.QueryRow(`
		SELECT email, name, phone, company, department, time_zone, updated_at
		FROM user_profiles
		WHERE email = ?
	`, email)

	var p UserProfile
	var name, phone, company, department, timeZone sql.NullString
	err := row.Scan(&p.Email, &name, &phone, &company, &department, &timeZone, &p.UpdatedAt)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	if name.Valid { p.Name = name.String }
	if phone.Valid { p.Phone = phone.String }
	if company.Valid { p.Company = company.String }
	if department.Valid { p.Department = department.String }
	if timeZone.Valid { p.TimeZone = timeZone.String }
	return &p, nil
}

// SaveUserProfile creates or updates a user's profile.
func (d *DB) SaveUserProfile(p *UserProfile) error {
	if p.Email == "" {
		return fmt.Errorf("email is required")
	}
	_, err := d.conn.Exec(`
		INSERT INTO user_profiles (email, name, phone, company, department, time_zone, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
		ON CONFLICT(email) DO UPDATE SET
			name = excluded.name,
			phone = excluded.phone,
			company = excluded.company,
			department = excluded.department,
			time_zone = excluded.time_zone,
			updated_at = datetime('now')
	`, p.Email, p.Name, p.Phone, p.Company, p.Department, p.TimeZone)
	return err
}

// NotificationSettings represents a user's email/slack notification preferences.
type NotificationSettings struct {
	EmailNotifications bool   `json:"email_notifications"`
	SlackNotifications bool   `json:"slack_notifications"`
	SlackWebhookURL    string `json:"slack_webhook_url"`
	SlackChannel       string `json:"slack_channel"`
}

// GetNotificationSettings returns a user's notification preferences.
func (d *DB) GetNotificationSettings(email string) (*NotificationSettings, error) {
	var emailNotif, slackNotif int
	var webhookURL, channel sql.NullString
	err := d.conn.QueryRow(`
		SELECT email_notifications, slack_notifications,
		       COALESCE(slack_webhook_url, ''), COALESCE(slack_channel, '')
		FROM users
		WHERE email = ?
	`, email).Scan(&emailNotif, &slackNotif, &webhookURL, &channel)
	if err == sql.ErrNoRows {
		// Default settings for user not yet in users table
		return &NotificationSettings{
			EmailNotifications: true,
			SlackNotifications: false,
			SlackWebhookURL:    "",
			SlackChannel:       "",
		}, nil
	}
	if err != nil {
		return nil, err
	}
	return &NotificationSettings{
		EmailNotifications: emailNotif == 1,
		SlackNotifications: slackNotif == 1,
		SlackWebhookURL:    webhookURL.String,
		SlackChannel:       channel.String,
	}, nil
}

// UpdateNotificationSettings updates a user's notification preferences.
func (d *DB) UpdateNotificationSettings(email string, emailNotif, slackNotif bool, slackWebhookURL, slackChannel string) error {
	// Upsert into users table so users logging in via bypass or first-time session exist
	_, err := d.conn.Exec(`
		INSERT INTO users (email, email_notifications, slack_notifications, slack_webhook_url, slack_channel, updated_at)
		VALUES (?, ?, ?, ?, ?, datetime('now'))
		ON CONFLICT(email) DO UPDATE SET
			email_notifications = excluded.email_notifications,
			slack_notifications = excluded.slack_notifications,
			slack_webhook_url   = excluded.slack_webhook_url,
			slack_channel       = excluded.slack_channel,
			updated_at          = excluded.updated_at
	`, email, boolToInt(emailNotif), boolToInt(slackNotif), slackWebhookURL, slackChannel)
	return err
}

// ListUploadedBills returns all uploaded bills, newest first.
func (d *DB) ListUploadedBills() ([]UploadedBill, error) {
	rows, err := d.conn.Query(`
		SELECT content_hash, input_ext, file_size, COALESCE(file_name, ''), created_at
		FROM uploaded_bills
		ORDER BY created_at DESC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var bills []UploadedBill
	for rows.Next() {
		var b UploadedBill
		err := rows.Scan(&b.ContentHash, &b.InputExt, &b.FileSize, &b.FileName, &b.CreatedAt)
		if err != nil {
			return nil, err
		}
		bills = append(bills, b)
	}
	return bills, rows.Err()
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
