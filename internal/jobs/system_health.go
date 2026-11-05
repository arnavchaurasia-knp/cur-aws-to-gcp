package jobs

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/envmgr"
	"github.com/facets/cur-web/internal/skill"
)

var (
	serverStartedAt = time.Now().UTC()
	serverBuildSHA  = "dev"
	serverBuildTime = "unknown"
)

// SetServerInfo is called once from main with the -ldflags build info.
func SetServerInfo(sha, buildTime string) { serverBuildSHA, serverBuildTime = sha, buildTime }

// HealthSchemaVersion: bump whenever the /api/admin/health response shape
// changes, and bump EXPECTED_HEALTH_SCHEMA in the frontend in the same commit.
const HealthSchemaVersion = 3

type HealthIssue struct {
	ID       string       `json:"id"`       // stable, e.g. "engine.key_missing", "data.service_map"
	Severity string       `json:"severity"` // critical | warning | info
	Area     string       `json:"area"`     // engine | catalog | jobs | data | settings | runtime
	Title    string       `json:"title"`    // what's wrong, one line, plain English
	Detail   string       `json:"detail"`   // why it matters
	Fix      string       `json:"fix"`      // what to do
	Action   *IssueAction `json:"action,omitempty"`
}

type IssueAction struct {
	Kind   string `json:"kind"`             // test_engine | refresh_catalog | edit_env | open_store | open_tab | link
	Target string `json:"target,omitempty"` // env key / store key / tab id / path
	Label  string `json:"label"`            // button text, e.g. "Add the key"
}

type HealthResponse struct {
	SchemaVersion int            `json:"schema_version"` // HealthSchemaVersion
	CheckedAt     string         `json:"checked_at"`
	Status        string         `json:"status"`  // ok | warn | fail — DERIVED from Issues
	Summary       string         `json:"summary"` // one sentence
	Issues        []HealthIssue  `json:"issues"`  // sorted critical → warning → info
	Engine        EngineHealth   `json:"engine"`
	Catalog       CatalogHealth  `json:"catalog"`
	Jobs          JobsHealth     `json:"jobs"`
	Data          DataHealth     `json:"data"`
	Settings      SettingsHealth `json:"settings"`
	Runtime       RuntimeHealth  `json:"runtime"`
	Server        ServerInfo     `json:"server"`
}

type EngineHealth struct {
	Status              string             `json:"status"` // ok | warn | fail | unknown
	Summary             string             `json:"summary"`
	Model               string             `json:"model"`
	ModelSource         string             `json:"model_source"` // report_settings | env | default
	CLIFound            bool               `json:"cli_found"`
	CLIPath             string             `json:"cli_path"`
	CLIVersion          string             `json:"cli_version"`
	KeyPresent          bool               `json:"key_present"`
	KeySource           string             `json:"key_source"`            // app | server | none
	VerifiedBy          string             `json:"verified_by,omitempty"` // test | job
	LastTest            *EngineTestRecord  `json:"last_test,omitempty"`
	History             []EngineTestRecord `json:"history"`
	LastSuccessfulJobAt string             `json:"last_successful_job_at,omitempty"`
	LiveTestDisabled    bool               `json:"live_test_disabled"`
}

type CatalogHealth struct {
	Status         string              `json:"status"`
	Summary        string              `json:"summary"`
	SKUCount       int                 `json:"sku_count"`
	ServiceCount   int                 `json:"service_count"`
	FetchedAt      string              `json:"fetched_at"`
	AgeDays        int                 `json:"age_days"`
	StaleAfterDays int                 `json:"stale_after_days"` // catalogStaleDays()
	Stale          bool                `json:"stale"`
	ArtifactOK     bool                `json:"artifact_ok"`
	SourceTotal    int                 `json:"source_total"`
	SourceProblems []string            `json:"source_problems"` // "service: STATUS — issue"
	ImageBaked     bool                `json:"image_baked"`
	APIKeyPresent  bool                `json:"api_key_present"`
	LastRun        CatalogRefreshRun   `json:"last_run"` // GetCatalogRefreshStatus()
	History        []CatalogRefreshRun `json:"history"`  // GetCatalogRefreshHistory()
}

type JobsHealth struct {
	Status           string         `json:"status"`
	Summary          string         `json:"summary"`
	Running          int            `json:"running"`
	Queued           int            `json:"queued"`
	CompletedTotal   int            `json:"completed_total"`
	FailedTotal      int            `json:"failed_total"`
	RecentWindow     int            `json:"recent_window"`      // 20
	RecentFailed     int            `json:"recent_failed"`
	RecentFailurePct float64        `json:"recent_failure_pct"` // over the window
	AvgDurationSec   float64        `json:"avg_duration_sec"`
	ConcurrencyLimit int            `json:"concurrency_limit"` // from GetQuotaSettings, default 2
	Halted           bool           `json:"halted"`
	LastSuccessAt    string         `json:"last_success_at,omitempty"`
	LastFailureAt    string         `json:"last_failure_at,omitempty"`
	RecentFailures   []db.JobBrief  `json:"recent_failures"` // 5 newest, Error truncated to 300 chars
}

