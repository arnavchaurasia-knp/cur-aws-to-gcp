package jobs

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/envmgr"
	"github.com/go-chi/chi/v5"
)

var cloudBillingBaseURL = "https://cloudbilling.googleapis.com"

// ApplyStoredEnvOverrides applies every saved Settings & Secrets override to
// the process env. Call once in main after the DB opens. Never logs values.
func ApplyStoredEnvOverrides(d *db.DB) {
	vals, err := d.LoadRuntimeEnv()
	if err != nil {
		slog.Warn("could not load saved settings overrides", "err", err)
		return
	}
	applied := 0
	for _, it := range vals {
		v, ok := envmgr.Lookup(it.Key)
		if !ok || v.Mode != envmgr.ModeManaged {
			slog.Warn("ignoring saved override for a variable that is no longer editable", "key", it.Key)
			continue
		}
		if it.Err != nil {
			envmgr.RecordLoadError(it.Key, "The saved value can't be decrypted — the encryption key changed since it was saved. Enter it again.")
			continue
		}
		if err := envmgr.ApplyOverride(it.Key, it.Value); err == nil {
			applied++
		}
	}
	slog.Info("applied saved settings overrides", "count", applied)
}

type envValueView struct {
	Set     bool   `json:"set"`
	Preview string `json:"preview,omitempty"`  // from envmgr.Preview — NEVER the raw secret
	FromKey string `json:"from_key,omitempty"` // when an alias supplied the value
}

type envOverrideView struct {
	Set       bool   `json:"set"`
	Preview   string `json:"preview,omitempty"`
	UpdatedBy string `json:"updated_by,omitempty"`
	UpdatedAt string `json:"updated_at,omitempty"`
	LoadError string `json:"load_error,omitempty"`
}

type envEffectiveView struct {
	Set     bool   `json:"set"`
	Preview string `json:"preview,omitempty"`
	Source  string `json:"source"` // "app" | "server" | "integrations" | "none"
}

type envVarView struct {
	Key         string           `json:"key"`
	Title       string           `json:"title"`
	Category    string           `json:"category"`
	Description string           `json:"description"`
	Note        string           `json:"note,omitempty"`
	Secret      bool             `json:"secret"`
	Mode        string           `json:"mode"` // managed | integration | deployment
	Required    bool             `json:"required"`
	Options     []string         `json:"options,omitempty"`
	Example     string           `json:"example,omitempty"`
	ManageURL   string           `json:"manage_url,omitempty"`
	Testable    bool             `json:"testable"`
	Server      envValueView     `json:"server"`
	Override    envOverrideView  `json:"override"`
	Effective   envEffectiveView `json:"effective"`
}

type envListResponse struct {
	Encryption struct {
		Available bool   `json:"available"`
		Source    string `json:"source"` // env | file | generated | none
		KeyPath   string `json:"key_path,omitempty"`
		Problem   string `json:"problem,omitempty"`
	} `json:"encryption"`
	Categories     []string            `json:"categories"`
	Vars           []envVarView        `json:"vars"`
	IgnoredPresent []envmgr.IgnoredVar `json:"ignored_present"`
}

