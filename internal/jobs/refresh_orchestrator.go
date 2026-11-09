package jobs

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/notify"
	"github.com/facets/cur-web/internal/skill"
)

// SourceExecutor runs a refresh for a specific source and returns the finished run.
type SourceExecutor func(ctx context.Context, source, trigger, actor string) RefreshRun

// OrchestratorConfig configures the RefreshOrchestrator.
type OrchestratorConfig struct {
	DB               *db.DB
	EmailCfg         func() notify.EmailConfig
	SlackURL         func() string
	AdminEmails      []string
	TickInterval     time.Duration
	Clock            func() time.Time
	SourcesLoader    func() (map[string]RefreshSourceConfig, error)
	Executors        map[string]SourceExecutor
	OnEscalationSent func(source string, attempts int, lastErr string)
	OnRecoverySent   func(source string)
}

// RefreshOrchestrator coordinates the lifecycle, retries, and dependencies of all refresh sources.
type RefreshOrchestrator struct {
	mu               sync.Mutex
	cfg              OrchestratorConfig
	triggerCh        chan string
	isSourceRunning  bool
	runningSource    string
	clock            func() time.Time
	sourcesLoader    func() (map[string]RefreshSourceConfig, error)
	executors        map[string]SourceExecutor
	onEscalationSent func(source string, attempts int, lastErr string)
	onRecoverySent   func(source string)
}

var (
	defaultOrchestrator   *RefreshOrchestrator
	defaultOrchestratorMu sync.Mutex
)

// ── Persistence Helpers ──────────────────────────────────────────────────

func refreshRunsDir() string {
	return filepath.Join(skill.ResolveDir(), "data", "refresh_runs")
}

func sourceStatusPath(source string) string {
	return filepath.Join(refreshRunsDir(), source+".json")
}

func sourceHistoryPath(source string) string {
	return filepath.Join(refreshRunsDir(), source+".history.json")
}

// ReadSourceRun reads the latest run for a source, falling back to catalog_refresh_status.json for gcp_catalog.
func ReadSourceRun(source string) RefreshRun {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	return readSourceRunLocked(source)
}

func readSourceRunLocked(source string) RefreshRun {
	fallback := RefreshRun{Source: source, State: "idle"}
	path := sourceStatusPath(source)
	data, err := os.ReadFile(path)
	if err != nil {
		if source == "gcp_catalog" {
			legacy := readRefreshRunLocked(refreshStatusPath())
			if legacy.State != "" {
				legacy.Source = "gcp_catalog"
				return legacy
			}
		}
		return fallback
	}
	var run RefreshRun
	if err := json.Unmarshal(data, &run); err != nil || run.State == "" {
		return fallback
	}
	if run.Source == "" {
		run.Source = source
	}
	return run
}

// WriteSourceRun writes the latest run and appends finished runs to history.
func WriteSourceRun(source string, run RefreshRun) {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	writeSourceRunLocked(source, run)
}

func writeSourceRunLocked(source string, run RefreshRun) {
	if run.Source == "" {
		run.Source = source
	}
	dir := refreshRunsDir()
	_ = os.MkdirAll(dir, 0755)

	path := sourceStatusPath(source)
	data, err := json.MarshalIndent(run, "", "  ")
	if err == nil {
		_ = atomicWriteFile(path, data)
	}

	if run.State == "success" || run.State == "failed" || run.State == "skipped" || run.State == "partial" || run.State == "escalated" {
		appendSourceHistoryLocked(source, run)
	}

	// Maintain backward compatibility for gcp_catalog
	if source == "gcp_catalog" {
		writeRefreshRunLocked(refreshStatusPath(), run)
		if run.State == "success" || run.State == "failed" || run.State == "skipped" || run.State == "partial" {
			appendRefreshHistoryLocked(run)
		}
	}
}

