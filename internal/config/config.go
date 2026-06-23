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
	// GeminiModel is the model alias passed to `gemini --model`. From
	// $GEMINI_MODEL, default "pro". Set to "flash" for cheaper/free-tier
	// runs (Pro has no free-tier quota).
	GeminiModel string
	// AdminEmails is a comma-separated allow-list from $ADMIN_EMAILS.
	// Members can see every job (not just their own) via /api/admin/*.
	// Empty = no admins. Compared case-insensitively against session.Email.
	AdminEmails []string
}

func (c *Config) JobsDir() string { return c.DataDir + "/jobs" }
func (c *Config) DBPath() string  { return c.DataDir + "/cur-web.db" }

func LoadFromEnv() (*Config, error) {
	devBypass := os.Getenv("DEV_AUTH_BYPASS") == "true"

	required := []string{"DATA_DIR", "SESSION_SECRET", "APP_BASE_URL"}
	if !devBypass {
		required = append(required,
			"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
			"RESEND_API_KEY", "RESEND_FROM", "SLACK_WEBHOOK_URL",
		)
	}
	// GEMINI_API_KEY is intentionally NOT in the required list. It must be
	// set in the host environment (VM-wide on prod, shell on dev) so the
	// spawned gemini subprocess inherits it via os.Environ(). Treating it as
	// part of cur-web's config would force operators to duplicate it.
	for _, k := range required {
		if os.Getenv(k) == "" {
			return nil, errors.New("missing required env var: " + k)
		}
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	model := os.Getenv("GEMINI_MODEL")
	if model == "" {
		model = "pro"
	}
	return &Config{
		GeminiModel:        model,
		Port:               port,
		DataDir:            os.Getenv("DATA_DIR"),
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
