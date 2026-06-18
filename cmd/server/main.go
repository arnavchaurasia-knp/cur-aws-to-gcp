// cmd/server/main.go
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/facets/cur-web/internal/auth"
	"github.com/facets/cur-web/internal/config"
	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/facets/cur-web/internal/notify"
	"github.com/facets/cur-web/internal/preflight"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/joho/godotenv"
)

func main() {
	godotenv.Load() // no-op if .env absent

	cfg, err := config.LoadFromEnv()
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	if err := preflight.Check(cfg.SkillDir); err != nil {
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
	allowedDomains := []string{"google.com", "facets.cloud"}
	oauthH := auth.NewOAuthHandler(
		cfg.GoogleClientID, cfg.GoogleClientSecret,
		cfg.GoogleRedirectURI, sm, cfg.AppBaseURL, cfg.DevAuthBypass,
		allowedDomains, cfg.AdminEmails,
	)
	if cfg.DevAuthBypass {
		log.Println("WARNING: DEV_AUTH_BYPASS=true — Google OAuth disabled, /api/auth/login sets a fake dev@google.com session")
	}

	spawner := jobs.NewSpawner(jobs.SpawnerConfig{})
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
		log.Printf("admins (%d): %v", len(cfg.AdminEmails), cfg.AdminEmails)
	}

	// Reap orphan jobs surviving a previous backend run.
	if orphans, err := database.ListNonTerminalJobs(); err == nil {
		for _, j := range orphans {
			log.Printf("attaching watcher to orphan job %s (status=%s, pid=%d, attempts=%d)",
				j.ID, j.Status, j.ClaudePID, j.Attempts)
			go watcher.Watch(j.ID, j.ClaudePID)
		}
	}

	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)

	// Auth (no session required)
	r.Get("/api/auth/login", oauthH.Login)
	r.Get("/api/auth/callback", oauthH.Callback)
	r.Get("/api/auth/logout", oauthH.Logout)

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
		r.Post("/api/contact", func(w http.ResponseWriter, r *http.Request) {
			sess := auth.SessionFromCtx(r.Context())
			var body struct {
				Message string `json:"message"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			msg := strings.TrimSpace(body.Message)
			if len(msg) > 2000 {
				msg = msg[:2000]
			}
			go notify.PostContactInterest(notifyCfg.SlackURL, sess.Name, sess.Email, msg)
			go notify.SendContactInterest(notifyCfg.Email, cfg.AdminEmails, sess.Name, sess.Email, msg)
			w.WriteHeader(http.StatusAccepted)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]string{"status": "received"})
		})
	})

	addr := ":" + cfg.Port
	fmt.Printf("cur-web listening on %s\n", addr)
	log.Fatal(http.ListenAndServe(addr, r))
}
