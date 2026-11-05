// Package envmgr is the single source of truth for every environment variable
// the server reads, plus the machinery that lets admins override the safe,
// operational ones from System Health → Settings & Secrets.
//
// HOW TO ADD A VARIABLE (colleague guide):
//  1. Add one Var entry to registry below, in the category where an admin would
//     look for it. Write Title/Description for a non-engineer: what it does and
//     what breaks without it.
//  2. Pick a Mode:
//     - ModeManaged:     editable in the app. ONLY for values the code reads at
//     use-time via os.Getenv (or that a subprocess inherits), so a change
//     applies live. Never for sign-in/session/storage settings.
//     - ModeIntegration: owned by the Integrations page (DB-backed already).
//     - ModeDeployment:  read-only here; change it in the server's .env /
//     deployment settings and restart.
//  3. If the variable is secret, set Secret: true — its value is then never
//     sent to the browser (only "set" + last 4 characters for managed ones).
//  4. If a change must trigger code (like re-reading the log level), register
//     a hook in RegisterApplyHook.
//
// Nothing outside this registry can be set from the UI. That is deliberate:
// letting a web form set arbitrary variables (PATH, PYTHONPATH, …) would let a
// typo — or a stolen admin session — change which programs the server runs.
package envmgr

import (
	"errors"
	"fmt"
	"strings"
)

// Mode says where a variable is managed.
type Mode string

const (
	ModeManaged     Mode = "managed"
	ModeIntegration Mode = "integration"
	ModeDeployment  Mode = "deployment"
)

// Var describes one environment variable the app reads.
type Var struct {
	Key         string
	Title       string
	Category    string
	Description string
	Secret      bool
	Mode        Mode
	// Required: the product is broken without it (drives a critical issue).
	Required bool
	// Aliases are alternative names the code also accepts as a fallback.
	Aliases []string
	// Options restricts the value to a fixed set (rendered as a dropdown).
	Options []string
	// Example is a placeholder shown in the edit field.
	Example string
	// ManageURL points to the page that owns ModeIntegration variables.
	ManageURL string
	// Testable: the Settings page offers a "Test" button for this variable.
	Testable bool
	// Note is an optional extra hint shown under the description.
	Note string
}

// Category names, in display order.
const (
	CatEngine        = "AI engine"
	CatCatalog       = "Pricing catalog"
	CatNotifications = "Notifications"
	CatDiagnostics   = "Logging & diagnostics"
	CatSecurity      = "Sign-in & security"
	CatServer        = "Server & storage"
)

// Categories returns category names in the order the UI should show them.
func Categories() []string {
	return []string{CatEngine, CatCatalog, CatNotifications, CatDiagnostics, CatSecurity, CatServer}
}