type DataHealth struct {
	Status  string        `json:"status"`
	Summary string        `json:"summary"`
	Stores  []StoreHealth `json:"stores"`
}

type SettingsHealth struct {
	Status           string              `json:"status"`
	Summary          string              `json:"summary"`
	EncryptionSource string              `json:"encryption_source"`
	OverrideCount    int                 `json:"override_count"`
	MissingRequired  []string            `json:"missing_required"` // keys of Required vars not effectively set
	IgnoredPresent   []envmgr.IgnoredVar `json:"ignored_present"`
	NotificationsOn  bool                `json:"notifications_on"` // email or slack configured
}

type RuntimeHealth struct {
	Status  string      `json:"status"`
	Summary string      `json:"summary"`
	Checks  []DepHealth `json:"checks"` // CheckRuntimeDependencies
}

type ServerInfo struct {
	SchemaVersion int    `json:"schema_version"`
	BuildSHA      string `json:"build_sha"`
	BuildTime     string `json:"build_time"`
	StartedAt     string `json:"started_at"`
	UptimeSec     int64  `json:"uptime_sec"`
	GoVersion     string `json:"go_version"` // runtime.Version()
	OS            string `json:"os"`         // runtime.GOOS + "/" + runtime.GOARCH
	PID           int    `json:"pid"`
	DataDir       string `json:"data_dir"` // filepath.Dir(h.jobsDir)
	SkillDir      string `json:"skill_dir"`
	ImageBaked    bool   `json:"image_baked"`
}

type catalogHealthJSON struct {
	GeneratedAt  string `json:"generated_at"`
	AgeDays      int    `json:"age_days"`
	Stale        bool   `json:"stale"`
	ArtifactOK   bool   `json:"artifact_ok"`
	Source       struct {
		Total    int `json:"total"`
		OK       int `json:"ok"`
		Problems int `json:"problems"`
		Bad      []struct {
			Service string   `json:"service"`
			Name    string   `json:"name"`
			Status  string   `json:"status"`
			Issues  []string `json:"issues"`
		} `json:"bad"`
	} `json:"source"`
	SKUCount     int    `json:"sku_count"`
	ServiceCount int    `json:"service_count"`
	Overall      string `json:"overall"`
}

var (
	catHealthCacheMu sync.Mutex
	catHealthCacheAt time.Time
	catHealthCached  *catalogHealthJSON
)

func getCatalogHealthCheck(skillDir string) (*catalogHealthJSON, error) {
	catHealthCacheMu.Lock()
	if catHealthCached != nil && time.Since(catHealthCacheAt) < 60*time.Second {
		cached := catHealthCached
		catHealthCacheMu.Unlock()
		return cached, nil
	}
	catHealthCacheMu.Unlock()

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	script := filepath.Join(skillDir, "scripts", "catalog_manager.py")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, python, script, "check", "--json", "--skill-dir", skillDir)
	cmd.Dir = skillDir
	out, err := cmd.Output()
	if err != nil && len(out) == 0 {
		return nil, fmt.Errorf("check failed: %w", err)
	}

	var res catalogHealthJSON
	if jsonErr := json.Unmarshal(out, &res); jsonErr != nil {
		return nil, fmt.Errorf("unmarshal error: %w (output: %s)", jsonErr, string(out))
	}

	catHealthCacheMu.Lock()
	catHealthCached = &res
	catHealthCacheAt = time.Now()
	catHealthCacheMu.Unlock()

	return &res, nil
}

func parseDBTime(s string) (time.Time, error) {
	if s == "" {
		return time.Time{}, fmt.Errorf("empty")
	}
	if t, err := time.Parse("2006-01-02 15:04:05", s); err == nil {
		return t.UTC(), nil
	}
	return time.Parse(time.RFC3339, s)
}

func formatDurationAgo(d time.Duration) string {
	if d < time.Minute {
		return "just now"
	}
	if d < time.Hour {
		m := int(d.Minutes())
		if m == 1 {
			return "1 minute ago"
		}
		return fmt.Sprintf("%d minutes ago", m)
	}
	if d < 24*time.Hour {
		h := int(d.Hours())
		if h == 1 {
			return "1 hour ago"
		}
		return fmt.Sprintf("%d hours ago", h)
	}
	days := int(d.Hours() / 24)
	if days == 1 {
		return "1 day ago"
	}
	return fmt.Sprintf("%d days ago", days)
}

