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
	"github.com/facets/cur-web/internal/skill"
	"github.com/facets/cur-web/internal/storage"
	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/joho/godotenv"
)

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load(".env")
	_ = godotenv.Load("../.env")
	_ = godotenv.Load("../../.env")
	logger.Init()

	cfg, err := config.LoadFromEnv()
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	if cfg.GoogleClientID != "" {
		slog.Info("Google OAuth configured", "client_id_prefix", cfg.GoogleClientID[:min(12, len(cfg.GoogleClientID))]+"...")
	} else {
		slog.Warn("Google OAuth not configured (GOOGLE_CLIENT_ID empty in .env)")
	}

	if !cfg.DevAuthBypass {
		if err := preflight.Check(); err != nil {
			slog.Warn("preflight check completed with warnings", "err", err)
		}
	}

	os.MkdirAll(cfg.JobsDir(), 0755)

	database, err := db.Open(cfg.DBPath(), cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("db error: %v", err)
	}
	defer database.Close()

	// Log the effective allowed-domains list so it is visible in deploy logs.
	if len(cfg.AllowedDomains) == 0 {
		slog.Warn("ALLOWED_DOMAINS unset — default allow-list is " + strings.Join(config.DefaultAllowedDomains, ","))
	} else {
		slog.Info("allowed domains", "domains", strings.Join(cfg.AllowedDomains, ","))
	}

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
	oauthH.SetFacetsLoginEmails(cfg.FacetsLoginEmails)
	oauthH.SetUserStore(database)
	oauthH.SetAdminPassword(cfg.AdminPassword)
	if cfg.DevAuthBypass {
		log.Println("WARNING: DEV_AUTH_BYPASS=true — Google OAuth disabled, /api/auth/login sets a fake dev@facets.cloud session")
	}

	spawner := jobs.NewSpawner(jobs.SpawnerConfig{AGYPath: cfg.AGYPath, AGYModel: cfg.AGYModel})
	spawner.SetDB(database)
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

	storageClient, err := storage.New(context.Background(), storage.Config{
		Endpoint:        cfg.S3Endpoint,
		Bucket:          cfg.S3Bucket,
		AccessKeyID:     cfg.S3AccessKey,
		SecretAccessKey: cfg.S3SecretKey,
		Region:          cfg.S3Region,
	})
	if err != nil {
		slog.Warn("failed to initialize remote storage, falling back to local only", "err", err)
		storageClient = &storage.NoopClient{}
	}
	watcher.SetStorage(storageClient)
	jobHandler.SetStorage(storageClient)

	if len(cfg.AdminEmails) > 0 {
		log.Printf("admins configured: %d", len(cfg.AdminEmails))
	}

	// Reap orphan jobs surviving a previous backend run.
	if jobs.IsStopAllActive(cfg.DataDir) {
		log.Println("STOP_ALL is active — startup orphan reaper attaching NO watchers and resuming nothing")
	} else if orphans, err := database.ListNonTerminalJobs(); err == nil {
		for _, j := range orphans {
			if j.AgentPID == 0 {
				// Never actually started (server crashed before spawn). Mark
				// failed so the user can retry cleanly without burning an
				// attempt slot through the Watch → immediate-finalize path.
				log.Printf("orphan job %s never started (pid=0); marking failed", j.ID)
				_ = database.UpdateJobFailed(j.ID, "server restarted before job could start — please retry")
				continue
			}
			// Fast-path: process is already dead and a report exists on disk.
			// This happens when the job completed normally but the server
			// restarted before the watcher could call UpdateJobDone (e.g. the
			// watcher goroutine panicked or the process was killed mid-finalize).
			// Attach directly rather than spinning up a goroutine that would
			// just block on watchUntilDead for one dead-PID poll cycle.
			jobDir := filepath.Join(cfg.JobsDir(), j.ID)
			reportGlob, _ := filepath.Glob(filepath.Join(jobDir, "projection-audit", "report*.html"))
			pidDead := !jobs.PidAlive(j.AgentPID)
			if pidDead && len(reportGlob) > 0 {
				log.Printf("orphan job %s: pid %d dead, report exists — marking done", j.ID, j.AgentPID)
				spend, err := jobs.QueryAWSSpend(filepath.Join(jobDir, "projection-audit", "projection.duckdb"))
				if err != nil {
					log.Printf("orphan job %s: QueryAWSSpend: %v", j.ID, err)
				}
				_ = database.UpdateJobDone(j.ID, spend)
				continue
			}
			log.Printf("attaching watcher to orphan job %s (status=%s, pid=%d, attempts=%d)",
				j.ID, j.Status, j.AgentPID, j.Attempts)
			go watcher.Watch(j.ID, j.AgentPID)
		}
	}

	catalogFetchedAt, catalogAgeDays := loadCatalogVintage(skill.ResolveDir())

	r := chi.NewRouter()
	r.Use(structuredRequestLogger)
	r.Use(middleware.Recoverer)
	r.Use(securityHeaders)

	// Health — unauthenticated, for load balancers and container probes.
	r.Get("/health", healthHandler(catalogFetchedAt, catalogAgeDays))

	// Version — public, no secrets. Reports the build version (git SHA injected
	// at build time via -ldflags) and the GCP catalog vintage.
	r.Get("/version", versionHandler(catalogFetchedAt, catalogAgeDays))

	// Rate limiting for sensitive endpoints
	loginLimiter := jobs.NewRateLimitMiddleware(10.0/60.0, 10)  // 10/min/IP
	uploadLimiter := jobs.NewRateLimitMiddleware(5.0/3600.0, 5) // 5/hour/session
	shareLimiter := jobs.NewRateLimitMiddleware(60.0/60.0, 60)  // 60/min/IP

	// Auth (no session required)
	r.Get("/api/auth/login", loginLimiter(http.HandlerFunc(oauthH.Login)))
	r.Post("/api/auth/login", loginLimiter(http.HandlerFunc(oauthH.Login)))
	r.Get("/api/auth/callback", oauthH.Callback)
	r.Get("/api/auth/logout", oauthH.Logout)
	r.Post("/api/auth/logout", oauthH.Logout)
	// When Google OAuth redirects to /login with ?code=..., handle it immediately via Callback
	r.Get("/login", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("code") != "" {
			oauthH.Callback(w, r)
			return
		}
		webRoot := os.Getenv("WEB_ROOT")
		if webRoot == "" {
			webRoot = filepath.Join(".", "frontend", "dist")
		}
		http.ServeFile(w, r, filepath.Join(webRoot, "index.html"))
	})

	// Public (no-auth) read-only shared report lookup — rate limited.
	r.Get("/api/reports/{token}", shareLimiter(jobHandler.GetSharedReport))

	// Authenticated routes
	r.Group(func(r chi.Router) {
		// Resolve role per-request from the users table so role changes
		// (admin demotion/promotion) take effect on the next request without
		// requiring re-login (WORKORDER §1.1).
		r.Use(auth.Middleware(sm, database.GetUserRole, cfg.DevAuthBypass))
		r.Get("/api/auth/me", oauthH.Me)
		r.Post("/api/jobs", uploadLimiter(jobHandler.Create))
		r.Get("/api/jobs", jobHandler.List)
		r.Get("/api/jobs/{id}/progress", jobHandler.Progress)
		r.Post("/api/jobs/{id}/cancel", jobHandler.Cancel)
		r.Post("/api/jobs/{id}/retry", jobHandler.Retry)
		r.Post("/api/jobs/{id}/refine", jobHandler.Refine)
		r.Get("/api/jobs/{id}/download", jobHandler.Download)
		r.Get("/api/jobs/{id}/runs", jobHandler.Runs)
		r.Post("/api/jobs/{id}/share", jobHandler.ShareJob)
		r.Delete("/api/jobs/{id}", jobHandler.Delete)

		// Admin-only routes, gated by auth.RequireAdmin in addition to the
		// session check already applied by this group.
		r.Group(func(r chi.Router) {
			r.Use(auth.RequireAdmin(cfg.AdminEmails))
			r.Get("/api/admin/jobs", jobHandler.AdminListAllPaged)
			r.Post("/api/admin/jobs/{id}/cancel", jobHandler.AdminCancelJob)
			r.Post("/api/admin/jobs/cancel-all", jobHandler.CancelAll)
			r.Post("/api/admin/jobs/resume-all", jobHandler.ResumeAll)
			r.Post("/api/admin/jobs/{id}/regenerate-report", jobHandler.RegenerateReport)
			r.Get("/api/admin/system/state", jobHandler.SystemState)
			r.Delete("/api/admin/jobs/{id}", jobHandler.AdminDeleteJob)
			r.Get("/api/admin/deleted-jobs", jobHandler.AdminListDeletedJobs)
			r.Get("/api/admin/users", jobHandler.AdminListUsers)
			r.Post("/api/admin/users/{email}/role", jobHandler.AdminSetUserRole)
			r.Get("/api/admin/catalog/status", jobHandler.AdminCatalogStatus)
			r.Post("/api/admin/catalog/refresh", jobHandler.AdminCatalogRefresh)
			r.Get("/api/admin/audit", jobHandler.AdminAuditLog)
			r.Get("/api/admin/analytics", jobHandler.AdminAnalytics)
			r.Get("/api/admin/assumptions", jobHandler.AdminGetAssumptions)
			r.Put("/api/admin/assumptions/{key}", jobHandler.AdminPutAssumption)
			r.Get("/api/admin/assumptions/history", jobHandler.AdminGetAssumptionHistory)
			r.Get("/api/admin/report-settings", jobHandler.AdminGetReportSettings)
			r.Put("/api/admin/report-settings/{key}", jobHandler.AdminPutReportSetting)
			r.Get("/api/admin/report-settings/history", jobHandler.AdminGetReportSettingHistory)
			r.Post("/api/admin/report-settings/reset", jobHandler.AdminResetReportSettings)
			r.Get("/api/admin/shares", jobHandler.AdminListReportShares)
			r.Delete("/api/admin/shares/{token}", jobHandler.AdminDeleteReportShare)
			r.Get("/api/admin/jobs/{id}/diagnostics", jobHandler.AdminJobDiagnostics)
			r.Get("/api/admin/jobs/{id}/diagnostics/records", jobHandler.AdminJobDiagnosticRecords)
			r.Get("/api/admin/jobs/{id}/compare", jobHandler.AdminCompareRuns)
			r.Get("/api/admin/notify/status", jobHandler.AdminNotifyStatus)
			r.Post("/api/admin/notify/test", jobHandler.AdminNotifyTest)
		})
		// @facets.cloud-only internal panel: recent job logs + a manually
		// selected multi-job portfolio summary. Each handler re-checks
		// auth.Session.IsFacetsEmployee() itself — this route group only
		// requires a valid session, same as everything else above.
		r.Get("/api/facets/jobs", jobHandler.FacetsListJobs)
		r.Get("/api/facets/jobs/{id}/logs", jobHandler.FacetsLogs)
		r.Post("/api/facets/portfolio-summary", jobHandler.FacetsPortfolioSummary)
		r.Get("/api/facets/portfolio-summary.html", jobHandler.FacetsPortfolioSummaryHTML)
		r.Get("/api/facets/portfolio-summary.xlsx", jobHandler.FacetsPortfolioSummaryXLSX)
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

	jobs.StartCatalogRefresher(ctx)
	jobs.StartComputeAvailabilityRefresher(ctx)

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

// healthHandler returns the /health handler, closed over the catalog vintage
// read once at startup. Extracted from main() so a test can exercise the real
// handler rather than re-implementing its response shape — a test that builds
// its own handler passes even when this one is deleted.
func healthHandler(catalogFetchedAt, catalogAgeDays any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// Always 200 with status ok: this is a liveness probe. A stale or
		// unreadable catalog is reported in the body, never by failing the
		// probe — Render would restart the container.
		json.NewEncoder(w).Encode(map[string]any{
			"status":             "ok",
			"catalog_fetched_at": catalogFetchedAt,
			"catalog_age_days":   catalogAgeDays,
		})
	}
}

// loadCatalogVintage reads CATALOG_META.json once at server startup and returns
// the fetched_at timestamp and age in days. Degrades to nil on missing/corrupt metadata.
func loadCatalogVintage(skillDir string) (any, any) {
	metaPath := filepath.Join(skillDir, "data", "CATALOG_META.json")
	b, err := os.ReadFile(metaPath)
	if err != nil {
		return nil, nil
	}
	var meta struct {
		FetchedAt string `json:"fetched_at"`
	}
	if err := json.Unmarshal(b, &meta); err != nil || meta.FetchedAt == "" {
		return nil, nil
	}
	t, err := time.Parse(time.RFC3339, meta.FetchedAt)
	if err != nil {
		return meta.FetchedAt, nil
	}
	days := int(time.Since(t).Hours() / 24)
	return meta.FetchedAt, days
}

// versionHandler returns version/build info. The git SHA is injected at build
// time via -ldflags (see Makefile). Degrades gracefully when not set.
func versionHandler(catalogFetchedAt, catalogAgeDays any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"build_sha":          buildSHA,
			"build_time":         buildTime,
			"catalog_fetched_at": catalogFetchedAt,
			"catalog_age_days":   catalogAgeDays,
		})
	}
}

