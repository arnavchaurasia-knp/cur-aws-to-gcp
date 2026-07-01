package config

import (
	"errors"
	"os"
	"strings"
)

type Config struct {
	Port               string
	DataDir            string
	GoogleClientID     string
	GoogleClientSecret string
	GoogleRedirectURI  string
	SessionSecret      string
	ResendAPIKey       string
	ResendFrom         string
	AppBaseURL         string
	SlackWebhookURL    string
	DevAuthBypass      bool
	// AGYModel is the model alias passed to `agy --model`. From
	// $AGY_MODEL, default "gemini-3.5-flash".
	AGYModel string
	// AdminEmails is a comma-separated allow-list from $ADMIN_EMAILS.
	// Members can see every job (not just their own) via /api/admin/*.
	// Empty = no admins. Compared case-insensitively against session.Email.
	AdminEmails []string
}

func (c *Config) JobsDir() string { return c.DataDir + "/jobs" }
func (c *Config) DBPath() string  { return c.DataDir + "/cur-web.db" }

func LoadFromEnv() (*Config, error) {
	devBypass := os.Getenv("DEV_AUTH_BYPASS") == "true"

	required := []string{"SESSION_SECRET", "APP_BASE_URL"}
	if !devBypass {
		required = append(required,
			"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
			"RESEND_API_KEY", "RESEND_FROM", "SLACK_WEBHOOK_URL",
		)
	}
	// ANTIGRAVITY_API_KEY is intentionally NOT in the required list. It must be
	// set in the host environment (VM-wide on prod, shell on dev) so the
	// spawned agy subprocess inherits it via os.Environ(). GEMINI_API_KEY is
	// ignored by agy — use ANTIGRAVITY_API_KEY.
	for _, k := range required {
		if os.Getenv(k) == "" {
			return nil, errors.New("missing required env var: " + k)
		}
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	model := os.Getenv("AGY_MODEL")
	if model == "" {
		model = "gemini-3.5-flash"
	}
	dataDir := os.Getenv("DATA_DIR")
	if dataDir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			home = "/var/lib/cur-web"
		}
		dataDir = home + "/.cur-web"
	}
	return &Config{
		AGYModel:           model,
		Port:               port,
		DataDir:            dataDir,
		GoogleClientID:     os.Getenv("GOOGLE_CLIENT_ID"),
		GoogleClientSecret: os.Getenv("GOOGLE_CLIENT_SECRET"),
		GoogleRedirectURI:  os.Getenv("GOOGLE_REDIRECT_URI"),
		SessionSecret:      os.Getenv("SESSION_SECRET"),
		ResendAPIKey:       os.Getenv("RESEND_API_KEY"),
		ResendFrom:         os.Getenv("RESEND_FROM"),
		AppBaseURL:         os.Getenv("APP_BASE_URL"),
		SlackWebhookURL:    os.Getenv("SLACK_WEBHOOK_URL"),
		DevAuthBypass:      devBypass,
		AdminEmails:        parseAdminEmails(os.Getenv("ADMIN_EMAILS")),
	}, nil
}

func parseAdminEmails(raw string) []string {
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.ToLower(strings.TrimSpace(p))
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}
