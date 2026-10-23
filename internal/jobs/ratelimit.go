package jobs

import (
	"net/http"
	"strings"
	"sync"
	"time"
)

// tokenBucket is a simple token-bucket rate limiter.
type tokenBucket struct {
	tokens   float64
	capacity float64
	rate     float64 // tokens per second
	last     time.Time
	mu       sync.Mutex
}

func newTokenBucket(rate float64, burst float64) *tokenBucket {
	return &tokenBucket{
		tokens:   float64(burst),
		capacity: float64(burst),
		rate:     rate,
		last:     time.Now(),
	}
}

func (tb *tokenBucket) allow() bool {
	tb.mu.Lock()
	defer tb.mu.Unlock()
	now := time.Now()
	elapsed := now.Sub(tb.last).Seconds()
	tb.last = now
	tb.tokens += elapsed * tb.rate
	if tb.tokens > tb.capacity {
		tb.tokens = tb.capacity
	}
	if tb.tokens < 1.0 {
		return false
	}
	tb.tokens--
	return true
}

// NewRateLimitMiddleware wraps a handler with a per-IP rate limiter.
func NewRateLimitMiddleware(limit float64, burst float64) func(http.HandlerFunc) http.HandlerFunc {
	buckets := make(map[string]*tokenBucket)
	var mu sync.Mutex

	return func(next http.HandlerFunc) http.HandlerFunc {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Use IP as the key (first part of X-Forwarded-For if present, else RemoteAddr)
			ip := r.RemoteAddr
			if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
				// Take the first IP in the chain
				for _, part := range strings.Split(forwarded, ",") {
					ip = strings.TrimSpace(part)
					break
				}
			}

			mu.Lock()
			b, ok := buckets[ip]
			if !ok {
				b = newTokenBucket(limit, burst)
				buckets[ip] = b
			}
			mu.Unlock()

			if !b.allow() {
				w.Header().Set("Retry-After", "60")
				http.Error(w, `{"error":"rate limit exceeded, try again later"}`, http.StatusTooManyRequests)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}