var registry = []Var{
	// ── AI engine ────────────────────────────────────────────────────────
	{
		Key: "ANTIGRAVITY_API_KEY", Title: "AI engine API key", Category: CatEngine,
		Description: "Lets the agy command-line tool call the Gemini model that maps AWS line items to GCP. Without it every projection job fails at the AI mapping step.",
		Note:        "Only this exact name works. GEMINI_API_KEY and AGY_API_KEY are not read by the app.",
		Secret:      true, Mode: ModeManaged, Required: true, Testable: true,
	},
	{
		Key: "AGY_PATH", Title: "agy tool location", Category: CatEngine,
		Description: "Full path to the agy command-line tool. Leave empty if 'agy' is already on the server's PATH; set it on Windows or when agy is installed somewhere unusual.",
		Example:     `C:\Users\you\AppData\Local\agy\agy.exe`,
		Mode:        ModeManaged, Testable: true,
	},
	{
		Key: "AGY_HEALTH_PROBE_DISABLED", Title: "Disable live AI test", Category: CatEngine,
		Description: "Set to 1 to stop the 'Test AI engine' button from calling the model (for example to save quota). All other checks keep running.",
		Options:     []string{"0", "1"},
		Mode:        ModeManaged,
	},
	{
		Key: "AGY_MODEL", Title: "Fallback AI model", Category: CatEngine,
		Description: "Model used only when no default engine is chosen in Report Settings. The model actually used is set in Report Settings → Default engine.",
		Mode:        ModeDeployment,
	},

	// ── Pricing catalog ──────────────────────────────────────────────────
	{
		Key: "GOOGLE_CLOUD_API_KEY", Title: "Google Cloud pricing API key", Category: CatCatalog,
		Description: "Used to download the latest GCP prices (Cloud Billing Catalog API). Without it, 'Refresh catalog' fails and prices slowly go out of date.",
		Note:        "A plain API key from a project with the Cloud Billing API enabled is enough. GCP_API_KEY is accepted as a fallback name.",
		Aliases:     []string{"GCP_API_KEY"},
		Secret:      true, Mode: ModeManaged, Testable: true,
	},
	{
		Key: "GOOGLE_CLOUD_AVAILABILITY_PROJECT", Title: "Machine-availability project", Category: CatCatalog,
		Description: "Optional. GCP project used to check which machine types exist in each region. Leave empty to skip that check.",
		Mode:        ModeManaged,
	},
	{
		Key: "GOOGLE_CLOUD_AVAILABILITY_TOKEN", Title: "Machine-availability token", Category: CatCatalog,
		Description: "Optional OAuth access token for the machine-availability check. These tokens expire after about an hour, so most setups leave this empty.",
		Secret:      true, Mode: ModeManaged,
	},

	// ── Notifications (owned by the Integrations page) ───────────────────
	{
		Key: "RESEND_API_KEY", Title: "Email API key (Resend)", Category: CatNotifications,
		Description: "Sends 'your report is ready' emails. Configure it on the Integrations page — a value saved there takes priority over the server's environment.",
		Secret:      true, Mode: ModeIntegration, ManageURL: "/admin/integrations",
	},
	{
		Key: "RESEND_FROM", Title: "Email sender address", Category: CatNotifications,
		Description: "The From address on notification emails. Configure it on the Integrations page.",
		Mode:        ModeIntegration, ManageURL: "/admin/integrations",
	},
	{
		Key: "SLACK_WEBHOOK_URL", Title: "Slack webhook", Category: CatNotifications,
		Description: "Posts job notifications to a Slack channel. Configure it on the Integrations page.",
		Secret:      true, Mode: ModeIntegration, ManageURL: "/admin/integrations",
	},

	// ── Logging & diagnostics ────────────────────────────────────────────
	{
		Key: "LOG_LEVEL", Title: "Server log detail", Category: CatDiagnostics,
		Description: "How much the server writes to its log. Switch to 'debug' while investigating a problem, then back to 'info'. Applies immediately.",
		Options:     []string{"info", "debug", "warn", "error"},
		Mode:        ModeManaged,
	},
	{
		Key: "LOG_FORMAT", Title: "Server log format", Category: CatDiagnostics,
		Description: "'json' (default) for cloud log tools, 'text' for reading logs by eye on a laptop.",
		Mode:        ModeDeployment,
	},

	// ── Sign-in & security (read-only: changing these can lock everyone out)
	{
		Key: "SETTINGS_ENCRYPTION_KEY", Title: "Encryption key for saved secrets", Category: CatSecurity,
		Description: "Encrypts every secret saved in the app (API keys here and on Integrations). If it is not set, the server creates one in its data folder automatically.",
		Note:        "On servers whose disk is replaced at each deploy, set this explicitly or saved secrets are lost on redeploy. Generate one with: openssl rand -base64 32",
		Secret:      true, Mode: ModeDeployment,
	},
	{Key: "SESSION_SECRET", Title: "Session signing key", Category: CatSecurity, Description: "Signs login cookies. Changing it signs everyone out.", Secret: true, Mode: ModeDeployment, Required: true},
	{Key: "GOOGLE_CLIENT_ID", Title: "Google sign-in client ID", Category: CatSecurity, Description: "Identifies this app to Google for 'Sign in with Google'.", Mode: ModeDeployment},
	{Key: "GOOGLE_CLIENT_SECRET", Title: "Google sign-in client secret", Category: CatSecurity, Description: "Secret half of the Google sign-in credentials.", Secret: true, Mode: ModeDeployment},
	{Key: "GOOGLE_REDIRECT_URI", Title: "Google sign-in return URL", Category: CatSecurity, Description: "Where Google sends users back after they sign in.", Mode: ModeDeployment},
	{Key: "ADMIN_EMAILS", Title: "Admin emails", Category: CatSecurity, Description: "Comma-separated emails that always get admin access. Others can be promoted in User Management.", Mode: ModeDeployment},
	{Key: "ALLOWED_DOMAINS", Title: "Allowed sign-in domains", Category: CatSecurity, Description: "Email domains allowed to sign in (default facets.cloud).", Mode: ModeDeployment},
	{Key: "ALLOW_ANY_DOMAIN", Title: "Allow any Google account", Category: CatSecurity, Description: "Must be 'true' for ALLOWED_DOMAINS=* to take effect. Leave unset.", Mode: ModeDeployment},
	{Key: "FACETS_LOGIN_EMAILS", Title: "Facets portal sign-in list", Category: CatSecurity, Description: "Emails allowed to sign in through the /facets entry point.", Mode: ModeDeployment},
	{Key: "ADMIN_PASSWORD", Title: "Fallback admin password", Category: CatSecurity, Description: "Lets an admin sign in with a password when Google sign-in is unavailable.", Secret: true, Mode: ModeDeployment},
	{Key: "DEV_AUTH_BYPASS", Title: "Developer sign-in bypass", Category: CatSecurity, Description: "When 'true', any email + any password signs in. For local development only — never on a shared server.", Mode: ModeDeployment},

	// ── Server & storage ─────────────────────────────────────────────────
	{Key: "APP_BASE_URL", Title: "App URL", Category: CatServer, Description: "Public URL of the server. An https:// URL also turns on secure cookies.", Mode: ModeDeployment, Required: true},
	{Key: "FRONTEND_URL", Title: "Frontend URL", Category: CatServer, Description: "Base URL used for links in emails, if the UI is served from a different address than the API.", Mode: ModeDeployment},
	{Key: "PORT", Title: "Server port", Category: CatServer, Description: "Port the server listens on (default 8080).", Mode: ModeDeployment},
	{Key: "DATA_DIR", Title: "Data folder", Category: CatServer, Description: "Where jobs, the database and runtime state are stored (default ~/.cur-web).", Mode: ModeDeployment},
	{Key: "DATABASE_URL", Title: "Remote database URL", Category: CatServer, Description: "Optional Turso/libSQL database. When empty, a local SQLite file in the data folder is used.", Aliases: []string{"TURSO_DATABASE_URL"}, Secret: true, Mode: ModeDeployment},
	{Key: "S3_ENDPOINT", Title: "File storage endpoint", Category: CatServer, Description: "Optional S3-compatible storage (R2/S3) for uploaded bills and reports.", Aliases: []string{"R2_ENDPOINT"}, Mode: ModeDeployment},
	{Key: "S3_BUCKET", Title: "File storage bucket", Category: CatServer, Description: "Bucket name for the optional file storage.", Aliases: []string{"R2_BUCKET"}, Mode: ModeDeployment},
	{Key: "S3_ACCESS_KEY_ID", Title: "File storage access key", Category: CatServer, Description: "Access key for the optional file storage.", Aliases: []string{"R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"}, Secret: true, Mode: ModeDeployment},
	{Key: "S3_SECRET_ACCESS_KEY", Title: "File storage secret key", Category: CatServer, Description: "Secret key for the optional file storage.", Aliases: []string{"R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"}, Secret: true, Mode: ModeDeployment},
	{Key: "S3_REGION", Title: "File storage region", Category: CatServer, Description: "Region for the optional file storage (default 'auto').", Aliases: []string{"R2_REGION"}, Mode: ModeDeployment},
}

