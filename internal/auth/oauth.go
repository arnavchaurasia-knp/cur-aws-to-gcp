package auth

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
)

// userStore is the minimal subset of *db.DB oauth.go needs. Declared as an
// interface here (rather than importing internal/db directly) to avoid a
// dependency from internal/auth on internal/db.
type userStore interface {
	UpsertUserSeen(email string) error
	GetUserRole(email string) (string, error)
}

type OAuthHandler struct {
	cfg               *oauth2.Config
	sm                *SessionManager
	redirectAfter     string
	devBypass         bool
	allowedDomains    map[string]bool
	adminEmails       []string
	facetsLoginEmails map[string]bool
	users             userStore
}

func NewOAuthHandler(clientID, clientSecret, redirectURI string, sm *SessionManager, appBaseURL string, devBypass bool, allowedDomains, adminEmails []string) *OAuthHandler {
	allowed := make(map[string]bool, len(allowedDomains))
	for _, d := range allowedDomains {
		allowed[d] = true
	}
	return &OAuthHandler{
		cfg: &oauth2.Config{
			ClientID:     clientID,
			ClientSecret: clientSecret,
			RedirectURL:  redirectURI,
			Scopes:       []string{"openid", "email", "profile"},
			Endpoint:     google.Endpoint,
		},
		sm:             sm,
		redirectAfter:  appBaseURL,
		devBypass:      devBypass,
		allowedDomains: allowed,
		adminEmails:    adminEmails,
	}
}

// SetFacetsLoginEmails installs the FACETS_LOGIN_EMAILS allow-list (lowercased
// entries, same convention as adminEmails/allowedDomains). Called once at
// startup from main.go.
func (h *OAuthHandler) SetFacetsLoginEmails(emails []string) {
	m := make(map[string]bool, len(emails))
	for _, e := range emails {
		m[strings.ToLower(e)] = true
	}
	h.facetsLoginEmails = m
}

// SetUserStore installs the users-table-backed RBAC lookup. Called once at
// startup from main.go. If never called, Callback falls back to the
// admin-email-list bootstrap only (Role stays empty on the session).
func (h *OAuthHandler) SetUserStore(s userStore) {
	h.users = s
}

// enforceFacetsLoginAllowList rejects a login attempted via next=/facets from
// any email not on the FACETS_LOGIN_EMAILS allow-list. Ordinary logins
// (next != "/facets") are unaffected. Returns false (having already written
// the 403 response) when the login must be rejected.
func (h *OAuthHandler) enforceFacetsLoginAllowList(w http.ResponseWriter, email, next string) bool {
	if next != "/facets" {
		return true
	}
	if !h.facetsLoginEmails[strings.ToLower(email)] {
		http.Error(w, `{"error":"forbidden: this account is not permitted to sign in here"}`, http.StatusForbidden)
		return false
	}
	return true
}

// sanitizeNext restricts the post-login redirect to a small allow-list of
// in-app paths. Never echoes the raw query value back into a Location
// header — an open redirect would let a crafted /api/auth/login?next=...
// link send an authenticated user somewhere attacker-controlled.
func sanitizeNext(next string) string {
	if next == "/facets" {
		return next
	}
	return "/"
}

