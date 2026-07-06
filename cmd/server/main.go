// cmd/server/main.go
package main

import (
	"context"
	"encoding/json"
	"expvar"
	"io/fs"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/config"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/facets/cur-web/internal/logger"
	"github.com/facets/cur-web/internal/notify"
	"github.com/facets/cur-web/internal/preflight"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/joho/godotenv"
)

func main() {
	godotenv.Load() // no-op if .env absent
	logger.Init()

	cfg, err := config.LoadFromEnv()
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	if err := preflight.Check(); err != nil {
		log.Fatalf("preflight failed: %v", err)
	}

	os.MkdirAll(cfg.JobsDir(), 0755)

	database, err := db.Open(cfg.DBPath())
	if err != nil {
		log.Fatalf("db error: %v", err)
	}
	defer database.Close()

	// Best-effort one-shot backfill: walks existing job dirs and ensures
	// each projection.duckdb has a run_results table with >=1 row. Lets
	// pre-this-release jobs return non-empty history from GET /runs.
	// Errors are logged, not fatal — bad job dirs can't crash startup.
	if err := jobs.BackfillRunResults(cfg.JobsDir()); err != nil {
		log.Printf("backfill run_results: %v", err)
	}

	secureCookies := strings.HasPrefix(cfg.AppBaseURL, "https://")
	sm := auth.NewSessionManager(cfg.SessionSecret, secureCookies)
	oauthH := auth.NewOAuthHandler(
		cfg.GoogleClientID, cfg.GoogleClientSecret,
		cfg.GoogleRedirectURI, sm, cfg.AppBaseURL, cfg.DevAuthBypass,
		cfg.AllowedDomains, cfg.AdminEmails,
	)
	if cfg.DevAuthBypass {
		log.Println("WARNING: DEV_AUTH_BYPASS=true — Google OAuth disabled, /api/auth/login sets a fake dev@google.com session")
	}

	spawner := jobs.NewSpawner(jobs.SpawnerConfig{AGYModel: cfg.AGYModel})
	notifyCfg := jobs.NotifyConfig{
		Email: notify.EmailConfig{
			APIKey:  cfg.ResendAPIKey,
			From:    cfg.ResendFrom,
			BaseURL: cfg.AppBaseURL,
		},
		SlackURL: cfg.SlackWebhookURL,
	}
	watcher := jobs.NewWatcher(database, spawner, cfg.JobsDir(), notifyCfg)
	jobHandler := jobs.NewHandler(database, cfg.JobsDir(), spawner, watcher, notifyCfg, cfg.AdminEmails)
	if len(cfg.AdminEmails) > 0 {
		log.Printf("admins configured: %d", len(cfg.AdminEmails))
	}

	// Reap orphan jobs surviving a previous backend run.
	if orphans, err := database.ListNonTerminalJobs(); err == nil {
		for _, j := range orphans {
			if j.AgentPID == 0 {
				// Never actually started (server crashed before spawn). Mark
				// failed so the user can retry cleanly without burning an
				// attempt slot through the Watch → immediate-finalize path.
				log.Printf("orphan job %s never started (pid=0); marking failed", j.ID)
				_ = database.UpdateJobFailed(j.ID, "server restarted before job could start — please retry")
				continue
			}
			log.Printf("attaching watcher to orphan job %s (status=%s, pid=%d, attempts=%d)",
				j.ID, j.Status, j.AgentPID, j.Attempts)
			go watcher.Watch(j.ID, j.AgentPID)
		}
	}

	r := chi.NewRouter()
	r.Use(structuredRequestLogger)
	r.Use(middleware.Recoverer)

	// Health — unauthenticated, for load balancers and container probes.
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	// Auth (no session required)
	r.Get("/api/auth/login", oauthH.Login)
	r.Get("/api/auth/callback", oauthH.Callback)
	r.Get("/api/auth/logout", oauthH.Logout)
	r.Post("/api/auth/logout", oauthH.Logout)

	// Authenticated routes
	r.Group(func(r chi.Router) {
		r.Use(auth.Middleware(sm))
		r.Get("/api/auth/me", oauthH.Me)
		r.Post("/api/jobs", jobHandler.Create)
		r.Get("/api/jobs", jobHandler.List)
		r.Get("/api/jobs/{id}", jobHandler.GetByID)
		r.Get("/api/jobs/{id}/progress", jobHandler.Progress)
		r.Post("/api/jobs/{id}/retry", jobHandler.Retry)
		r.Post("/api/jobs/{id}/refine", jobHandler.Refine)
		r.Get("/api/jobs/{id}/download", jobHandler.Download)
		r.Get("/api/jobs/{id}/runs", jobHandler.Runs)
		r.Get("/api/jobs/{id}/summary", jobHandler.Summary)
		r.Get("/api/admin/jobs", jobHandler.AdminListAll)
		// Metrics endpoint behind auth — requires a valid session to avoid
		// exposing job counts and runtime stats to unauthenticated callers.
		r.Get("/debug/vars", expvar.Handler().ServeHTTP)
		r.Post("/api/contact", func(w http.ResponseWriter, r *http.Request) {
			sess := auth.SessionFromCtx(r.Context())
			var body struct {
				Message string `json:"message"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
				return
			}
			if sess == nil {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			msg := strings.TrimSpace(body.Message)
			if runes := []rune(msg); len(runes) > 2000 {
				http.Error(w, `{"error":"message too long; maximum 2000 characters"}`, http.StatusUnprocessableEntity)
				return
			}
			go func() {
				if err := notify.PostContactInterest(notifyCfg.SlackURL, sess.Name, sess.Email, msg); err != nil {
					slog.Warn("PostContactInterest failed", "user", sess.Email, "err", err)
				}
			}()
			go func() {
				if err := notify.SendContactInterest(notifyCfg.Email, cfg.AdminEmails, sess.Name, sess.Email, msg); err != nil {
					slog.Warn("SendContactInterest failed", "user", sess.Email, "err", err)
				}
			}()
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusAccepted)
			json.NewEncoder(w).Encode(map[string]string{"status": "received"})
		})
	})

	// Serve the built frontend. WEB_ROOT defaults to ./frontend/dist relative to
	// the process working directory. Any path that isn't an API route and doesn't
	// match a real file falls back to index.html so React Router handles routing.
	webRoot := os.Getenv("WEB_ROOT")
	if webRoot == "" {
		webRoot = filepath.Join(".", "frontend", "dist")
	}
	if fi, err := os.Stat(webRoot); err == nil && fi.IsDir() {
		fsys := os.DirFS(webRoot)
		fileServer := http.FileServer(http.FS(fsys))
		r.NotFound(func(w http.ResponseWriter, r *http.Request) {
			p := strings.TrimPrefix(r.URL.Path, "/")
			if _, err := fs.Stat(fsys, p); err == nil {
				fileServer.ServeHTTP(w, r)
				return
			}
			// SPA fallback — let React Router handle unknown paths
			http.ServeFile(w, r, filepath.Join(webRoot, "index.html"))
		})
	} else {
		slog.Warn("WEB_ROOT not found — frontend not served", "path", webRoot)
	}

	addr := ":" + cfg.Port
	srv := &http.Server{Addr: addr, Handler: r}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		slog.Info("cur-web starting", "addr", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	<-ctx.Done()
	slog.Info("shutting down")
	shutCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		log.Printf("shutdown: %v", err)
	}
}

func structuredRequestLogger(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)
		slog.Info("http",
			"method", r.Method,
			"path", r.URL.Path,
			"status", ww.Status(),
			"duration_ms", time.Since(start).Milliseconds(),
			"remote_addr", r.RemoteAddr,
		)
	})
}