func (h *Handler) envVarView(v envmgr.Var, meta map[string]db.RuntimeEnvEntry) envVarView {
	val, from, ok := envmgr.ServerValue(v)
	fromKey := ""
	if from != v.Key {
		fromKey = from
	}
	server := envValueView{
		Set:     ok,
		Preview: envmgr.Preview(v, val),
		FromKey: fromKey,
	}

	hasOver := envmgr.HasOverride(v.Key)
	var over envOverrideView
	if hasOver {
		over.Set = true
		over.Preview = envmgr.Preview(v, os.Getenv(v.Key))
		if m, exists := meta[v.Key]; exists {
			over.UpdatedBy = m.UpdatedBy
			over.UpdatedAt = m.UpdatedAt
		}
	}
	over.LoadError = envmgr.LoadError(v.Key)

	var eff envEffectiveView
	if v.Mode == envmgr.ModeIntegration {
		resolved := ""
		switch v.Key {
		case "RESEND_API_KEY":
			resolved = h.emailCfg().APIKey
		case "RESEND_FROM":
			resolved = h.emailCfg().From
		case "SLACK_WEBHOOK_URL":
			resolved = h.slackURL()
		}
		if resolved != "" {
			eff.Set = true
			eff.Preview = envmgr.Preview(v, resolved)
			eff.Source = "integrations"
		} else {
			eff.Set = false
			eff.Source = "none"
		}
	} else {
		effVal, _, effOk := envmgr.Effective(v)
		if hasOver {
			eff.Set = true
			eff.Preview = envmgr.Preview(v, effVal)
			eff.Source = "app"
		} else if effOk {
			eff.Set = true
			eff.Preview = envmgr.Preview(v, effVal)
			eff.Source = "server"
		} else {
			eff.Set = false
			eff.Source = "none"
		}
	}

	// Special case: SETTINGS_ENCRYPTION_KEY
	if v.Key == "SETTINGS_ENCRYPTION_KEY" {
		src, _, _ := db.EncryptionKeyInfo()
		eff.Set = db.EncryptionAvailable()
		if src == "env" {
			eff.Source = "server"
		} else {
			eff.Source = "none"
		}
		eff.Preview = ""
	}

	return envVarView{
		Key:         v.Key,
		Title:       v.Title,
		Category:    v.Category,
		Description: v.Description,
		Note:        v.Note,
		Secret:      v.Secret,
		Mode:        string(v.Mode),
		Required:    v.Required,
		Options:     v.Options,
		Example:     v.Example,
		ManageURL:   v.ManageURL,
		Testable:    v.Testable,
		Server:      server,
		Override:    over,
		Effective:   eff,
	}
}

// AdminListEnv lists all registered environment variables and their effective status.
func (h *Handler) AdminListEnv(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	meta, _ := h.db.ListRuntimeEnv()
	src, path, problem := db.EncryptionKeyInfo()

	resp := envListResponse{
		Categories: envmgr.Categories(),
		Vars:       make([]envVarView, 0, len(envmgr.All())),
	}
	resp.Encryption.Available = db.EncryptionAvailable()
	resp.Encryption.Source = src
	resp.Encryption.KeyPath = path
	resp.Encryption.Problem = problem

	for _, v := range envmgr.All() {
		resp.Vars = append(resp.Vars, h.envVarView(v, meta))
	}

	resp.IgnoredPresent = envmgr.IgnoredPresent()
	if resp.IgnoredPresent == nil {
		resp.IgnoredPresent = []envmgr.IgnoredVar{}
	}

	writeJSON(w, resp)
}