// engineHealth inspects the AI engine and returns its component health and issues.
func (h *Handler) engineHealth(ctx context.Context) (EngineHealth, []HealthIssue) {
	dataDir := filepath.Dir(h.jobsDir)
	model, modelSource := h.spawner.activeModel()
	p := h.spawner.InspectEngine(ctx)

	keySource := "none"
	if envmgr.HasOverride("ANTIGRAVITY_API_KEY") {
		keySource = "app"
	} else if p.AuthPresent {
		keySource = "server"
	}

	liveDisabled := os.Getenv("AGY_HEALTH_PROBE_DISABLED") == "1"
	hist := loadEngineHistory(dataDir)
	if hist == nil {
		hist = []EngineTestRecord{}
	}

	var lastTest *EngineTestRecord
	if len(hist) > 0 {
		lastTest = &hist[0]
	}

	status := "unknown"
	summary := "Not verified recently — run a test."
	verifiedBy := ""

	if p.State == "fail" {
		status = "fail"
		summary = p.Detail
	} else if lastTest != nil {
		testTime, err := parseDBTime(lastTest.CheckedAt)
		if err == nil && time.Since(testTime) < 24*time.Hour {
			status = lastTest.State
			verifiedBy = "test"
			summary = "Last test: " + lastTest.Detail
		}
	}

	lastDoneStr, _ := h.db.LastJobUpdateAt("done")
	lastDoneRFC := ""
	if lastDoneStr != "" {
		if t, err := parseDBTime(lastDoneStr); err == nil {
			lastDoneRFC = t.Format(time.RFC3339)
			if status == "unknown" && time.Since(t) < 24*time.Hour {
				status = "ok"
				verifiedBy = "job"
				summary = fmt.Sprintf("A projection job completed successfully %s.", formatDurationAgo(time.Since(t)))
			}
		}
	}

	eng := EngineHealth{
		Status:              status,
		Summary:             summary,
		Model:               model,
		ModelSource:         modelSource,
		CLIFound:            p.BinaryFound,
		CLIPath:             p.BinaryPath,
		CLIVersion:          p.Version,
		KeyPresent:          p.AuthPresent,
		KeySource:           keySource,
		VerifiedBy:          verifiedBy,
		LastTest:            lastTest,
		History:             hist,
		LastSuccessfulJobAt: lastDoneRFC,
		LiveTestDisabled:    liveDisabled,
	}

	issues := buildEngineIssues(eng, p)
	return eng, issues
}

func buildEngineIssues(eng EngineHealth, p EngineProbe) []HealthIssue {
	var issues []HealthIssue

	if !p.BinaryFound {
		issues = append(issues, HealthIssue{
			ID:       "engine.cli_missing",
			Severity: "critical",
			Area:     "engine",
			Title:    "The AI engine tool (agy) isn't installed or can't be found",
			Detail:   p.Detail,
			Fix:      "Install agy on the server, or set its full path in Settings & Secrets → agy tool location.",
			Action: &IssueAction{
				Kind:   "edit_env",
				Target: "AGY_PATH",
				Label:  "Set agy location",
			},
		})
	} else if !p.AuthPresent {
		fix := "Add ANTIGRAVITY_API_KEY in Settings & Secrets. Every projection job fails without it."
		if os.Getenv("AGY_API_KEY") != "" || os.Getenv("GEMINI_API_KEY") != "" {
			fix += " AGY_API_KEY is set, but the app never reads that name."
		}
		issues = append(issues, HealthIssue{
			ID:       "engine.key_missing",
			Severity: "critical",
			Area:     "engine",
			Title:    "The AI engine API key is missing",
			Detail:   "The AI engine cannot run without an API key. Projections will fail.",
			Fix:      fix,
			Action: &IssueAction{
				Kind:   "edit_env",
				Target: "ANTIGRAVITY_API_KEY",
				Label:  "Add the key",
			},
		})
	}

	if eng.LastTest != nil {
		testTime, err := parseDBTime(eng.LastTest.CheckedAt)
		if err == nil && time.Since(testTime) < 24*time.Hour {
			if eng.LastTest.State == "fail" {
				issues = append(issues, HealthIssue{
					ID:       "engine.test_failed",
					Severity: "critical",
					Area:     "engine",
					Title:    "The last AI engine test failed",
					Detail:   eng.LastTest.Detail,
					Fix:      "Check the key and network, then run the test again.",
					Action: &IssueAction{
						Kind:  "test_engine",
						Label: "Test again",
					},
				})
			} else if eng.LastTest.State == "warn" {
				issues = append(issues, HealthIssue{
					ID:       "engine.slow",
					Severity: "warning",
					Area:     "engine",
					Title:    "The AI engine is responding slowly",
					Detail:   eng.LastTest.Detail,
					Fix:      "Jobs will take longer; usually temporary. Re-test later.",
					Action: &IssueAction{
						Kind:  "test_engine",
						Label: "Test again",
					},
				})
			}
		}
	}

	if eng.Status == "unknown" && !eng.LiveTestDisabled {
		issues = append(issues, HealthIssue{
			ID:       "engine.unverified",
			Severity: "info",
			Area:     "engine",
			Title:    "The AI engine hasn't been verified recently",
			Detail:   "Run a quick test to confirm the AI engine and model answer.",
			Fix:      "Run a quick test (uses a tiny amount of quota).",
			Action: &IssueAction{
				Kind:  "test_engine",
				Label: "Run test",
			},
		})
	}

	return issues
}

