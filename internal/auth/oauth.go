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
	AuthenticateUser(email, password string) (bool, error)
}

type OAuthHandler struct {
	cfg               *oauth2.Config
	sm                *SessionManager
	redirectAfter     string
	devBypass         bool
	adminPassword     string
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

// SetAdminPassword installs the fallback admin password.
func (h *OAuthHandler) SetAdminPassword(pwd string) {
	h.adminPassword = pwd
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
	var password, userName string

	if r.Method == http.MethodPost {
		_ = r.ParseForm()
		if email == "" {
			email = strings.TrimSpace(r.FormValue("email"))
		}
		password = strings.TrimSpace(r.FormValue("password"))
		userName = strings.TrimSpace(r.FormValue("name"))
		if email == "" {
			var body struct {
				Email    string `json:"email"`
				Password string `json:"password"`
				Name     string `json:"name"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err == nil {
				email = strings.TrimSpace(body.Email)
				password = strings.TrimSpace(body.Password)
				if userName == "" {
					userName = strings.TrimSpace(body.Name)
				}
			}
		}
	}

	// 1. If an email is supplied (via form or POST), authenticate with password
	if email != "" {
		if !h.enforceFacetsLoginAllowList(w, email, next) {
			return
		}

		authenticated := false
		if h.users != nil && password != "" {
			if ok, err := h.users.AuthenticateUser(email, password); err == nil && ok {
				authenticated = true
			}
		}
		if !authenticated && h.adminPassword != "" && password == h.adminPassword {
			authenticated = true
		}
		if !authenticated && h.devBypass && password != "" {
			authenticated = true
		}

		if !authenticated {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{
				"error": "Invalid email or password. Please use 'Sign in with Google' or enter valid credentials.",
			})
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

	// 2. No email supplied (GET /api/auth/login) — initiate Google OAuth flow
	if h.cfg.ClientID != "" {
		state := randomState()
		http.SetCookie(w, &http.Cookie{Name: "oauth_state", Value: state, HttpOnly: true, Secure: h.sm.Secure(), Path: "/", MaxAge: 600})
		http.SetCookie(w, &http.Cookie{Name: "oauth_next", Value: next, HttpOnly: true, Secure: h.sm.Secure(), Path: "/", MaxAge: 600})

		redirectURI := h.cfg.RedirectURL
		if strings.HasSuffix(r.Host, "onrender.com") && !strings.Contains(redirectURI, "onrender.com") {
			redirectURI = "https://" + r.Host + "/api/auth/callback"
		}
		cfgCopy := *h.cfg
		cfgCopy.RedirectURL = redirectURI

		var url string
		if len(h.allowedDomains) == 1 && !h.allowedDomains["*"] {
			for d := range h.allowedDomains {
				url = cfgCopy.AuthCodeURL(state, oauth2.SetAuthURLParam("hd", d))
			}
		} else {
			url = cfgCopy.AuthCodeURL(state)
		}
		http.Redirect(w, r, url, http.StatusTemporaryRedirect)
		return
	}

	http.Redirect(w, r, "/login?error=google_oauth_not_configured", http.StatusTemporaryRedirect)
}

func (h *OAuthHandler) Callback(w http.ResponseWriter, r *http.Request) {
	stateCookie, err := r.Cookie("oauth_state")
	if err != nil || stateCookie.Value != r.URL.Query().Get("state") {
		http.Redirect(w, r, "/login?error=invalid_state", http.StatusTemporaryRedirect)
		return
	}

	redirectURI := h.cfg.RedirectURL
	if strings.HasSuffix(r.Host, "onrender.com") && !strings.Contains(redirectURI, "onrender.com") {
		redirectURI = "https://" + r.Host + "/api/auth/callback"
	}
	cfgCopy := *h.cfg
	cfgCopy.RedirectURL = redirectURI

	token, err := cfgCopy.Exchange(context.Background(), r.URL.Query().Get("code"))
	if err != nil {
		http.Redirect(w, r, "/login?error=token_exchange_failed", http.StatusTemporaryRedirect)
		return
	}
	client := cfgCopy.Client(context.Background(), token)
	resp, err := client.Get("https://www.googleapis.com/oauth2/v3/userinfo")
	if err != nil {
		http.Redirect(w, r, "/login?error=userinfo_failed", http.StatusTemporaryRedirect)
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
		http.Redirect(w, r, "/login?error=decode_failed", http.StatusTemporaryRedirect)
		return
	}

	userDomain := strings.ToLower(info.HD)
	if userDomain == "" {
		parts := strings.Split(info.Email, "@")
		if len(parts) == 2 {
			userDomain = strings.ToLower(parts[1])
		}
	}

	if len(h.allowedDomains) > 0 && !h.allowedDomains["*"] {
		if !h.allowedDomains[userDomain] && !h.allowedDomains[strings.ToLower(info.HD)] {
			http.Redirect(w, r, "/login?error=forbidden", http.StatusTemporaryRedirect)
			return
		}
	}

	next := "/"
	if c, err := r.Cookie("oauth_next"); err == nil {
		next = sanitizeNext(c.Value)
	}
	if !h.enforceFacetsLoginAllowList(w, info.Email, next) {
		return
	}

	lowerEmail := strings.ToLower(info.Email)
	isFacets := strings.HasSuffix(lowerEmail, "@facets.cloud") || strings.HasSuffix(lowerEmail, "@facets.io") || strings.Contains(lowerEmail, "@facets.")
	role := "viewer"
	if isFacets {
		role = "admin"
	}
	if h.users != nil {
		if r, err := h.users.GetUserRole(info.Email); err == nil && r != "" {
			role = r
		}
		_ = h.users.UpsertUserSeen(info.Email)
	}
	if (&Session{Email: info.Email, Role: role}).IsAdmin(h.adminEmails) {
		role = "admin"
	}

	if err := h.sm.Set(w, &Session{Email: info.Email, Name: info.Name, Picture: info.Picture, Role: role}); err != nil {
		http.Redirect(w, r, "/login?error=session_failed", http.StatusTemporaryRedirect)
		return
	}

	http.SetCookie(w, &http.Cookie{Name: "oauth_next", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: h.sm.Secure()})
	http.SetCookie(w, &http.Cookie{Name: "oauth_state", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: h.sm.Secure()})
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
