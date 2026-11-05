package jobs

import (
	"encoding/json"
	"net/http"

	"github.com/facets/cur-web/internal/auth"
)

// adminOnly returns the session if the caller is an admin; otherwise it writes
// a JSON 401/403 and returns nil.
func (h *Handler) adminOnly(w http.ResponseWriter, r *http.Request) *auth.Session {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		writeJSONError(w, http.StatusUnauthorized, "Please sign in again.")
		return nil
	}
	if !sess.IsAdmin(h.adminEmails) {
		writeJSONError(w, http.StatusForbidden, "Only admins can do this.")
		return nil
	}
	return sess
}

// writeJSONError writes {"error": msg}. Messages are shown to admins as-is:
// write plain sentences that say what to do.
func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(v)
}