func appendSourceHistoryLocked(source string, run RefreshRun) {
	historyPath := sourceHistoryPath(source)
	var runs []RefreshRun
	if data, err := os.ReadFile(historyPath); err == nil {
		_ = json.Unmarshal(data, &runs)
	}
	if len(run.LogTail) > 15 {
		run.LogTail = run.LogTail[len(run.LogTail)-15:]
	}
	runs = append([]RefreshRun{run}, runs...)
	if len(runs) > refreshHistoryMax {
		runs = runs[:refreshHistoryMax]
	}
	if b, err := json.MarshalIndent(runs, "", "  "); err == nil {
		_ = atomicWriteFile(historyPath, b)
	}
}

// GetSourceRefreshStatus returns the latest status for a source.
func GetSourceRefreshStatus(source string) RefreshRun {
	return ReadSourceRun(source)
}

// GetSourceRefreshHistory returns finished runs for a source, newest first.
func GetSourceRefreshHistory(source string) []RefreshRun {
	refreshRunMu.Lock()
	defer refreshRunMu.Unlock()
	historyPath := sourceHistoryPath(source)
	data, err := os.ReadFile(historyPath)
	if err != nil {
		if source == "gcp_catalog" {
			return readRefreshHistoryLocked()
		}
		return nil
	}
	var runs []RefreshRun
	if err := json.Unmarshal(data, &runs); err != nil {
		return nil
	}
	return runs
}

// GetAllSourcesStatus returns the latest run status for all declared sources.
func GetAllSourcesStatus() map[string]RefreshRun {
	sources, err := LoadRefreshSources()
	if err != nil {
		return nil
	}
	res := make(map[string]RefreshRun, len(sources))
	for name := range sources {
		res[name] = ReadSourceRun(name)
	}
	return res
}

// ── Orchestrator Implementation ──────────────────────────────────────────

// NewRefreshOrchestrator constructs a new RefreshOrchestrator instance.
func NewRefreshOrchestrator(cfg OrchestratorConfig) *RefreshOrchestrator {
	clock := cfg.Clock
	if clock == nil {
		clock = time.Now
	}
	loader := cfg.SourcesLoader
	if loader == nil {
		loader = LoadRefreshSources
	}
	interval := cfg.TickInterval
	if interval <= 0 {
		interval = 1 * time.Minute
	}
	cfg.TickInterval = interval

	execs := make(map[string]SourceExecutor)
	for k, v := range cfg.Executors {
		execs[k] = v
	}
	// Register default production executors if not overridden
	if _, ok := execs["gcp_catalog"]; !ok {
		execs["gcp_catalog"] = executeCatalogRefresh
	}
	if _, ok := execs["compute_availability"]; !ok {
		execs["compute_availability"] = executeComputeAvailabilityRefresh
	}
	if _, ok := execs["assumption_domains"]; !ok {
		execs["assumption_domains"] = executeAssumptionDomainsRefresh
	}
	if _, ok := execs["cud_coverage"]; !ok {
		execs["cud_coverage"] = executeCUDCoverageRefresh
	}

	return &RefreshOrchestrator{
		cfg:              cfg,
		triggerCh:        make(chan string, 10),
		clock:            clock,
		sourcesLoader:    loader,
		executors:        execs,
		onEscalationSent: cfg.OnEscalationSent,
		onRecoverySent:   cfg.OnRecoverySent,
	}
}

// checkDependencies verifies that every dependency has a complete run newer than the target source's last success.
func (o *RefreshOrchestrator) checkDependencies(source string, run RefreshRun, policy LifecyclePolicy, latest map[string]RefreshRun, sources map[string]RefreshSourceConfig) bool {
	if len(policy.DependsOn) == 0 {
		return true
	}
	var lastSuccess time.Time
	if run.FinishedAt != "" && (run.State == "success" || run.Complete) {
		lastSuccess, _ = time.Parse(time.RFC3339, run.FinishedAt)
	}
	for _, dep := range policy.DependsOn {
		depRun, ok := latest[dep]
		if !ok {
			return false
		}
		depCfg, hasCfg := sources[dep]
		isAcceptedPartial := hasCfg && depCfg.Lifecycle.TreatPartialAs == "accept" && depRun.State == "partial"
		if !depRun.Complete && depRun.State != "success" && !isAcceptedPartial {
			return false
		}
		if !lastSuccess.IsZero() {
			depFinish, err := time.Parse(time.RFC3339, depRun.FinishedAt)
			if err != nil || depFinish.Before(lastSuccess) {
				return false
			}
		}
	}
	return true
}