func (h *Handler) catalogHealth() (CatalogHealth, []HealthIssue) {
	skillDir := skill.ResolveDir()
	catStatus := GetCatalogStatus()
	staleDays := catalogStaleDays()
	baked := isImageBaked(skillDir)
	apiKeyPresent := os.Getenv("GOOGLE_CLOUD_API_KEY") != "" || os.Getenv("GCP_API_KEY") != ""

	skuCount := 0
	svcCount := 0
	fetchedAt := ""
	ageDays := catStatus.AgeDays
	stale := catStatus.Stale
	artifactOK := true
	sourceTotal := 0
	var sourceProblems []string

	metaFile := filepath.Join(skillDir, "data", "CATALOG_META.json")
	if data, err := os.ReadFile(metaFile); err == nil {
		var meta struct {
			FetchedAt    string `json:"fetched_at"`
			ServiceCount int    `json:"service_count"`
			SKUCount     int    `json:"sku_count"`
		}
		if json.Unmarshal(data, &meta) == nil {
			skuCount = meta.SKUCount
			svcCount = meta.ServiceCount
			fetchedAt = meta.FetchedAt
		}
	}

	if checkRes, err := getCatalogHealthCheck(skillDir); err == nil && checkRes != nil {
		if checkRes.SKUCount > 0 {
			skuCount = checkRes.SKUCount
		}
		if checkRes.ServiceCount > 0 {
			svcCount = checkRes.ServiceCount
		}
		if checkRes.AgeDays >= 0 {
			ageDays = checkRes.AgeDays
		}
		stale = checkRes.Stale
		artifactOK = checkRes.ArtifactOK
		sourceTotal = checkRes.Source.Total
		for _, b := range checkRes.Source.Bad {
			if len(b.Issues) > 0 {
				sourceProblems = append(sourceProblems, fmt.Sprintf("%s: %s — %s", b.Service, b.Status, strings.Join(b.Issues, ", ")))
			} else {
				sourceProblems = append(sourceProblems, fmt.Sprintf("%s: %s", b.Service, b.Status))
			}
		}
	}

	if sourceProblems == nil {
		sourceProblems = []string{}
	}

	lastRun := GetCatalogRefreshStatus()
	history := GetCatalogRefreshHistory()
	if history == nil {
		history = []CatalogRefreshRun{}
	}

	status := "ok"
	summary := "Pricing catalog is healthy and up to date."
	if !artifactOK {
		status = "fail"
		summary = "Pricing catalog database is missing or corrupt."
	} else if stale || len(sourceProblems) > 0 || lastRun.State == "failed" {
		status = "warn"
		if stale {
			summary = fmt.Sprintf("GCP prices are %d days old (refresh recommended).", ageDays)
		} else if lastRun.State == "failed" {
			summary = "The last catalog refresh failed."
		} else {
			summary = fmt.Sprintf("%d price files are incomplete.", len(sourceProblems))
		}
	}

	cat := CatalogHealth{
		Status:         status,
		Summary:        summary,
		SKUCount:       skuCount,
		ServiceCount:   svcCount,
		FetchedAt:      fetchedAt,
		AgeDays:        ageDays,
		StaleAfterDays: staleDays,
		Stale:          stale,
		ArtifactOK:     artifactOK,
		SourceTotal:    sourceTotal,
		SourceProblems: sourceProblems,
		ImageBaked:     baked,
		APIKeyPresent:  apiKeyPresent,
		LastRun:        lastRun,
		History:        history,
	}

	issues := buildCatalogIssues(cat)
	return cat, issues
}

