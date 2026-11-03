package auth_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type mockUserStore struct {
	users map[string]string // email -> role
}

func (m *mockUserStore) UpsertUserSeen(email, role string) error {
	if _, exists := m.users[email]; !exists {
		m.users[email] = role
	}
	return nil
}

func (m *mockUserStore) GetUserRole(email string) (string, error) {
	return m.users[email], nil
}

func (m *mockUserStore) AuthenticateUser(email, password string) (bool, error) {
	// SEC-1: Does not auto-provision unknown users
	if _, exists := m.users[email]; !exists {
		return false, nil
	}
	return password == "validpass", nil
}

// TestSEC1_UnknownEmailCannotLoginOrAutoProvisionAdmin verifies that POST /api/auth/login
// with an unknown email (even on a facets.cloud domain) returns 401 and does NOT provision an account.
func TestSEC1_UnknownEmailCannotLoginOrAutoProvisionAdmin(t *testing.T) {
	sm := auth.NewSessionManager("test-secret-key-that-is-at-least-32-bytes-long", false)
	store := &mockUserStore{users: make(map[string]string)}

	h := auth.NewOAuthHandler("client_id", "client_secret", "http://localhost:8080/callback", sm, "/", false, []string{"facets.cloud"}, nil)
	h.SetUserStore(store)
	h.SetAdminPassword("adminpass")

	// Attempt login with an unknown facets.cloud email
	body := `{"email":"unclaimed_attacker@facets.cloud","password":"password123"}`
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.Login(w, req)

	// Must be rejected with 401
	assert.Equal(t, http.StatusUnauthorized, w.Code, "Unknown email must return 401 Unauthorized")
	assert.Empty(t, store.users, "No user must be provisioned in the store")

	var resp map[string]string
	err := json.Unmarshal(w.Body.Bytes(), &resp)
	require.NoError(t, err)
	assert.Contains(t, resp["error"], "Invalid email or password")
}

// TestSEC3_OAuthStateMissingOrMismatchedFailsClosed verifies that the callback fails closed
// when state is missing, empty, or mismatched when code is present.
func TestSEC3_OAuthStateMissingOrMismatchedFailsClosed(t *testing.T) {
	sm := auth.NewSessionManager("test-secret-key-that-is-at-least-32-bytes-long", false)
	h := auth.NewOAuthHandler("client_id", "client_secret", "http://localhost:8080/callback", sm, "/", false, nil, nil)

	// Case 1: Code present, but state is omitted from query and cookie
	{
		req := httptest.NewRequest(http.MethodGet, "/api/auth/callback?code=some_oauth_code", nil)
		req.Header.Set("Accept", "application/json")
		w := httptest.NewRecorder()
		h.Callback(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code, "Missing state must return 400 Bad Request")
		assert.Contains(t, w.Body.String(), "invalid_state")
	}

	// Case 2: Code present, state in query but missing in cookie
	{
		req := httptest.NewRequest(http.MethodGet, "/api/auth/callback?code=some_oauth_code&state=attack_state", nil)
		req.Header.Set("Accept", "application/json")
		w := httptest.NewRecorder()
		h.Callback(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code, "Missing cookie state must return 400 Bad Request")
		assert.Contains(t, w.Body.String(), "invalid_state")
	}

	// Case 3: Code present, cookie present, but state values mismatch
	{
		req := httptest.NewRequest(http.MethodGet, "/api/auth/callback?code=some_oauth_code&state=query_state_val", nil)
		req.AddCookie(&http.Cookie{Name: "oauth_state", Value: "cookie_state_val"})
		req.Header.Set("Accept", "application/json")
		w := httptest.NewRecorder()
		h.Callback(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code, "Mismatched state must return 400 Bad Request")
		assert.Contains(t, w.Body.String(), "invalid_state")
	}
}