// buildSHA and buildTime are injected at build time via -ldflags. Degrade to
// "dev" / "unknown" when not set (local development).
var (
	buildSHA  = "dev"
	buildTime = "unknown"
)

// securityHeaders adds security headers to every response. Applied globally via
// middleware so it covers API responses, the HTML report, and the React SPA equally.
//
// CSP: the generated HTML report uses inline styles (report CSS is inlined),
// so style-src allows 'unsafe-inline'. The report contains no inline
// <script> tags — all JS is served as static assets — so script-src can stay
// 'self'.
//
// The policy also covers the SPA (index.html). The SPA loads Google Fonts
// (Plus Jakarta Sans and Inter) from external origins, so style-src allows
// https://fonts.googleapis.com (the stylesheet) and font-src allows
// https://fonts.gstatic.com (the webfont files).
func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		// HSTS only when the request actually reached us over HTTPS — setting
		// it on plain HTTP would break local development and could lock users
		// out if the site is later served over plain HTTP.
		//
		// r.TLS alone is not that test in production: Render (like any reverse
		// proxy) terminates TLS at the edge and forwards plain HTTP, so r.TLS
		// is nil on every live request and HSTS was never actually sent on the
		// deployed site. Trust X-Forwarded-Proto for the same reason, and in
		// the same shape, as internal/auth/oauth.go does when deciding whether
		// to mark the session cookie Secure.
		if r.TLS != nil || r.Header.Get("X-Forwarded-Proto") == "https" {
			w.Header().Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
		}
		w.Header().Set("Content-Security-Policy",
			"default-src 'self'; "+
				"img-src 'self' data:; "+
				"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "+
				"font-src 'self' https://fonts.gstatic.com;")
		next.ServeHTTP(w, r)
	})
}
