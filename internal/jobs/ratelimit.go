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

// NewRateLimitMiddleware wraps a handler with a rate limiter keyed by IP (or session user if authenticated).
// The token bucket algorithm smoothly limits requests to `limit` per second with a maximum burst capacity of `burst`.
// Unlike fixed-window counters, token buckets prevent burst attacks at window boundaries.
func NewRateLimitMiddleware(limit float64, burst float64) func(http.HandlerFunc) http.HandlerFunc {
	return NewRateLimitMiddlewareWithKey(limit, burst, nil)
}

// NewRateLimitMiddlewareWithKey wraps a handler with a rate limiter using a custom key extractor.
func NewRateLimitMiddlewareWithKey(limit float64, burst float64, keyFn func(r *http.Request) string) func(http.HandlerFunc) http.HandlerFunc {
	buckets := make(map[string]*tokenBucket)
	var mu sync.Mutex

	// Periodic cleanup of stale buckets every 10 minutes to prevent memory leaks
	go func() {
		ticker := time.NewTicker(10 * time.Minute)
		for range ticker.C {
			mu.Lock()
			now := time.Now()
			for k, b := range buckets {
				b.mu.Lock()
				idle := now.Sub(b.last)
				b.mu.Unlock()
				if idle > 30*time.Minute {
					delete(buckets, k)
				}
			}
			mu.Unlock()
		}
	}()

	return func(next http.HandlerFunc) http.HandlerFunc {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var key string
			if keyFn != nil {
				key = keyFn(r)
			}
			if key == "" {
				// Default: Use IP as key (first part of X-Forwarded-For if present, else RemoteAddr)
				key = r.RemoteAddr
				if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
					for _, part := range strings.Split(forwarded, ",") {
						key = strings.TrimSpace(part)
						break
					}
				}
			}

			mu.Lock()
			b, ok := buckets[key]
			if !ok {
				b = newTokenBucket(limit, burst)
				buckets[key] = b
			}
			mu.Unlock()

			if !b.allow() {
				w.Header().Set("Retry-After", "1")
				http.Error(w, `{"error":"rate limit exceeded: maximum 2 uploads per second"}`, http.StatusTooManyRequests)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}