// TickOnce executes a single orchestration cycle across all sources.
func (o *RefreshOrchestrator) TickOnce(now time.Time) {
	o.mu.Lock()
	defer o.mu.Unlock()

	sources, err := o.sourcesLoader()
	if err != nil {
		slog.Error("refresh orchestrator failed to load sources", "err", err)
		return
	}

	latestRuns := make(map[string]RefreshRun, len(sources))
	for name := range sources {
		latestRuns[name] = ReadSourceRun(name)
	}

	// 1. Evaluate actions for each source
	type pendingRun struct {
		name      string
		action    Action
		nextState RefreshRun
		policy    LifecyclePolicy
	}
	var toRun *pendingRun

	for name, srcCfg := range sources {
		curr := latestRuns[name]
		depsOk := o.checkDependencies(name, curr, srcCfg.Lifecycle, latestRuns, sources)
		action, next := nextAction(curr, srcCfg.Lifecycle, now, depsOk)

		switch action {
		case ActionSkip:
			// No action needed
		case ActionWait:
			if next.State != curr.State || next.NextRetryAt != curr.NextRetryAt || next.ExitCode != curr.ExitCode {
				WriteSourceRun(name, next)
				latestRuns[name] = next
			}
		case ActionEscalate:
			WriteSourceRun(name, next)
			latestRuns[name] = next
			if !curr.Escalated {
				o.fireEscalation(name, next.Attempt, next.Reason)
			}
		case ActionRun:
			if toRun == nil && !o.isSourceRunning {
				toRun = &pendingRun{
					name:      name,
					action:    action,
					nextState: next,
					policy:    srcCfg.Lifecycle,
				}
			}
		}
	}

	// 2. Execute at most one source per tick under single-writer lock
	if toRun != nil {
		o.executeSource(toRun.name, toRun.nextState, toRun.policy, now)
	}
}

func (o *RefreshOrchestrator) executeSource(source string, runState RefreshRun, policy LifecyclePolicy, now time.Time) {
	o.isSourceRunning = true
	o.runningSource = source

	// Persist running state
	runState.State = "running"
	runState.StartedAt = now.Format(time.RFC3339)
	runState.FinishedAt = ""
	runState.NextRetryAt = ""
	WriteSourceRun(source, runState)

	executor, ok := o.executors[source]
	if !ok {
		slog.Warn("no executor registered for refresh source", "source", source)
		runState.State = "failed"
		runState.Reason = fmt.Sprintf("no executor registered for source %s", source)
		runState.FinishedAt = now.Format(time.RFC3339)
		WriteSourceRun(source, runState)
		o.isSourceRunning = false
		o.runningSource = ""
		return
	}

	timeoutSec := policy.TimeoutSeconds
	if timeoutSec <= 0 {
		timeoutSec = 1800 // default 30 min
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSec)*time.Second)
	defer cancel()

	trigger := runState.Trigger
	if trigger == "" {
		trigger = "auto"
	}
	actor := runState.Actor
	if actor == "" {
		actor = "orchestrator"
	}

	priorRun := ReadSourceRun(source)
	wasEscalated := priorRun.Escalated || priorRun.State == "escalated"
	result := executor(ctx, source, trigger, actor)
	finishedTime := o.clock().UTC()
	result.FinishedAt = finishedTime.Format(time.RFC3339)
	if result.DurationSeconds == 0 && result.StartedAt != "" {
		if st, err := time.Parse(time.RFC3339, result.StartedAt); err == nil {
			result.DurationSeconds = int(finishedTime.Sub(st).Seconds())
		}
	}

	isSuccess := result.ExitCode == 0 && (policy.TreatPartialAs != "retry" || result.Complete || result.State == "success")
	if isSuccess {
		if wasEscalated {
			o.fireRecovery(source)
		}
		if result.Complete {
			result.State = "success"
		} else {
			result.State = "partial"
		}
		result.Attempt = 0
		result.ConsecutiveFailures = 0
		result.Escalated = false
		result.NextRetryAt = ""
	} else {
		result.ConsecutiveFailures = priorRun.ConsecutiveFailures + 1
		result.Attempt = runState.Attempt
		if result.Attempt < 1 {
			result.Attempt = 1
		}
		if policy.MaxAttempts > 0 && result.Attempt >= policy.MaxAttempts {
			result.State = "escalated"
			result.Escalated = true
			result.NextRetryAt = ""
			o.fireEscalation(source, result.Attempt, result.Reason)
		} else {
			result.State = "retrying"
			backoffDur := ComputeBackoff(policy, result.Attempt)
			result.NextRetryAt = finishedTime.Add(backoffDur).Format(time.RFC3339)
		}
	}

	WriteSourceRun(source, result)
	o.isSourceRunning = false
	o.runningSource = ""
}