// AdminSetEnv saves an in-app override for a managed setting.
func (h *Handler) AdminSetEnv(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	v, ok := envmgr.Lookup(key)
	if !ok {
		writeJSONError(w, http.StatusNotFound, "Unknown setting.")
		return
	}
	if v.Mode == envmgr.ModeIntegration {
		writeJSONError(w, http.StatusBadRequest, "Change this on the Integrations page.")
		return
	}
	if v.Mode == envmgr.ModeDeployment {
		writeJSONError(w, http.StatusBadRequest, "This one can only be changed in the server's .env file (then restart) — changing it from a web page could lock everyone out.")
		return
	}

	var req struct {
		Value string `json:"value"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 16*1024)).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "Invalid request body.")
		return
	}

	if err := envmgr.Validate(v, req.Value); err != nil {
		writeJSONError(w, http.StatusBadRequest, err.Error())
		return
	}

	if v.Secret && !db.EncryptionAvailable() {
		writeJSONError(w, http.StatusConflict, "Secrets can't be saved yet: the server has no encryption key. Check the 'Encryption key' row below.")
		return
	}

	if err := h.db.SetRuntimeEnv(key, req.Value, v.Secret, sess.Email); err != nil {
		slog.Error("failed to set runtime env", "key", key, "err", err)
		writeJSONError(w, http.StatusInternalServerError, "Couldn't save the setting.")
		return
	}

	_ = envmgr.ApplyOverride(key, req.Value)
	_ = h.db.LogAdminAction(sess.Email, "env_set", key, "")

	meta, _ := h.db.ListRuntimeEnv()
	writeJSON(w, h.envVarView(v, meta))
}

// AdminClearEnv clears an in-app override, restoring the server's baseline value.
func (h *Handler) AdminClearEnv(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	v, ok := envmgr.Lookup(key)
	if !ok {
		writeJSONError(w, http.StatusNotFound, "Unknown setting.")
		return
	}
	if v.Mode == envmgr.ModeIntegration {
		writeJSONError(w, http.StatusBadRequest, "Change this on the Integrations page.")
		return
	}
	if v.Mode == envmgr.ModeDeployment {
		writeJSONError(w, http.StatusBadRequest, "This one can only be changed in the server's .env file (then restart) — changing it from a web page could lock everyone out.")
		return
	}

	if err := h.db.DeleteRuntimeEnv(key); err != nil {
		slog.Error("failed to delete runtime env", "key", key, "err", err)
		writeJSONError(w, http.StatusInternalServerError, "Couldn't clear the setting.")
		return
	}
	envmgr.ClearOverride(key)
	_ = h.db.LogAdminAction(sess.Email, "env_clear", key, "")

	meta, _ := h.db.ListRuntimeEnv()
	writeJSON(w, h.envVarView(v, meta))
}

// AdminTestEnv tests a specific setting (ANTIGRAVITY_API_KEY, AGY_PATH, GOOGLE_CLOUD_API_KEY).
func (h *Handler) AdminTestEnv(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	key := chi.URLParam(r, "key")
	switch key {
	case "ANTIGRAVITY_API_KEY":
		p := h.spawner.ProbeEngine(r.Context(), true)
		appendEngineHistory(filepath.Dir(h.jobsDir), EngineTestRecord{EngineProbe: p, Actor: sess.Email})
		_ = h.db.LogAdminAction(sess.Email, "env_test", key, fmt.Sprintf("status=%s", p.State))
		writeJSON(w, map[string]any{
			"ok":         p.State == "ok",
			"status":     p.State,
			"detail":     p.Detail,
			"latency_ms": p.LatencyMS,
		})
		return
	case "AGY_PATH":
		p := h.spawner.InspectEngine(r.Context())
		status := "fail"
		ok := false
		detail := p.Detail
		if p.BinaryFound {
			status = "ok"
			ok = true
			detail = fmt.Sprintf("Found agy at %s (version %s).", p.BinaryPath, p.Version)
		}
		_ = h.db.LogAdminAction(sess.Email, "env_test", key, fmt.Sprintf("status=%s", status))
		writeJSON(w, map[string]any{
			"ok":     ok,
			"status": status,
			"detail": detail,
		})
		return
	case "GOOGLE_CLOUD_API_KEY":
		apiKey := os.Getenv("GOOGLE_CLOUD_API_KEY")
		if apiKey == "" {
			apiKey = os.Getenv("GCP_API_KEY")
		}
		if apiKey == "" {
			_ = h.db.LogAdminAction(sess.Email, "env_test", key, "status=fail")
			writeJSON(w, map[string]any{
				"ok":     false,
				"status": "fail",
				"detail": "No key is set.",
			})
			return
		}
		reqURL := cloudBillingBaseURL + "/v1/services?pageSize=1"
		req, err := http.NewRequestWithContext(r.Context(), "GET", reqURL, nil)
		if err != nil {
			writeJSONError(w, http.StatusInternalServerError, "Couldn't create request: "+err.Error())
			return
		}
		req.Header.Set("X-Goog-Api-Key", apiKey)
		client := &http.Client{Timeout: 10 * time.Second}
		start := time.Now()
		resp, err := client.Do(req)
		latency := time.Since(start).Milliseconds()
		if err != nil {
			_ = h.db.LogAdminAction(sess.Email, "env_test", key, "status=fail")
			writeJSON(w, map[string]any{
				"ok":         false,
				"status":     "fail",
				"detail":     "Couldn't reach Google: " + err.Error(),
				"latency_ms": latency,
			})
			return
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 8*1024))
		if resp.StatusCode == http.StatusOK {
			_ = h.db.LogAdminAction(sess.Email, "env_test", key, "status=ok")
			writeJSON(w, map[string]any{
				"ok":         true,
				"status":     "ok",
				"detail":     "Google accepted the key (Cloud Billing API reachable).",
				"latency_ms": latency,
			})
			return
		}
		var errResp struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		detail := fmt.Sprintf("Google returned HTTP %d", resp.StatusCode)
		if json.Unmarshal(body, &errResp) == nil && errResp.Error.Message != "" {
			detail = errResp.Error.Message
		}
		_ = h.db.LogAdminAction(sess.Email, "env_test", key, "status=fail")
		writeJSON(w, map[string]any{
			"ok":         false,
			"status":     "fail",
			"detail":     detail,
			"latency_ms": latency,
		})
		return
	default:
		writeJSONError(w, http.StatusBadRequest, "This setting has no test.")
		return
	}
}