// IgnoredVar is a variable name people commonly set that the app never reads.
type IgnoredVar struct {
	Key        string `json:"key"`
	UseInstead string `json:"use_instead"`
}

// ignored maps names that look right but are never read to the name that is.
var ignored = []IgnoredVar{
	{Key: "AGY_API_KEY", UseInstead: "ANTIGRAVITY_API_KEY"},
	{Key: "GEMINI_API_KEY", UseInstead: "ANTIGRAVITY_API_KEY"},
	{Key: "GOOGLE_API_KEY", UseInstead: "GOOGLE_CLOUD_API_KEY"},
}

// All returns the registry in display order.
func All() []Var {
	out := make([]Var, len(registry))
	copy(out, registry)
	return out
}

// Lookup finds a variable by its primary key.
func Lookup(key string) (Var, bool) {
	for _, v := range registry {
		if v.Key == key {
			return v, true
		}
	}
	return Var{}, false
}

// IgnoredNames returns the list of known-ignored variable names.
func IgnoredNames() []IgnoredVar {
	out := make([]IgnoredVar, len(ignored))
	copy(out, ignored)
	return out
}

// MaxValueLen bounds a stored value; real keys and paths are far shorter.
const MaxValueLen = 4096

// ErrNotManaged is returned when a caller tries to set a variable that is not
// editable in the app.
var ErrNotManaged = errors.New("this variable can't be changed from the app")

// Validate checks a proposed value for a managed variable. Empty values are
// rejected — clearing an override is a separate, explicit action.
func Validate(v Var, value string) error {
	if v.Mode != ModeManaged {
		return ErrNotManaged
	}
	if strings.TrimSpace(value) == "" {
		return errors.New("value is empty — use 'Remove override' to go back to the server's value")
	}
	if len(value) > MaxValueLen {
		return fmt.Errorf("value is too long (%d characters, max %d)", len(value), MaxValueLen)
	}
	if strings.ContainsAny(value, "\r\n\x00") {
		return errors.New("value must be a single line")
	}
	if value != strings.TrimSpace(value) {
		return errors.New("value has leading or trailing spaces — remove them (a pasted key often picks these up)")
	}
	if len(v.Options) > 0 {
		ok := false
		for _, o := range v.Options {
			if strings.EqualFold(o, value) {
				ok = true
				break
			}
		}
		if !ok {
			return fmt.Errorf("value must be one of: %s", strings.Join(v.Options, ", "))
		}
	}
	if v.Secret && len(value) < 8 {
		return errors.New("that looks too short to be a real key")
	}
	return nil
}

// Preview renders a value for display. Secrets show only their last 4
// characters (and only for ModeManaged/Integration — deployment secrets show
// nothing beyond "set"). Non-secrets show the value, truncated.
func Preview(v Var, value string) string {
	if value == "" {
		return ""
	}
	if v.Secret {
		if v.Mode == ModeDeployment || len(value) < 8 {
			return "••••"
		}
		return "••••" + value[len(value)-4:]
	}
	if len(value) > 160 {
		return value[:157] + "…"
	}
	return value
}