func (o *RefreshOrchestrator) fireEscalation(source string, attempts int, lastErr string) {
	slog.Warn("refresh escalated to admin", "source", source, "attempts", attempts, "last_error", lastErr)
	if o.onEscalationSent != nil {
		o.onEscalationSent(source, attempts, lastErr)
	}
	if o.cfg.SlackURL != nil {
		if url := o.cfg.SlackURL(); url != "" {
			_ = notify.PostRefreshEscalated(url, source, attempts, lastErr)
		}
	}
	if o.cfg.EmailCfg != nil {
		emailCfg := o.cfg.EmailCfg()
		if emailCfg.APIKey != "" {
			for _, admin := range o.cfg.AdminEmails {
				_ = notify.SendRefreshEscalated(emailCfg, admin, source, attempts, lastErr)
			}
		}
	}
}

func (o *RefreshOrchestrator) fireRecovery(source string) {
	slog.Info("refresh recovered", "source", source)
	if o.onRecoverySent != nil {
		o.onRecoverySent(source)
	}
	if o.cfg.SlackURL != nil {
		if url := o.cfg.SlackURL(); url != "" {
			_ = notify.PostRefreshRecovered(url, source)
		}
	}
	if o.cfg.EmailCfg != nil {
		emailCfg := o.cfg.EmailCfg()
		if emailCfg.APIKey != "" {
			for _, admin := range o.cfg.AdminEmails {
				_ = notify.SendRefreshRecovered(emailCfg, admin, source)
			}
		}
	}
}

// Start launches the orchestrator ticker loop.
func (o *RefreshOrchestrator) Start(ctx context.Context) {
	go func() {
		o.TickOnce(o.clock().UTC())
		ticker := time.NewTicker(o.cfg.TickInterval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				o.TickOnce(o.clock().UTC())
			case source := <-o.triggerCh:
				slog.Info("orchestrator received manual trigger", "source", source)
				o.TickOnce(o.clock().UTC())
			}
		}
	}()
}

// Trigger forces an immediate refresh cycle for a source.
func (o *RefreshOrchestrator) Trigger(source, actor string) (bool, string) {
	sources, err := o.sourcesLoader()
	if err != nil {
		return false, "failed to load sources: " + err.Error()
	}
	srcCfg, ok := sources[source]
	if !ok {
		return false, fmt.Sprintf("source %q is not registered in refresh_sources.json", source)
	}

	skillDir := skill.ResolveDir()
	if isImageBaked(skillDir) {
		reason := fmt.Sprintf("%s is baked into the container image; runtime refresh cannot persist", source)
		run := ReadSourceRun(source)
		run.State = "skipped"
		run.Reason = reason
		run.Trigger = "manual"
		run.Actor = actor
		run.FinishedAt = o.clock().UTC().Format(time.RFC3339)
		WriteSourceRun(source, run)
		return false, reason
	}

	o.mu.Lock()
	defer o.mu.Unlock()

	run := ReadSourceRun(source)
	wasEscalated := run.Escalated || run.State == "escalated"
	run.State = "idle" // Force due
	run.Escalated = wasEscalated
	run.NextRetryAt = ""
	run.Trigger = "manual"
	run.Actor = actor
	run.StartedAt = ""
	run.FinishedAt = ""
	run.Attempt = 1
	WriteSourceRun(source, run)

	// If no source is running right now, execute immediately
	if !o.isSourceRunning {
		go func() {
			o.mu.Lock()
			defer o.mu.Unlock()
			if !o.isSourceRunning {
				o.executeSource(source, run, srcCfg.Lifecycle, o.clock().UTC())
			}
		}()
	} else {
		select {
		case o.triggerCh <- source:
		default:
		}
	}

	return true, "refresh triggered"
}