func buildCatalogIssues(cat CatalogHealth) []HealthIssue {
	var issues []HealthIssue

	refreshAction := &IssueAction{Kind: "refresh_catalog", Label: "Refresh catalog"}
	if !cat.APIKeyPresent {
		refreshAction = &IssueAction{Kind: "edit_env", Target: "GOOGLE_CLOUD_API_KEY", Label: "Add pricing API key"}
	}

	if !cat.ArtifactOK {
		issues = append(issues, HealthIssue{
			ID:       "catalog.artifact",
			Severity: "critical",
			Area:     "catalog",
			Title:    "The pricing catalog database is missing or broken",
			Detail:   "Projections can't price anything without a valid catalog database.",
			Fix:      "Projections can't price anything. Run a catalog refresh.",
			Action:   refreshAction,
		})
	}

	if cat.Stale {
		issues = append(issues, HealthIssue{
			ID:       "catalog.stale",
			Severity: "warning",
			Area:     "catalog",
			Title:    fmt.Sprintf("GCP prices are %d days old (refresh after %d days)", cat.AgeDays, cat.StaleAfterDays),
			Detail:   "Prices change over time. Keeping the catalog fresh ensures accurate projections.",
			Fix:      "Refresh the catalog. Needs the Google Cloud pricing API key.",
			Action:   refreshAction,
		})
	}

	if cat.LastRun.State == "failed" {
		detail := cat.LastRun.Reason
		if !cat.APIKeyPresent {
			if detail != "" {
				detail += " "
			}
			detail += "No GOOGLE_CLOUD_API_KEY is set — that is the usual cause."
		}
		var action *IssueAction
		if !cat.APIKeyPresent {
			action = &IssueAction{Kind: "edit_env", Target: "GOOGLE_CLOUD_API_KEY", Label: "Add pricing API key"}
		} else {
			action = &IssueAction{Kind: "open_tab", Target: "catalog", Label: "See the log"}
		}
		issues = append(issues, HealthIssue{
			ID:       "catalog.refresh_failed",
			Severity: "warning",
			Area:     "catalog",
			Title:    "The last catalog refresh failed",
			Detail:   detail,
			Fix:      "Check the pricing API key and network connectivity, then try refreshing again.",
			Action:   action,
		})
	}

	if len(cat.SourceProblems) > 0 {
		issues = append(issues, HealthIssue{
			ID:       "catalog.source",
			Severity: "warning",
			Area:     "catalog",
			Title:    fmt.Sprintf("%d price files are incomplete", len(cat.SourceProblems)),
			Detail:   strings.Join(cat.SourceProblems, "; "),
			Fix:      "Refresh the catalog to re-download them.",
			Action:   &IssueAction{Kind: "refresh_catalog", Label: "Refresh catalog"},
		})
	}

	if cat.ImageBaked {
		issues = append(issues, HealthIssue{
			ID:       "catalog.image_baked",
			Severity: "info",
			Area:     "catalog",
			Title:    "Catalog is built into the server image",
			Detail:   "The pricing catalog is baked into the deployment image.",
			Fix:      "It refreshes on each deploy, not from this page.",
		})
	}

	return issues
}

func (h *Handler) jobsHealth() (JobsHealth, []HealthIssue) {
	dataDir := filepath.Dir(h.jobsDir)
	halted := IsStopAllActive(dataDir)

	running, _ := h.db.CountRunningJobs()
	queued, _ := h.db.CountPendingJobs()
	stats, _ := h.db.GetUsageStats()

	completedTotal := 0
	failedTotal := 0
	avgDuration := 0.0
	if stats != nil {
		completedTotal = stats.DoneJobs
		failedTotal = stats.FailedJobs
		avgDuration = stats.AvgProcessingSec
	}

	window := 20
	doneCount, failedCount, _ := h.db.RecentOutcomes(window)
	recentTotal := doneCount + failedCount
	recentFailurePct := 0.0
	if recentTotal > 0 {
		recentFailurePct = (float64(failedCount) / float64(recentTotal)) * 100.0
	}

	_, maxConcurrent, _ := h.db.GetQuotaSettings()
	if maxConcurrent <= 0 {
		maxConcurrent = 2
	}

	lastSuccess, _ := h.db.LastJobUpdateAt("done")
	lastFailure, _ := h.db.LastJobUpdateAt("failed")

	recentFailures, _ := h.db.ListRecentJobsByStatus("failed", 5)
	if recentFailures == nil {
		recentFailures = []db.JobBrief{}
	}
	for i := range recentFailures {
		if len(recentFailures[i].Error) > 300 {
			recentFailures[i].Error = recentFailures[i].Error[:300] + "..."
		}
	}

	status := "ok"
	summary := "Jobs are processing normally."
	if halted {
		status = "fail"
		summary = "All job processing is paused (STOP ALL is on)."
	} else if recentTotal >= 4 && recentFailurePct > 25.0 {
		status = "fail"
		summary = fmt.Sprintf("%d of the last %d jobs failed.", failedCount, recentTotal)
	} else if recentTotal >= 10 && recentFailurePct > 5.0 {
		status = "warn"
		summary = fmt.Sprintf("%d of the last %d jobs failed.", failedCount, recentTotal)
	}

	jh := JobsHealth{
		Status:           status,
		Summary:          summary,
		Running:          running,
		Queued:           queued,
		CompletedTotal:   completedTotal,
		FailedTotal:      failedTotal,
		RecentWindow:     window,
		RecentFailed:     failedCount,
		RecentFailurePct: recentFailurePct,
		AvgDurationSec:   avgDuration,
		ConcurrencyLimit: maxConcurrent,
		Halted:           halted,
		LastSuccessAt:    lastSuccess,
		LastFailureAt:    lastFailure,
		RecentFailures:   recentFailures,
	}

	issues := buildJobsIssues(jh, recentTotal)
	return jh, issues
}

