package jobs

import (
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/db"
	"github.com/go-chi/chi/v5"
)

// sessionInjector wraps a router so each request carries the given session.
func sessionInjector(sess *auth.Session) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			ctx := context.WithValue(req.Context(), auth.ExportedCtxKey{}, sess)
			next.ServeHTTP(w, req.WithContext(ctx))
		})
	}
}

// TestPromotedTicketDeleteAuthorization verifies that DELETE /api/tickets/{id},
// now reachable by any authenticated user, is owner-scoped: a non-admin may
// delete only their own ticket, while admins and the owner succeed.
func TestPromotedTicketDeleteAuthorization(t *testing.T) {
	tmpDir := t.TempDir()
	database, err := db.Open(filepath.Join(tmpDir, "test.db"))
	if err != nil {
		t.Fatalf("db.Open failed: %v", err)
	}
	defer database.Close()

	seed := func(id, owner string) {
		if err := database.CreateTicket(&db.Ticket{
			ID: id, Title: "t", Description: "d", Status: "open",
			Priority: "normal", Category: "general", CreatedBy: owner,
		}); err != nil {
			t.Fatalf("CreateTicket(%s) failed: %v", id, err)
		}
	}
	seed("tkt-owned", "user@example.com")
	seed("tkt-foreign", "other@example.com")
	seed("tkt-admin-del", "other@example.com")

	newRouter := func(sess *auth.Session) *chi.Mux {
		h := &Handler{db: database}
		r := chi.NewRouter()
		r.Use(sessionInjector(sess))
		r.Delete("/api/tickets/{id}", h.AdminDeleteTicket)
		return r
	}

	del := func(sess *auth.Session, id string) int {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest("DELETE", "/api/tickets/"+id, nil)
		newRouter(sess).ServeHTTP(rec, req)
		return rec.Code
	}

	user := &auth.Session{Email: "user@example.com", Role: "user"}
	admin := &auth.Session{Email: "admin@example.com", Role: "admin", AdminGrantedBy: "root@facets.cloud"}

	// 1. Non-admin deleting someone else's ticket → 403 (the security fix).
	if code := del(user, "tkt-foreign"); code != http.StatusForbidden {
		t.Fatalf("non-owner delete: expected 403, got %d", code)
	}
	if tk, _ := database.GetTicket("tkt-foreign"); tk == nil {
		t.Fatal("foreign ticket was deleted by a non-owner — authorization bypassed")
	}

	// 2. Non-admin deleting their own ticket → 200.
	if code := del(user, "tkt-owned"); code != http.StatusOK {
		t.Fatalf("owner delete: expected 200, got %d", code)
	}
	if tk, _ := database.GetTicket("tkt-owned"); tk != nil {
		t.Fatal("owner's ticket was not deleted")
	}

	// 3. Admin deleting any ticket → 200.
	if code := del(admin, "tkt-admin-del"); code != http.StatusOK {
		t.Fatalf("admin delete: expected 200, got %d", code)
	}
	if tk, _ := database.GetTicket("tkt-admin-del"); tk != nil {
		t.Fatal("admin failed to delete a foreign ticket")
	}
}