// Pause pauses automatic scheduling for a source.
func (o *RefreshOrchestrator) Pause(source, actor string) error {
	sources, err := o.sourcesLoader()
	if err != nil {
		return err
	}
	if _, ok := sources[source]; !ok {
		return fmt.Errorf("source %q not found", source)
	}
	o.mu.Lock()
	defer o.mu.Unlock()

	run := ReadSourceRun(source)
	run.State = "paused"
	run.Actor = actor
	WriteSourceRun(source, run)
	return nil
}

// Resume unpauses a source, placing it back in scheduled evaluation.
func (o *RefreshOrchestrator) Resume(source, actor string) error {
	sources, err := o.sourcesLoader()
	if err != nil {
		return err
	}
	if _, ok := sources[source]; !ok {
		return fmt.Errorf("source %q not found", source)
	}
	o.mu.Lock()
	defer o.mu.Unlock()

	run := ReadSourceRun(source)
	if run.State == "paused" {
		run.State = "idle"
		run.Actor = actor
		run.NextRetryAt = ""
		WriteSourceRun(source, run)
	}
	return nil
}

// ── Global Convenience Functions ─────────────────────────────────────────

// StartRefreshOrchestrator initializes and starts the global refresh orchestrator.
func StartRefreshOrchestrator(ctx context.Context, cfgs ...OrchestratorConfig) {
	defaultOrchestratorMu.Lock()
	defer defaultOrchestratorMu.Unlock()

	var cfg OrchestratorConfig
	if len(cfgs) > 0 {
		cfg = cfgs[0]
	}
	defaultOrchestrator = NewRefreshOrchestrator(cfg)
	defaultOrchestrator.Start(ctx)
}

// TriggerRefresh triggers an immediate refresh for any declared source.
func TriggerRefresh(source, actor string) (bool, string) {
	defaultOrchestratorMu.Lock()
	orch := defaultOrchestrator
	defaultOrchestratorMu.Unlock()

	if orch != nil {
		return orch.Trigger(source, actor)
	}
	// Fallback if orchestrator goroutine has not initialized
	tmp := NewRefreshOrchestrator(OrchestratorConfig{})
	return tmp.Trigger(source, actor)
}

// PauseSource pauses a source.
func PauseSource(source, actor string) error {
	defaultOrchestratorMu.Lock()
	orch := defaultOrchestrator
	defaultOrchestratorMu.Unlock()

	if orch != nil {
		return orch.Pause(source, actor)
	}
	tmp := NewRefreshOrchestrator(OrchestratorConfig{})
	return tmp.Pause(source, actor)
}

// ResumeSource resumes a source.
func ResumeSource(source, actor string) error {
	defaultOrchestratorMu.Lock()
	orch := defaultOrchestrator
	defaultOrchestratorMu.Unlock()

	if orch != nil {
		return orch.Resume(source, actor)
	}
	tmp := NewRefreshOrchestrator(OrchestratorConfig{})
	return tmp.Resume(source, actor)
}

// ── Default Production Executors ─────────────────────────────────────────

type RefreshSummary struct {
	Source        string            `json:"source"`
	ExpectedCount int               `json:"expected_count"`
	FetchedCount  int               `json:"fetched_count"`
	CoveragePct   float64           `json:"coverage_pct"`
	Complete      bool              `json:"complete"`
	RecordCount   int               `json:"record_count"`
	RecordDelta   int               `json:"record_delta"`
	Anomalies     []string          `json:"anomalies"`
	ItemOutcomes  map[string]string `json:"item_outcomes"`
	State         string            `json:"state"`
	Reason        string            `json:"reason"`
}

