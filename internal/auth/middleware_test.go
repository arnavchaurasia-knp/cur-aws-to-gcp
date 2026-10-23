package auth_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// getRoleDemo returns a fixed role for the given email, simulating a DB lookup
// where the user's current role may differ from the role cached in their
// session cookie.
func getRoleDemo(email string) (string, error) {
	overrides := map[string]string{
		"rep@google.com": "viewer", // demoted: cookie says admin, DB says viewer
		"viewer@google.com": "admin", // promoted: cookie says viewer, DB says admin
	}
	if role, ok := overrides[email]; ok {
		return role, nil
	}
	return "", nil // no row -> keep session value
}

func TestMiddlewareRoleDemotionTakesEffect(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
	sm := auth.NewSessionManager(secret, false)

	// Session cookie says admin (stale), but DB says viewer (demoted).
	sess := &auth.Session{Email: "rep@google.com", Name: "Rep User", Role: "admin"}
	w := httptest.NewRecorder()
	require.NoError(t, sm.Set(w, sess))

	req := &http.Request{Header: http.Header{"Cookie": w.Result().Header["Set-Cookie"]}}

	// A handler that reports the session role after middleware resolution.
	handler := auth.Middleware(sm, getRoleDemo)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s := auth.SessionFromCtx(r.Context())
		w.Write([]byte(s.Role))
	}))
	handler.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "viewer", w.Body.String(), "demoted admin must be viewer on next request without re-login")
}

func TestMiddlewareRolePromotionTakesEffect(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
	sm := auth.NewSessionManager(secret, false)

	// Session cookie says viewer (stale), but DB says admin (promoted).
	sess := &auth.Session{Email: "viewer@google.com", Name: "Viewer User", Role: "viewer"}
	w := httptest.NewRecorder()
	require.NoError(t, sm.Set(w, sess))

	req := &http.Request{Header: http.Header{"Cookie": w.Result().Header["Set-Cookie"]}}

	handler := auth.Middleware(sm, getRoleDemo)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s := auth.SessionFromCtx(r.Context())
		w.Write([]byte(s.Role))
	}))
	handler.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "admin", w.Body.String(), "promoted viewer must be admin on next request without re-login")
}

func TestMiddlewareNoDBRowKeepsSessionRole(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
	sm := auth.NewSessionManager(secret, false)

	// User not in DB -> getRole returns ("", nil), session role should be kept.
	sess := &auth.Session{Email: "unknown@google.com", Name: "Unknown User", Role: "admin"}
	w := httptest.NewRecorder()
	require.NoError(t, sm.Set(w, sess))

	req := &http.Request{Header: http.Header{"Cookie": w.Result().Header["Set-Cookie"]}}

	handler := auth.Middleware(sm, getRoleDemo)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s := auth.SessionFromCtx(r.Context())
		w.Write([]byte(s.Role))
	}))
	handler.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)
	assert.Equal(t, "admin", w.Body.String(), "no DB row -> keep session role")
}
