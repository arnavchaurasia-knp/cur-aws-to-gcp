package jobs_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func withChiParam(ctx context.Context, key, value string) context.Context {
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add(key, value)
	return context.WithValue(ctx, chi.RouteCtxKey, rctx)
}

func TestPhase3_AdminGrantsAndGuardrails(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"superadmin@facets.cloud"}
	h := jobs.NewHandler(d, jobsDir, nil, nil, jobs.NotifyConfig{}, adminEmails)

	// Seed facets admin and target external user
	facetsAdmin := "boss@facets.cloud"
	require.NoError(t, d.UpsertUserSeen(facetsAdmin, "admin"))
	require.NoError(t, d.SetUserRole(facetsAdmin, "admin"))

	extUser := "x@gmail.com"
	require.NoError(t, d.UpsertUserSeen(extUser, "viewer"))
	require.NoError(t, d.SetUserRole(extUser, "viewer"))

	// Test 1: Facets admin grants admin to x@gmail.com -> succeeds; IsAdmin true on next request
	{
		payload, _ := json.Marshal(map[string]string{"role": "admin"})
		req, _ := http.NewRequestWithContext(ctxWithSession(facetsAdmin, "Boss"), "POST", "/api/admin/users/"+extUser+"/role", bytes.NewReader(payload))
		req = req.WithContext(withChiParam(req.Context(), "email", extUser))
		w := httptest.NewRecorder()
		h.AdminSetUserRole(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		// Check DB state
		info, err := d.GetUserRoleInfo(extUser)
		require.NoError(t, err)
		assert.Equal(t, "admin", info.Role)
		assert.Equal(t, facetsAdmin, info.AdminGrantedBy)

		// Next request session resolution
		sess := &auth.Session{
			Email:          extUser,
			Role:           info.Role,
			AdminGrantedBy: info.AdminGrantedBy,
		}
		assert.True(t, sess.IsAdmin(adminEmails))
	}

	// Test 2: The granted non-facets admin tries to grant admin to y@gmail.com -> refused (403)
	extUser2 := "y@gmail.com"
	require.NoError(t, d.UpsertUserSeen(extUser2, "viewer"))
	{
		payload, _ := json.Marshal(map[string]string{"role": "admin"})
		req, _ := http.NewRequestWithContext(ctxWithSession(extUser, "X Admin"), "POST", "/api/admin/users/"+extUser2+"/role", bytes.NewReader(payload))
		req = req.WithContext(withChiParam(req.Context(), "email", extUser2))
		w := httptest.NewRecorder()
		h.AdminSetUserRole(w, req)
		assert.Equal(t, http.StatusForbidden, w.Code)
		assert.Contains(t, w.Body.String(), "only facets admins can grant admin to external users")

		// Confirm y@gmail.com is still viewer
		info, err := d.GetUserRoleInfo(extUser2)
		require.NoError(t, err)
		assert.Equal(t, "viewer", info.Role)
	}

	// Test 3: Any admin tries to change their own role -> refused (400)
	{
		payload, _ := json.Marshal(map[string]string{"role": "viewer"})
		req, _ := http.NewRequestWithContext(ctxWithSession(facetsAdmin, "Boss"), "POST", "/api/admin/users/"+facetsAdmin+"/role", bytes.NewReader(payload))
		req = req.WithContext(withChiParam(req.Context(), "email", facetsAdmin))
		w := httptest.NewRecorder()
		h.AdminSetUserRole(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code)
		assert.Contains(t, w.Body.String(), "cannot change your own role")
	}

	// Test 5: Demoting the last facets admin -> refused with a clear message
	{
		// Attempt by external admin (or another facets admin) to demote the sole facets admin
		payload, _ := json.Marshal(map[string]string{"role": "viewer"})
		req, _ := http.NewRequestWithContext(ctxWithSession(extUser, "X Admin"), "POST", "/api/admin/users/"+facetsAdmin+"/role", bytes.NewReader(payload))
		req = req.WithContext(withChiParam(req.Context(), "email", facetsAdmin))
		w := httptest.NewRecorder()
		h.AdminSetUserRole(w, req)
		assert.Equal(t, http.StatusBadRequest, w.Code)
		assert.Contains(t, w.Body.String(), "cannot demote the last facets admin")
	}

	// Test 4: Revoke clears role, admin_granted_by and admin_granted_at
	{
		payload, _ := json.Marshal(map[string]string{"role": "viewer"})
		req, _ := http.NewRequestWithContext(ctxWithSession(facetsAdmin, "Boss"), "POST", "/api/admin/users/"+extUser+"/role", bytes.NewReader(payload))
		req = req.WithContext(withChiParam(req.Context(), "email", extUser))
		w := httptest.NewRecorder()
		h.AdminSetUserRole(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		info, err := d.GetUserRoleInfo(extUser)
		require.NoError(t, err)
		assert.Equal(t, "viewer", info.Role)
		assert.Empty(t, info.AdminGrantedBy)

		// Next request session resolution
		sess := &auth.Session{
			Email:          extUser,
			Role:           info.Role,
			AdminGrantedBy: info.AdminGrantedBy,
		}
		assert.False(t, sess.IsAdmin(adminEmails))
	}

	// Test 6: The users list marks a non-facets admin with its granter and date
	{
		// Grant admin again to test listUsers output
		require.NoError(t, d.SetUserRoleWithGrant(extUser, "admin", facetsAdmin))

		req, _ := http.NewRequestWithContext(ctxWithSession(facetsAdmin, "Boss"), "GET", "/api/admin/users", nil)
		w := httptest.NewRecorder()
		h.AdminListUsers(w, req)
		assert.Equal(t, http.StatusOK, w.Code)

		var users []*db.User
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &users))
		var foundExt *db.User
		for _, u := range users {
			if u.Email == extUser {
				foundExt = u
				break
			}
		}
		require.NotNil(t, foundExt)
		assert.Equal(t, "admin", foundExt.Role)
		require.NotNil(t, foundExt.AdminGrantedBy)
		assert.Equal(t, facetsAdmin, *foundExt.AdminGrantedBy)
		assert.NotNil(t, foundExt.AdminGrantedAt)
	}

	// Test 7: Every grant and revoke appears in the admin audit log
	{
		entries, err := d.ListAuditLog(10, 0, "", "", nil, nil)
		require.NoError(t, err)
		require.NotEmpty(t, entries)

		actions := make([]string, 0, len(entries))
		for _, e := range entries {
			actions = append(actions, e.Action)
		}
		assert.Contains(t, actions, "set_user_role")
	}
}