func parseRefreshSummary(rawLines []string) *RefreshSummary {
	for i := len(rawLines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(rawLines[i])
		if strings.HasPrefix(line, "REFRESH_SUMMARY ") {
			payload := strings.TrimPrefix(line, "REFRESH_SUMMARY ")
			var summary RefreshSummary
			if err := json.Unmarshal([]byte(payload), &summary); err == nil && summary.Source != "" {
				return &summary
			}
		}
	}
	return nil
}

func executeCatalogRefresh(ctx context.Context, source, trigger, actor string) RefreshRun {
	skillDir := skill.ResolveDir()
	startTime := time.Now().UTC()

	script := filepath.Join(skillDir, "scripts", "catalog_manager.py")
	if _, err := os.Stat(script); err != nil {
		return RefreshRun{
			Source:     source,
			State:      "failed",
			Reason:     "catalog_manager.py not found",
			Trigger:    trigger,
			Actor:      actor,
			StartedAt:  startTime.Format(time.RFC3339),
			FinishedAt: startTime.Format(time.RFC3339),
			ExitCode:   1,
		}
	}

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	cmd := exec.CommandContext(ctx, python, script, "refresh", "--skill-dir", skillDir)
	cmd.Dir = skillDir

	var buf bytes.Buffer
	mw := io.MultiWriter(os.Stdout, &buf)
	cmd.Stdout = mw
	cmd.Stderr = mw

	err := cmd.Run()
	finishedTime := time.Now().UTC()
	durationSec := int(finishedTime.Sub(startTime).Seconds())

	rawLines := strings.Split(buf.String(), "\n")
	var logTail []string
	for _, l := range rawLines {
		l = strings.TrimRight(l, "\r")
		if strings.TrimSpace(l) != "" {
			logTail = append(logTail, l)
		}
	}
	if len(logTail) > 40 {
		logTail = logTail[len(logTail)-40:]
	}

	run := RefreshRun{
		Source:          source,
		State:           "success",
		StartedAt:       startTime.Format(time.RFC3339),
		FinishedAt:      finishedTime.Format(time.RFC3339),
		ExitCode:        0,
		Trigger:         trigger,
		Actor:           actor,
		LogTail:         logTail,
		DurationSeconds: durationSec,
		Complete:        true,
	}

	if summary := parseRefreshSummary(rawLines); summary != nil {
		run.ExpectedCount = summary.ExpectedCount
		run.FetchedCount = summary.FetchedCount
		run.CoveragePct = summary.CoveragePct
		run.Complete = summary.Complete
		run.RecordCount = summary.RecordCount
		run.RecordDelta = summary.RecordDelta
		run.Anomalies = summary.Anomalies
		run.ItemOutcomes = summary.ItemOutcomes
		if !run.Complete {
			run.State = "partial"
		}
	}

	if err != nil {
		exitCode := 1
		reason := err.Error()
		if ctx.Err() == context.DeadlineExceeded {
			reason = "timed out"
		} else if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
			reason = fmt.Sprintf("Script stopped with exit code %d.", exitCode)
			if msg := lastMeaningfulLine(logTail); msg != "" {
				reason += " Last message: " + msg
			}
		}
		run.ExitCode = exitCode
		run.Reason = reason
		run.State = "failed"
	}

	return run
}

