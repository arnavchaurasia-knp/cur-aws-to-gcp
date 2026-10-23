package auth

import (
	"context"
	"net/http"
)

// ExportedCtxKey is exported so handler tests in other packages can inject sessions.
type ExportedCtxKey struct{}

// getRoleFunc looks up a user's current RBAC role from the durable users table.
// It is used by Middleware to resolve role per-request so that role changes
// (admin demotion/promotion) take effect on the next request without requiring
// re-login. Returns ("", nil) when the user has no row (keep session value).
type getRoleFunc func(email string) (string, error)

func Middleware(sm *SessionManager, getRole getRoleFunc, devBypass ...bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			sess, err := sm.Get(r)
			if err != nil {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			// Resolve role from the users table on every request so that role
			// changes (admin demotion/promotion) take effect immediately without
			// requiring the user to log out and back in. If the user has no DB
			// row (ErrNoRows), keep the session's cached role.
			if getRole != nil {
				if role, err := getRole(sess.Email); err == nil && role != "" {
					sess.Role = role
				}
			}
			ctx := context.WithValue(r.Context(), ExportedCtxKey{}, sess)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

func SessionFromCtx(ctx context.Context) *Session {
	s, _ := ctx.Value(ExportedCtxKey{}).(*Session)
	return s
}

// RequireAdmin wraps a handler chain (after Middleware) and rejects any
// request whose session isn't an admin (per Session.IsAdmin) with 403.
// Centralizes the inline `sess.IsAdmin(...)` check for new /api/admin/*
// routes instead of repeating it in every handler.
func RequireAdmin(adminEmails []string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			sess := SessionFromCtx(r.Context())
			if !sess.IsAdmin(adminEmails) {
				http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
