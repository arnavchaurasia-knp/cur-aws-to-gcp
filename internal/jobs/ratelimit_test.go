package jobs_test

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
)

func TestTokenBucketRateLimit(t *testing.T) {
	// 2 requests per second, capacity 2
	mw := jobs.NewRateLimitMiddlewareWithKey(2.0, 2.0, func(r *http.Request) string {
		return r.Header.Get("X-User-ID")
	})

	handler := mw(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`ok`))
	}))

	// Initial burst: 2 requests allowed immediately
	req1 := httptest.NewRequest("POST", "/api/jobs", nil)
	req1.Header.Set("X-User-ID", "user1")
	rec1 := httptest.NewRecorder()
	handler.ServeHTTP(rec1, req1)
	assert.Equal(t, http.StatusOK, rec1.Code)

	req2 := httptest.NewRequest("POST", "/api/jobs", nil)
	req2.Header.Set("X-User-ID", "user1")
	rec2 := httptest.NewRecorder()
	handler.ServeHTTP(rec2, req2)
	assert.Equal(t, http.StatusOK, rec2.Code)

	// 3rd consecutive immediate request should be rejected (burst = 2 exhausted)
	req3 := httptest.NewRequest("POST", "/api/jobs", nil)
	req3.Header.Set("X-User-ID", "user1")
	rec3 := httptest.NewRecorder()
	handler.ServeHTTP(rec3, req3)
	assert.Equal(t, http.StatusTooManyRequests, rec3.Code)
	assert.Contains(t, rec3.Body.String(), "maximum 2 uploads per second")

	// Different user should NOT be blocked
	reqOther := httptest.NewRequest("POST", "/api/jobs", nil)
	reqOther.Header.Set("X-User-ID", "user2")
	recOther := httptest.NewRecorder()
	handler.ServeHTTP(recOther, reqOther)
	assert.Equal(t, http.StatusOK, recOther.Code)

	// Wait 550ms: at 2 tokens/sec, ~1.1 tokens should refill, allowing another request
	time.Sleep(550 * time.Millisecond)
	req4 := httptest.NewRequest("POST", "/api/jobs", nil)
	req4.Header.Set("X-User-ID", "user1")
	rec4 := httptest.NewRecorder()
	handler.ServeHTTP(rec4, req4)
	assert.Equal(t, http.StatusOK, rec4.Code)
}