func executeComputeAvailabilityRefresh(ctx context.Context, source, trigger, actor string) RefreshRun {
	skillDir := skill.ResolveDir()
	startTime := time.Now().UTC()

	project := os.Getenv("GOOGLE_CLOUD_AVAILABILITY_PROJECT")
	if project == "" {
		return RefreshRun{
			Source:     source,
			State:      "skipped",
			Reason:     "GOOGLE_CLOUD_AVAILABILITY_PROJECT not set",
			Trigger:    trigger,
			Actor:      actor,
			StartedAt:  startTime.Format(time.RFC3339),
			FinishedAt: startTime.Format(time.RFC3339),
			ExitCode:   0,
		}
	}

	script := filepath.Join(skillDir, "scripts", "verify_compute_availability.py")
	if _, err := os.Stat(script); err != nil {
		return RefreshRun{
			Source:     source,
			State:      "failed",
			Reason:     "verify_compute_availability.py not found",
			Trigger:    trigger,
			Actor:      actor,
			StartedAt:  startTime.Format(time.RFC3339),
			FinishedAt: startTime.Format(time.RFC3339),
			ExitCode:   1,
		}
	}

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	cmd := exec.CommandContext(ctx, python, script, "--project", project)
	cmd.Dir = skillDir

	var buf bytes.Buffer
	mw := io.MultiWriter(os.Stdout, &buf)
	cmd.Stdout = mw
	cmd.Stderr = mw

	err := cmd.Run()
	finishedTime := time.Now().UTC()
	durationSec := int(finishedTime.Sub(startTime).Seconds())

	rawLines := strings.Split(buf.String(), "\n")
	var logTail []string
	for _, l := range rawLines {
		l = strings.TrimRight(l, "\r")
		if strings.TrimSpace(l) != "" {
			logTail = append(logTail, l)
		}
	}
	if len(logTail) > 40 {
		logTail = logTail[len(logTail)-40:]
	}

	run := RefreshRun{
		Source:          source,
		State:           "success",
		StartedAt:       startTime.Format(time.RFC3339),
		FinishedAt:      finishedTime.Format(time.RFC3339),
		ExitCode:        0,
		Trigger:         trigger,
		Actor:           actor,
		LogTail:         logTail,
		DurationSeconds: durationSec,
		Complete:        true,
	}

	if summary := parseRefreshSummary(rawLines); summary != nil {
		run.ExpectedCount = summary.ExpectedCount
		run.FetchedCount = summary.FetchedCount
		run.CoveragePct = summary.CoveragePct
		run.Complete = summary.Complete
		run.RecordCount = summary.RecordCount
		run.RecordDelta = summary.RecordDelta
		run.Anomalies = summary.Anomalies
		run.ItemOutcomes = summary.ItemOutcomes
		if !run.Complete {
			run.State = "partial"
		}
	}

	if err != nil {
		exitCode := 1
		reason := err.Error()
		if ctx.Err() == context.DeadlineExceeded {
			reason = "timed out"
		} else if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
			reason = fmt.Sprintf("Script stopped with exit code %d.", exitCode)
		}
		run.ExitCode = exitCode
		run.Reason = reason
		run.State = "failed"
	}

	return run
}

func executeAssumptionDomainsRefresh(ctx context.Context, source, trigger, actor string) RefreshRun {
	skillDir := skill.ResolveDir()
	startTime := time.Now().UTC()

	script := filepath.Join(skillDir, "scripts", "refresh_assumption_domains.py")
	if _, err := os.Stat(script); err != nil {
		return RefreshRun{
			Source:     source,
			State:      "skipped",
			Reason:     "refresh_assumption_domains.py not present",
			Trigger:    trigger,
			Actor:      actor,
			StartedAt:  startTime.Format(time.RFC3339),
			FinishedAt: startTime.Format(time.RFC3339),
			ExitCode:   0,
		}
	}

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	cmd := exec.CommandContext(ctx, python, script)
	cmd.Dir = skillDir

	var buf bytes.Buffer
	mw := io.MultiWriter(os.Stdout, &buf)
	cmd.Stdout = mw
	cmd.Stderr = mw

	err := cmd.Run()
	finishedTime := time.Now().UTC()
	durationSec := int(finishedTime.Sub(startTime).Seconds())

	rawLines := strings.Split(buf.String(), "\n")
	var logTail []string
	for _, l := range rawLines {
		l = strings.TrimRight(l, "\r")
		if strings.TrimSpace(l) != "" {
			logTail = append(logTail, l)
		}
	}
	if len(logTail) > 40 {
		logTail = logTail[len(logTail)-40:]
	}

	run := RefreshRun{
		Source:          source,
		State:           "success",
		StartedAt:       startTime.Format(time.RFC3339),
		FinishedAt:      finishedTime.Format(time.RFC3339),
		ExitCode:        0,
		Trigger:         trigger,
		Actor:           actor,
		LogTail:         logTail,
		DurationSeconds: durationSec,
		Complete:        true,
	}

	if summary := parseRefreshSummary(rawLines); summary != nil {
		run.ExpectedCount = summary.ExpectedCount
		run.FetchedCount = summary.FetchedCount
		run.CoveragePct = summary.CoveragePct
		run.Complete = summary.Complete
		run.RecordCount = summary.RecordCount
		run.RecordDelta = summary.RecordDelta
		run.Anomalies = summary.Anomalies
		run.ItemOutcomes = summary.ItemOutcomes
		if !run.Complete {
			run.State = "partial"
		}
	}

	if err != nil {
		exitCode := 1
		reason := err.Error()
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		}
		run.ExitCode = exitCode
		run.Reason = reason
		run.State = "failed"
	}

	return run
}