func buildJobsIssues(jh JobsHealth, recentTotal int) []HealthIssue {
	var issues []HealthIssue

	if jh.Halted {
		issues = append(issues, HealthIssue{
			ID:       "jobs.halted",
			Severity: "critical",
			Area:     "jobs",
			Title:    "All job processing is paused (STOP ALL is on)",
			Detail:   "The job processor is paused. New uploads and retries will not run.",
			Fix:      "New uploads won't run until it's resumed.",
			Action: &IssueAction{
				Kind:   "link",
				Target: "/admin",
				Label:  "Open All Projections",
			},
		})
	}

	if recentTotal >= 4 && jh.RecentFailurePct > 25.0 {
		issues = append(issues, HealthIssue{
			ID:       "jobs.failing",
			Severity: "critical",
			Area:     "jobs",
			Title:    fmt.Sprintf("%d of the last %d jobs failed", jh.RecentFailed, recentTotal),
			Detail:   "A high percentage of recent jobs failed to complete successfully.",
			Fix:      "Open the failures below to see the error.",
			Action: &IssueAction{
				Kind:   "open_tab",
				Target: "jobs",
				Label:  "See failures",
			},
		})
	} else if recentTotal >= 10 && jh.RecentFailurePct > 5.0 {
		issues = append(issues, HealthIssue{
			ID:       "jobs.some_failing",
			Severity: "warning",
			Area:     "jobs",
			Title:    fmt.Sprintf("%d of the last %d jobs failed", jh.RecentFailed, recentTotal),
			Detail:   "Some recent jobs encountered errors during processing.",
			Fix:      "Open the failures below to see the error.",
			Action: &IssueAction{
				Kind:   "open_tab",
				Target: "jobs",
				Label:  "See failures",
			},
		})
	}

	return issues
}

func (h *Handler) dataHealth() (DataHealth, []HealthIssue) {
	skillDir := skill.ResolveDir()
	stores, dataStatus := CheckDataStores(skillDir)
	if stores == nil {
		stores = []StoreHealth{}
	}

	summary := "All data dependency files are valid."
	if dataStatus == "fail" {
		summary = "One or more critical data files are missing or unreadable."
	} else if dataStatus == "warn" {
		summary = "One or more data files need attention."
	}

	dh := DataHealth{
		Status:  dataStatus,
		Summary: summary,
		Stores:  stores,
	}

	issues := buildDataIssues(stores)
	return dh, issues
}

func buildDataIssues(stores []StoreHealth) []HealthIssue {
	var issues []HealthIssue
	for _, s := range stores {
		title := s.Title
		if title == "" {
			title = s.Key
		}
		if s.Status == "fail" {
			issues = append(issues, HealthIssue{
				ID:       "data." + s.Key,
				Severity: "critical",
				Area:     "data",
				Title:    fmt.Sprintf("%q is missing or unreadable", title),
				Detail:   s.Detail,
				Fix:      "Restore a backup or redeploy the file.",
				Action: &IssueAction{
					Kind:   "open_store",
					Target: s.Key,
					Label:  "Open file",
				},
			})
		} else if s.Status == "warn" {
			issues = append(issues, HealthIssue{
				ID:       "data." + s.Key,
				Severity: "warning",
				Area:     "data",
				Title:    fmt.Sprintf("%q needs a look", title),
				Detail:   s.Detail,
				Fix:      s.Detail,
				Action: &IssueAction{
					Kind:   "open_store",
					Target: s.Key,
					Label:  "Open file",
				},
			})
		}
	}
	return issues
}

func (h *Handler) settingsHealth() (SettingsHealth, []HealthIssue) {
	src, _, problem := db.EncryptionKeyInfo()
	encryptionAvail := db.EncryptionAvailable()
	overCount := envmgr.OverrideCount()

	var missingRequired []string
	for _, v := range envmgr.All() {
		if v.Required && v.Mode == envmgr.ModeManaged && v.Key != "ANTIGRAVITY_API_KEY" {
			_, _, effOk := envmgr.Effective(v)
			if !effOk {
				missingRequired = append(missingRequired, v.Key)
			}
		}
	}
	if missingRequired == nil {
		missingRequired = []string{}
	}

	ignored := envmgr.IgnoredPresent()
	if ignored == nil {
		ignored = []envmgr.IgnoredVar{}
	}

	emailOk := h.emailCfg().APIKey != ""
	slackOk := h.slackURL() != ""
	notificationsOn := emailOk || slackOk

	status := "ok"
	summary := "Settings and encryption are configured."
	if len(missingRequired) > 0 {
		status = "fail"
		summary = fmt.Sprintf("%d required setting(s) are missing.", len(missingRequired))
	} else if !encryptionAvail || len(ignored) > 0 {
		status = "warn"
		if !encryptionAvail {
			summary = "Secrets cannot be saved because no encryption key is available."
		} else {
			summary = "Some ignored environment variables are present."
		}
	}

	sh := SettingsHealth{
		Status:           status,
		Summary:          summary,
		EncryptionSource: src,
		OverrideCount:    overCount,
		MissingRequired:  missingRequired,
		IgnoredPresent:   ignored,
		NotificationsOn:  notificationsOn,
	}

	appBaseURL := os.Getenv("APP_BASE_URL")
	devAuthBypass := os.Getenv("DEV_AUTH_BYPASS")
	issues := buildSettingsIssues(sh, appBaseURL, devAuthBypass, src, problem, encryptionAvail)
	return sh, issues
}