func (h *OAuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	next := sanitizeNext(r.URL.Query().Get("next"))
	if next == "" {
		next = "/"
	}

	email := strings.TrimSpace(r.URL.Query().Get("email"))
	if email == "" {
		email = strings.TrimSpace(r.URL.Query().Get("as"))
	}
	userName := strings.TrimSpace(r.URL.Query().Get("name"))
	if email == "" && r.Method == http.MethodPost {
		_ = r.ParseForm()
		email = strings.TrimSpace(r.FormValue("email"))
		if userName == "" {
			userName = strings.TrimSpace(r.FormValue("name"))
		}
		if email == "" {
			var body struct {
				Email string `json:"email"`
				Name  string `json:"name"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err == nil {
				email = strings.TrimSpace(body.Email)
				if userName == "" {
					userName = strings.TrimSpace(body.Name)
				}
			}
		}
	}

	if email != "" {
		if !h.enforceFacetsLoginAllowList(w, email, next) {
			return
		}

		name := userName
		if name == "" {
			parts := strings.Split(email, "@")
			localPart := parts[0]
			nameParts := strings.FieldsFunc(localPart, func(c rune) bool {
				return c == '.' || c == '_' || c == '-'
			})
			for i, p := range nameParts {
				if len(p) > 0 {
					nameParts[i] = strings.ToUpper(p[:1]) + strings.ToLower(p[1:])
				}
			}
			if len(nameParts) > 0 {
				name = strings.Join(nameParts, " ")
			} else {
				name = localPart
			}
		}

		lowerEmail := strings.ToLower(email)
		isFacets := strings.HasSuffix(lowerEmail, "@facets.cloud") || strings.HasSuffix(lowerEmail, "@facets.io") || strings.Contains(lowerEmail, "@facets.")
		role := "viewer"
		if isFacets {
			role = "admin"
		}
		if h.users != nil {
			if r, err := h.users.GetUserRole(email); err == nil && r != "" {
				role = r
			}
			_ = h.users.UpsertUserSeen(email)
		}
		if (&Session{Email: email, Role: role}).IsAdmin(h.adminEmails) {
			role = "admin"
		}

		sess := &Session{Email: email, Name: name, Role: role}
		if err := h.sm.Set(w, sess); err != nil {
			http.Error(w, `{"error":"failed to create session"}`, http.StatusInternalServerError)
			return
		}

		if r.Header.Get("Accept") == "application/json" || strings.Contains(r.Header.Get("Content-Type"), "application/json") {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]any{
				"status":   "ok",
				"email":    email,
				"name":     name,
				"role":     role,
				"is_admin": sess.IsAdmin(h.adminEmails),
			})
			return
		}
		http.Redirect(w, r, next, http.StatusTemporaryRedirect)
		return
	}

	// No email supplied; proceed with Google OAuth if configured
	if h.cfg.ClientID != "" && !h.devBypass {
		state := randomState()
		http.SetCookie(w, &http.Cookie{Name: "oauth_state", Value: state, HttpOnly: true, Secure: h.sm.Secure(), MaxAge: 600})
		http.SetCookie(w, &http.Cookie{Name: "oauth_next", Value: next, HttpOnly: true, Secure: h.sm.Secure(), MaxAge: 600})
		var url string
		if len(h.allowedDomains) == 1 {
			for d := range h.allowedDomains {
				url = h.cfg.AuthCodeURL(state, oauth2.SetAuthURLParam("hd", d))
			}
		} else {
			url = h.cfg.AuthCodeURL(state)
		}
		http.Redirect(w, r, url, http.StatusTemporaryRedirect)
		return
	}

	// If Google OAuth is not configured and no email was provided:
	http.Redirect(w, r, "/login?error=google_oauth_not_configured", http.StatusTemporaryRedirect)
}

func (h *OAuthHandler) Callback(w http.ResponseWriter, r *http.Request) {
	stateCookie, err := r.Cookie("oauth_state")
	if err != nil || stateCookie.Value != r.URL.Query().Get("state") {
		http.Error(w, "invalid state", http.StatusBadRequest)
		return
	}
	token, err := h.cfg.Exchange(context.Background(), r.URL.Query().Get("code"))
	if err != nil {
		http.Error(w, "token exchange failed", http.StatusInternalServerError)
		return
	}
	client := h.cfg.Client(context.Background(), token)
	resp, err := client.Get("https://www.googleapis.com/oauth2/v3/userinfo")
	if err != nil {
		http.Error(w, "userinfo failed", http.StatusInternalServerError)
		return
	}
	defer resp.Body.Close()
	var info struct {
		Email   string `json:"email"`
		Name    string `json:"name"`
		HD      string `json:"hd"`
		Picture string `json:"picture"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		http.Error(w, "decode failed", http.StatusInternalServerError)
		return
	}
	if !h.allowedDomains[info.HD] {
		http.Error(w, "forbidden: account domain not allowed", http.StatusForbidden)
		return
	}

	next := "/"
	if c, err := r.Cookie("oauth_next"); err == nil {
		next = sanitizeNext(c.Value)
	}
	// Facets lockdown: next=/facets requires the email be on the
	// FACETS_LOGIN_EMAILS allow-list. Checked before h.sm.Set — an unlisted
	// email must never get a session out of this path at all.
	if !h.enforceFacetsLoginAllowList(w, info.Email, next) {
		return
	}

	role := ""
	if h.users != nil {
		if r, err := h.users.GetUserRole(info.Email); err == nil {
			role = r
		}
		_ = h.users.UpsertUserSeen(info.Email)
	}
	if role == "" {
		// No users-table row yet (or lookup failed) — fall back to the
		// admin-email-list bootstrap so existing ADMIN_EMAILS deployments
		// keep working unchanged.
		if (&Session{Email: info.Email}).IsAdmin(h.adminEmails) {
			role = "admin"
		} else {
			role = "viewer"
		}
	}

	h.sm.Set(w, &Session{Email: info.Email, Name: info.Name, Picture: info.Picture, Role: role})

	http.SetCookie(w, &http.Cookie{Name: "oauth_next", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: h.sm.Secure()})
	http.Redirect(w, r, next, http.StatusTemporaryRedirect)
}

func (h *OAuthHandler) Logout(w http.ResponseWriter, r *http.Request) {
	h.sm.Clear(w)
	w.WriteHeader(http.StatusOK)
}

func (h *OAuthHandler) Me(w http.ResponseWriter, r *http.Request) {
	sess := SessionFromCtx(r.Context())
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"email":    sess.Email,
		"name":     sess.Name,
		"picture":  sess.Picture,
		"is_admin": sess.IsAdmin(h.adminEmails),
	})
}

func randomState() string {
	b := make([]byte, 16)
	rand.Read(b)
	return base64.URLEncoding.EncodeToString(b)
}

var ErrForbiddenDomain = errors.New("not a google.com account")