func executeCUDCoverageRefresh(ctx context.Context, source, trigger, actor string) RefreshRun {
	skillDir := skill.ResolveDir()
	startTime := time.Now().UTC()

	script := filepath.Join(skillDir, "scripts", "check_cud_coverage.py")
	if _, err := os.Stat(script); err != nil {
		return RefreshRun{
			Source:     source,
			State:      "failed",
			Reason:     "check_cud_coverage.py not present",
			Trigger:    trigger,
			Actor:      actor,
			StartedAt:  startTime.Format(time.RFC3339),
			FinishedAt: startTime.Format(time.RFC3339),
			ExitCode:   1,
		}
	}

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	cmd := exec.CommandContext(ctx, python, script, "--json")
	cmd.Dir = skillDir

	var buf bytes.Buffer
	mw := io.MultiWriter(os.Stdout, &buf)
	cmd.Stdout = mw
	cmd.Stderr = mw

	err := cmd.Run()
	finishedTime := time.Now().UTC()
	durationSec := int(finishedTime.Sub(startTime).Seconds())

	rawLines := strings.Split(buf.String(), "\n")
	var logTail []string
	for _, l := range rawLines {
		l = strings.TrimRight(l, "\r")
		if strings.TrimSpace(l) != "" {
			logTail = append(logTail, l)
		}
	}
	if len(logTail) > 40 {
		logTail = logTail[len(logTail)-40:]
	}

	run := RefreshRun{
		Source:          source,
		State:           "success",
		StartedAt:       startTime.Format(time.RFC3339),
		FinishedAt:      finishedTime.Format(time.RFC3339),
		ExitCode:        0,
		Trigger:         trigger,
		Actor:           actor,
		LogTail:         logTail,
		DurationSeconds: durationSec,
		Complete:        true,
	}

	if summary := parseRefreshSummary(rawLines); summary != nil {
		run.ExpectedCount = summary.ExpectedCount
		run.FetchedCount = summary.FetchedCount
		run.CoveragePct = summary.CoveragePct
		run.Complete = summary.Complete
		run.RecordCount = summary.RecordCount
		run.RecordDelta = summary.RecordDelta
		run.Anomalies = summary.Anomalies
		run.ItemOutcomes = summary.ItemOutcomes
		if !run.Complete {
			run.State = "partial"
		}
	} else {
		var out struct {
			Source        string            `json:"source"`
			State         string            `json:"state"`
			Reason        string            `json:"reason"`
			ExpectedCount int               `json:"expected_count"`
			FetchedCount  int               `json:"fetched_count"`
			CoveragePct   float64           `json:"coverage_pct"`
			Complete      bool              `json:"complete"`
			RecordCount   int               `json:"record_count"`
			Anomalies     []string          `json:"anomalies"`
			ItemOutcomes  map[string]string `json:"item_outcomes"`
			ExitCode      int               `json:"exit_code"`
		}
		if jsonErr := json.Unmarshal(buf.Bytes(), &out); jsonErr == nil && out.Source != "" {
			run.ExpectedCount = out.ExpectedCount
			run.FetchedCount = out.FetchedCount
			run.CoveragePct = out.CoveragePct
			run.Complete = out.Complete
			run.RecordCount = out.RecordCount
			run.Anomalies = out.Anomalies
			run.ItemOutcomes = out.ItemOutcomes
			if !run.Complete {
				run.State = "partial"
			}
		}
	}

	if err != nil {
		exitCode := 1
		reason := err.Error()
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		}
		run.ExitCode = exitCode
		run.Reason = reason
		run.State = "failed"
	}

	return run
}