func buildSettingsIssues(sh SettingsHealth, appBaseURL, devAuthBypass string, keySource, keyProblem string, encryptionAvail bool) []HealthIssue {
	var issues []HealthIssue

	for _, k := range sh.MissingRequired {
		v, ok := envmgr.Lookup(k)
		title := k
		desc := "Required setting is not configured."
		if ok {
			title = v.Title
			desc = v.Description
		}
		issues = append(issues, HealthIssue{
			ID:       "settings.missing." + k,
			Severity: "critical",
			Area:     "settings",
			Title:    fmt.Sprintf("%q is not set", title),
			Detail:   desc,
			Fix:      "Set this variable in Settings & Secrets.",
			Action: &IssueAction{
				Kind:   "edit_env",
				Target: k,
				Label:  "Set value",
			},
		})
	}

	for _, v := range envmgr.All() {
		if loadErr := envmgr.LoadError(v.Key); loadErr != "" {
			issues = append(issues, HealthIssue{
				ID:       "settings.load_error." + v.Key,
				Severity: "warning",
				Area:     "settings",
				Title:    fmt.Sprintf("The saved %q can't be read", v.Title),
				Detail:   loadErr,
				Fix:      "Re-enter it (the encryption key changed).",
				Action: &IssueAction{
					Kind:   "edit_env",
					Target: v.Key,
					Label:  "Re-enter",
				},
			})
		}
	}

	for _, iv := range sh.IgnoredPresent {
		useInsteadSet := false
		if iv.UseInstead != "" {
			if targetVar, ok := envmgr.Lookup(iv.UseInstead); ok {
				_, _, useInsteadSet = envmgr.Effective(targetVar)
			}
		}
		sev := "warning"
		if useInsteadSet {
			sev = "info"
		}
		issues = append(issues, HealthIssue{
			ID:       "settings.ignored." + iv.Key,
			Severity: sev,
			Area:     "settings",
			Title:    fmt.Sprintf("%s is set but the app never reads it", iv.Key),
			Detail:   fmt.Sprintf("The application does not read %s. Use %s instead.", iv.Key, iv.UseInstead),
			Fix:      fmt.Sprintf("Use %s instead (Settings & Secrets), then remove %s from .env.", iv.UseInstead, iv.Key),
			Action: &IssueAction{
				Kind:   "edit_env",
				Target: iv.UseInstead,
				Label:  "Set " + iv.UseInstead,
			},
		})
	}

	if !encryptionAvail {
		prob := keyProblem
		if prob == "" {
			prob = "No valid encryption key is present."
		}
		issues = append(issues, HealthIssue{
			ID:       "settings.encryption_none",
			Severity: "warning",
			Area:     "settings",
			Title:    "Secrets can't be saved in the app",
			Detail:   prob,
			Fix:      "Set SETTINGS_ENCRYPTION_KEY (generate: openssl rand -base64 32) in .env and restart.",
			Action: &IssueAction{
				Kind:   "open_tab",
				Target: "settings",
				Label:  "Open settings",
			},
		})
	} else if (keySource == "file" || keySource == "generated") && strings.HasPrefix(appBaseURL, "https://") {
		issues = append(issues, HealthIssue{
			ID:       "settings.encryption_on_disk",
			Severity: "warning",
			Area:     "settings",
			Title:    "Saved secrets depend on a key file on this server's disk",
			Detail:   "If the disk is replaced on redeploy, saved secrets are lost.",
			Fix:      "Set SETTINGS_ENCRYPTION_KEY in the deployment.",
			Action: &IssueAction{
				Kind:   "open_tab",
				Target: "settings",
				Label:  "Open settings",
			},
		})
	}

	if devAuthBypass == "true" {
		u, err := url.Parse(appBaseURL)
		isLocal := false
		if err == nil {
			h := u.Hostname()
			if h == "localhost" || h == "127.0.0.1" || h == "" {
				isLocal = true
			}
		}
		if !isLocal {
			issues = append(issues, HealthIssue{
				ID:       "settings.dev_bypass",
				Severity: "critical",
				Area:     "settings",
				Title:    "Anyone can sign in with any password",
				Detail:   "DEV_AUTH_BYPASS is on for a non-local server. Any email can sign in without authentication.",
				Fix:      "DEV_AUTH_BYPASS is on for a non-local server. Remove it from the deployment and restart.",
			})
		}
	}

	if !sh.NotificationsOn {
		issues = append(issues, HealthIssue{
			ID:       "settings.notifications_off",
			Severity: "info",
			Area:     "settings",
			Title:    "Job notifications are off",
			Detail:   "No email or Slack webhook is configured to notify when jobs finish or fail.",
			Fix:      "Set up email or Slack on the Integrations page.",
			Action: &IssueAction{
				Kind:   "link",
				Target: "/admin/integrations",
				Label:  "Configure integrations",
			},
		})
	}

	return issues
}

func (h *Handler) runtimeHealth() (RuntimeHealth, []HealthIssue) {
	skillDir := skill.ResolveDir()
	checks, status := CheckRuntimeDependencies(skillDir, h.jobsDir)
	if checks == nil {
		checks = []DepHealth{}
	}

	summary := "All runtime tools and directories are operational."
	if status == "fail" {
		summary = "One or more critical runtime dependencies are missing."
	} else if status == "warn" {
		summary = "One or more runtime tools reported warnings."
	}

	rh := RuntimeHealth{
		Status:  status,
		Summary: summary,
		Checks:  checks,
	}

	issues := buildRuntimeIssues(checks)
	return rh, issues
}

func buildRuntimeIssues(checks []DepHealth) []HealthIssue {
	var issues []HealthIssue
	for _, c := range checks {
		if c.Status == "fail" {
			issues = append(issues, HealthIssue{
				ID:       "runtime." + c.Key,
				Severity: "critical",
				Area:     "runtime",
				Title:    fmt.Sprintf("%s — %s", c.Title, c.Detail),
				Detail:   c.Detail,
				Fix:      c.Fix,
			})
		} else if c.Status == "warn" {
			issues = append(issues, HealthIssue{
				ID:       "runtime." + c.Key,
				Severity: "warning",
				Area:     "runtime",
				Title:    fmt.Sprintf("%s — %s", c.Title, c.Detail),
				Detail:   c.Detail,
				Fix:      c.Fix,
			})
		}
	}
	return issues
}

func deriveOverallHealth(issues []HealthIssue) (status string, summary string) {
	status = "ok"
	nonInfoCount := 0
	for _, iss := range issues {
		if iss.Severity == "critical" {
			status = "fail"
		} else if iss.Severity == "warning" && status != "fail" {
			status = "warn"
		}
		if iss.Severity != "info" {
			nonInfoCount++
		}
	}

	summary = "Everything is working."
	if len(issues) > 0 {
		summary = issues[0].Title
		if nonInfoCount > 1 {
			summary = fmt.Sprintf("%s (and %d more)", issues[0].Title, nonInfoCount-1)
		}
	}
	return status, summary
}

func (h *Handler) serverInfo() ServerInfo {
	dataDir := filepath.Dir(h.jobsDir)
	skillDir := skill.ResolveDir()
	return ServerInfo{
		SchemaVersion: HealthSchemaVersion,
		BuildSHA:      serverBuildSHA,
		BuildTime:     serverBuildTime,
		StartedAt:     serverStartedAt.Format(time.RFC3339),
		UptimeSec:     int64(time.Since(serverStartedAt).Seconds()),
		GoVersion:     runtime.Version(),
		OS:            runtime.GOOS + "/" + runtime.GOARCH,
		PID:           os.Getpid(),
		DataDir:       dataDir,
		SkillDir:      skillDir,
		ImageBaked:    isImageBaked(skillDir),
	}
}

// AdminHealthSummary returns the unified single-source-of-truth System Health response.
func (h *Handler) AdminHealthSummary(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	ctx := r.Context()
	eng, engIssues := h.engineHealth(ctx)
	cat, catIssues := h.catalogHealth()
	jobsH, jobsIssues := h.jobsHealth()
	dataH, dataIssues := h.dataHealth()
	settingsH, settingsIssues := h.settingsHealth()
	runtimeH, runtimeIssues := h.runtimeHealth()
	server := h.serverInfo()

	var issues []HealthIssue
	issues = append(issues, engIssues...)
	issues = append(issues, catIssues...)
	issues = append(issues, jobsIssues...)
	issues = append(issues, dataIssues...)
	issues = append(issues, settingsIssues...)
	issues = append(issues, runtimeIssues...)

	sort.SliceStable(issues, func(i, j int) bool {
		rank := func(sev string) int {
			switch sev {
			case "critical":
				return 0
			case "warning":
				return 1
			case "info":
				return 2
			default:
				return 3
			}
		}
		return rank(issues[i].Severity) < rank(issues[j].Severity)
	})

	if issues == nil {
		issues = []HealthIssue{}
	}

	status, summary := deriveOverallHealth(issues)

	resp := HealthResponse{
		SchemaVersion: HealthSchemaVersion,
		CheckedAt:     time.Now().UTC().Format(time.RFC3339),
		Status:        status,
		Summary:       summary,
		Issues:        issues,
		Engine:        eng,
		Catalog:       cat,
		Jobs:          jobsH,
		Data:          dataH,
		Settings:      settingsH,
		Runtime:       runtimeH,
		Server:        server,
	}

	writeJSON(w, resp)
}

// AdminTestEngine performs a live test probe against the AI engine.
func (h *Handler) AdminTestEngine(w http.ResponseWriter, r *http.Request) {
	sess := h.adminOnly(w, r)
	if sess == nil {
		return
	}

	p := h.spawner.ProbeEngine(r.Context(), true)
	dataDir := filepath.Dir(h.jobsDir)
	appendEngineHistory(dataDir, EngineTestRecord{EngineProbe: p, Actor: sess.Email})
	_ = h.db.LogAdminAction(sess.Email, "engine_test", p.Model, fmt.Sprintf("state=%s latency=%dms", p.State, p.LatencyMS))

	writeJSON(w, p)
}
