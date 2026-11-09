package jobs

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func setupTestSkillDir(t *testing.T) (string, func()) {
	tmpDir, err := os.MkdirTemp("", "orchestrator_test_*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	dataDir := filepath.Join(tmpDir, "data")
	refreshRuns := filepath.Join(dataDir, "refresh_runs")
	if err := os.MkdirAll(refreshRuns, 0755); err != nil {
		t.Fatalf("failed to create refresh_runs dir: %v", err)
	}

	oldSkillDir := os.Getenv("SKILL_DIR")
	os.Setenv("SKILL_DIR", tmpDir)

	cleanup := func() {
		if oldSkillDir == "" {
			os.Unsetenv("SKILL_DIR")
		} else {
			os.Setenv("SKILL_DIR", oldSkillDir)
		}
		_ = os.RemoveAll(tmpDir)
	}
	return tmpDir, cleanup
}

func TestRefreshOrchestrator_FullLifecycle(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time {
		return currTime
	}

	sources := map[string]RefreshSourceConfig{
		"test_source": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:      24,
				MaxAttempts:        3,
				BackoffBaseSeconds: 100,
				BackoffFactor:      2.0,
				BackoffCapSeconds:  3600,
				TimeoutSeconds:     60,
				TreatPartialAs:     "retry",
			},
		},
	}

	var shouldFail atomic.Bool
	shouldFail.Store(true)

	var escalationCount atomic.Int32
	var recoveryCount atomic.Int32

	executors := map[string]SourceExecutor{
		"test_source": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			if shouldFail.Load() {
				return RefreshRun{
					Source:   source,
					State:    "failed",
					ExitCode: 1,
					Reason:   "simulated transient failure",
				}
			}
			return RefreshRun{
				Source:   source,
				State:    "success",
				ExitCode: 0,
				Complete: true,
			}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
		OnEscalationSent: func(source string, attempts int, lastErr string) {
			escalationCount.Add(1)
		},
		OnRecoverySent: func(source string) {
			recoveryCount.Add(1)
		},
	})

	// Tick 1: Initial run (attempt 1) -> fails
	orch.TickOnce(currTime)

	run := ReadSourceRun("test_source")
	if run.State != "retrying" {
		t.Fatalf("expected state 'retrying' on attempt 1, got %q", run.State)
	}
	if run.Attempt != 1 {
		t.Fatalf("expected attempt 1, got %d", run.Attempt)
	}
	expectedRetry1 := currTime.Add(100 * time.Second).Format(time.RFC3339)
	if run.NextRetryAt != expectedRetry1 {
		t.Fatalf("expected next retry at %v, got %v", expectedRetry1, run.NextRetryAt)
	}
	if escalationCount.Load() != 0 {
		t.Fatalf("expected 0 escalations, got %d", escalationCount.Load())
	}

	// Tick 2: 50s later -> retry time not reached, remains retrying
	currTime = currTime.Add(50 * time.Second)
	orch.TickOnce(currTime)
	run = ReadSourceRun("test_source")
	if run.State != "retrying" || run.Attempt != 1 {
		t.Fatalf("should still be retrying attempt 1, got state=%q attempt=%d", run.State, run.Attempt)
	}

	// Tick 3: 100s reached -> attempt 2 runs -> fails
	currTime = currTime.Add(50 * time.Second) // total +100s
	orch.TickOnce(currTime)
	run = ReadSourceRun("test_source")
	if run.State != "retrying" || run.Attempt != 2 {
		t.Fatalf("expected retrying attempt 2, got state=%q attempt=%d", run.State, run.Attempt)
	}
	// Backoff for attempt 2 is 100 * 2^1 = 200s
	expectedRetry2 := currTime.Add(200 * time.Second).Format(time.RFC3339)
	if run.NextRetryAt != expectedRetry2 {
		t.Fatalf("expected next retry at %v, got %v", expectedRetry2, run.NextRetryAt)
	}
	if escalationCount.Load() != 0 {
		t.Fatalf("expected 0 escalations, got %d", escalationCount.Load())
	}

	// Tick 4: 200s reached -> attempt 3 (max_attempts = 3) runs -> fails -> ESCALATES
	currTime = currTime.Add(200 * time.Second)
	orch.TickOnce(currTime)
	run = ReadSourceRun("test_source")
	if run.State != "escalated" || !run.Escalated {
		t.Fatalf("expected state 'escalated', got state=%q, escalated=%v", run.State, run.Escalated)
	}
	if escalationCount.Load() != 1 {
		t.Fatalf("expected exactly 1 escalation notification, got %d", escalationCount.Load())
	}

	// Tick 5: Next tick while escalated -> no extra escalation notifications (no spam)
	currTime = currTime.Add(60 * time.Second)
	orch.TickOnce(currTime)
	if escalationCount.Load() != 1 {
		t.Fatalf("expected still exactly 1 escalation notification, got %d", escalationCount.Load())
	}

	// Step 6: Fix underlying issue and manual retry
	shouldFail.Store(false)
	triggered, reason := orch.Trigger("test_source", "admin@example.com")
	if !triggered {
		t.Fatalf("trigger failed: %s", reason)
	}

	// Let the trigger execution complete
	time.Sleep(50 * time.Millisecond)

	run = ReadSourceRun("test_source")
	if run.State != "success" {
		t.Fatalf("expected state 'success' after manual trigger, got %q", run.State)
	}
	if run.Attempt != 0 {
		t.Fatalf("expected attempt reset to 0, got %d", run.Attempt)
	}
	if run.Escalated {
		t.Fatalf("expected escalated=false after recovery")
	}
	if recoveryCount.Load() != 1 {
		t.Fatalf("expected exactly 1 recovery notification, got %d", recoveryCount.Load())
	}
}

func TestRefreshOrchestrator_DependencyOrdering(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"upstream_catalog": {
			Lifecycle: LifecyclePolicy{
				IntervalHours: 24,
				MaxAttempts:   3,
			},
		},
		"downstream_domains": {
			Lifecycle: LifecyclePolicy{
				IntervalHours: 24,
				MaxAttempts:   3,
				DependsOn:     []string{"upstream_catalog"},
			},
		},
	}

	catalogOk := false
	domainsRan := false

	executors := map[string]SourceExecutor{
		"upstream_catalog": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			if !catalogOk {
				return RefreshRun{Source: source, State: "failed", ExitCode: 1, Reason: "catalog failing"}
			}
			return RefreshRun{Source: source, State: "success", ExitCode: 0, Complete: true}
		},
		"downstream_domains": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			domainsRan = true
			return RefreshRun{Source: source, State: "success", ExitCode: 0, Complete: true}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
	})

	// Tick 1: upstream_catalog runs and fails
	orch.TickOnce(currTime)
	catRun := ReadSourceRun("upstream_catalog")
	if catRun.State != "retrying" && catRun.State != "failed" {
		t.Fatalf("expected catalog failed/retrying, got %q", catRun.State)
	}

	// downstream_domains must NOT have run because its dependency failed
	domRun := ReadSourceRun("downstream_domains")
	if domainsRan || domRun.State == "running" || domRun.State == "success" {
		t.Fatalf("downstream_domains ran while upstream was failing! State: %q", domRun.State)
	}

	// Now fix upstream_catalog
	catalogOk = true
	// Force catalog retry
	orch.Trigger("upstream_catalog", "admin")
	time.Sleep(50 * time.Millisecond)

	catRun = ReadSourceRun("upstream_catalog")
	if catRun.State != "success" {
		t.Fatalf("expected upstream_catalog success, got %q", catRun.State)
	}

	// Advance clock slightly and tick: downstream_domains should now run
	currTime = currTime.Add(1 * time.Minute)
	orch.TickOnce(currTime)

	domRun = ReadSourceRun("downstream_domains")
	if domRun.State != "success" {
		t.Fatalf("expected downstream_domains to succeed after dependency resolved, got %q", domRun.State)
	}
	if !domainsRan {
		t.Fatalf("downstream_domains executor was not invoked")
	}
}

func TestRefreshOrchestrator_TimeoutAndStuckRun(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"slow_source": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:      24,
				MaxAttempts:        3,
				BackoffBaseSeconds: 50,
				BackoffFactor:      1.0,
				TimeoutSeconds:     1, // 1 second timeout
			},
		},
	}

	executors := map[string]SourceExecutor{
		"slow_source": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			// Simulate a hang that respects context cancellation
			<-ctx.Done()
			return RefreshRun{
				Source:   source,
				State:    "failed",
				ExitCode: 124,
				Reason:   "timed out",
			}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
	})

	// Run tick — executor will timeout after 1s
	orch.TickOnce(currTime)

	run := ReadSourceRun("slow_source")
	if run.State != "retrying" {
		t.Fatalf("expected state 'retrying' after timeout failure, got %q", run.State)
	}
	if run.ExitCode != 124 {
		t.Errorf("expected exit code 124 for timeout, got %d", run.ExitCode)
	}

	// Test stuck run recovery:
	// Suppose a run was recorded as "running" 3 hours ago (e.g. process crashed)
	stuckTime := currTime.Add(-3 * time.Hour)
	WriteSourceRun("stuck_source", RefreshRun{
		Source:    "stuck_source",
		State:     "running",
		StartedAt: stuckTime.Format(time.RFC3339),
		Attempt:   1,
	})

	sources["stuck_source"] = RefreshSourceConfig{
		Lifecycle: LifecyclePolicy{
			TimeoutSeconds:     300, // 5 min
			MaxAttempts:        3,
			BackoffBaseSeconds: 60,
		},
	}

	orch.TickOnce(currTime)
	recoveredRun := ReadSourceRun("stuck_source")
	if recoveredRun.State != "retrying" {
		t.Fatalf("expected stuck run to be reset to retrying, got %q", recoveredRun.State)
	}
	if recoveredRun.ConsecutiveFailures < 1 {
		t.Errorf("expected consecutive failures incremented")
	}
}

func TestRefreshOrchestrator_PauseResume(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"pausable": {
			Lifecycle: LifecyclePolicy{
				IntervalHours: 24,
				MaxAttempts:   3,
			},
		},
	}

	ran := false
	executors := map[string]SourceExecutor{
		"pausable": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			ran = true
			return RefreshRun{Source: source, State: "success", ExitCode: 0, Complete: true}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
	})

	// 1. Pause
	if err := orch.Pause("pausable", "admin@example.com"); err != nil {
		t.Fatalf("pause failed: %v", err)
	}
	run := ReadSourceRun("pausable")
	if run.State != "paused" {
		t.Fatalf("expected state 'paused', got %q", run.State)
	}

	// 2. Tick while paused -> should not run
	orch.TickOnce(currTime)
	if ran {
		t.Fatalf("paused source was executed!")
	}

	// 3. Resume
	if err := orch.Resume("pausable", "admin@example.com"); err != nil {
		t.Fatalf("resume failed: %v", err)
	}
	run = ReadSourceRun("pausable")
	if run.State != "idle" {
		t.Fatalf("expected state 'idle' after resume, got %q", run.State)
	}

	// 4. Tick after resume -> executes
	orch.TickOnce(currTime)
	if !ran {
		t.Fatalf("resumed source was not executed on tick")
	}
	run = ReadSourceRun("pausable")
	if run.State != "success" {
		t.Fatalf("expected state 'success', got %q", run.State)
	}
}

func TestRefreshOrchestrator_FailsTwiceThenRecoversWithoutEscalation(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"transient_source": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:      24,
				MaxAttempts:        3,
				BackoffBaseSeconds: 60,
				BackoffFactor:      2.0,
				BackoffCapSeconds:  3600,
				TimeoutSeconds:     30,
			},
		},
	}

	failuresRemaining := 2
	var escalationSent atomic.Bool

	executors := map[string]SourceExecutor{
		"transient_source": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			if failuresRemaining > 0 {
				failuresRemaining--
				return RefreshRun{
					Source:   source,
					State:    "failed",
					ExitCode: 1,
					Reason:   "temporary network blip",
				}
			}
			return RefreshRun{
				Source:   source,
				State:    "success",
				ExitCode: 0,
				Complete: true,
			}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
		OnEscalationSent: func(source string, attempts int, lastErr string) {
			escalationSent.Store(true)
		},
	})

	// Run 1: Failure 1
	orch.TickOnce(currTime)
	r1 := ReadSourceRun("transient_source")
	if r1.State != "retrying" || r1.Attempt != 1 {
		t.Fatalf("expected attempt 1 retrying, got state=%q attempt=%d", r1.State, r1.Attempt)
	}

	// Advance 60s (backoff 60 * 2^0 = 60s)
	currTime = currTime.Add(60 * time.Second)

	// Run 2: Failure 2
	orch.TickOnce(currTime)
	r2 := ReadSourceRun("transient_source")
	if r2.State != "retrying" || r2.Attempt != 2 {
		t.Fatalf("expected attempt 2 retrying, got state=%q attempt=%d", r2.State, r2.Attempt)
	}

	// Advance 120s (backoff 60 * 2^1 = 120s)
	currTime = currTime.Add(120 * time.Second)

	// Run 3: Success!
	orch.TickOnce(currTime)
	r3 := ReadSourceRun("transient_source")
	if r3.State != "success" || r3.Attempt != 0 {
		t.Fatalf("expected success with attempt reset to 0, got state=%q attempt=%d", r3.State, r3.Attempt)
	}

	if escalationSent.Load() {
		t.Fatalf("escalation was sent for transient failures under max_attempts!")
	}
}

func TestRefreshCompletenessTracking(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	// 1. Test parseRefreshSummary
	sampleLog := []string{
		"Fetching services...",
		"REFRESH_SUMMARY {\"source\":\"gcp_catalog\",\"expected_count\":109,\"fetched_count\":105,\"coverage_pct\":96.33,\"complete\":false,\"record_count\":85000,\"record_delta\":-500,\"anomalies\":[\"Firebase: DEGRADED\"],\"item_outcomes\":{\"firebase\":\"DEGRADED\"}}",
		"Completed with warning",
	}
	s := parseRefreshSummary(sampleLog)
	if s == nil {
		t.Fatalf("parseRefreshSummary failed to parse valid summary")
	}
	if s.Source != "gcp_catalog" || s.ExpectedCount != 109 || s.FetchedCount != 105 || s.CoveragePct != 96.33 {
		t.Fatalf("unexpected summary parsed: %+v", s)
	}
	if s.Complete || len(s.Anomalies) != 1 || s.Anomalies[0] != "Firebase: DEGRADED" {
		t.Fatalf("unexpected completeness or anomalies: %+v", s)
	}

	// 2. Test execution resulting in partial state
	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"partial_source": {
			CompletenessThreshold: 100.0,
			Lifecycle: LifecyclePolicy{
				IntervalHours:  24,
				TreatPartialAs: "accept",
			},
		},
	}

	executors := map[string]SourceExecutor{
		"partial_source": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			return RefreshRun{
				Source:        source,
				State:         "partial",
				ExitCode:      0,
				ExpectedCount: 109,
				FetchedCount:  100,
				CoveragePct:   91.74,
				Complete:      false,
				RecordCount:   80000,
				Anomalies:     []string{"Compute: MISSING"},
			}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
	})

	orch.TickOnce(currTime)
	run := ReadSourceRun("partial_source")
	if run.State != "partial" {
		t.Fatalf("expected run state 'partial', got %q", run.State)
	}
	if run.Complete {
		t.Fatalf("expected run.Complete to be false")
	}
	if run.ExpectedCount != 109 || run.FetchedCount != 100 {
		t.Fatalf("expected counts 109/100, got %d/%d", run.ExpectedCount, run.FetchedCount)
	}
	if len(run.Anomalies) != 1 {
		t.Fatalf("expected 1 anomaly, got %v", run.Anomalies)
	}
}

func TestRefreshNotificationBoundary_EscalationAndRecovery(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	var slackEscalations atomic.Int32
	var slackRecoveries atomic.Int32

	slackServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		text := string(body)
		if strings.Contains(text, "Refresh escalated") {
			slackEscalations.Add(1)
		}
		if strings.Contains(text, "Refresh recovered") {
			slackRecoveries.Add(1)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer slackServer.Close()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"failing_source": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:      24,
				MaxAttempts:        2,
				BackoffBaseSeconds: 60,
				BackoffFactor:      2.0,
				BackoffCapSeconds:  300,
			},
		},
	}

	var shouldFail atomic.Bool
	shouldFail.Store(true)

	executors := map[string]SourceExecutor{
		"failing_source": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			if shouldFail.Load() {
				return RefreshRun{
					Source:   source,
					State:    "failed",
					ExitCode: 1,
					Reason:   "network error",
				}
			}
			return RefreshRun{
				Source:   source,
				State:    "success",
				ExitCode: 0,
				Complete: true,
			}
		},
	}

	// Real-shaped config with live Slack webhook pointing at httptest server,
	// and NO OnEscalationSent/OnRecoverySent test hooks.
	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
		SlackURL: func() string {
			return slackServer.URL
		},
	})

	// Run 1: Attempt 1 fails -> retrying
	orch.TickOnce(currTime)
	if slackEscalations.Load() != 0 {
		t.Fatalf("unexpected early escalation")
	}

	// Advance time past backoff: 60s
	currTime = currTime.Add(65 * time.Second)

	// Run 2: Attempt 2 fails -> reaches MaxAttempts (2) -> ESCALATED!
	orch.TickOnce(currTime)
	run := ReadSourceRun("failing_source")
	if run.State != "escalated" || !run.Escalated {
		t.Fatalf("expected state escalated, got %q (escalated=%v)", run.State, run.Escalated)
	}
	if slackEscalations.Load() != 1 {
		t.Fatalf("expected exactly 1 Slack escalation, got %d", slackEscalations.Load())
	}

	// Now fix the failure
	shouldFail.Store(false)

	// Trigger manual recovery
	ok, reason := orch.Trigger("failing_source", "admin")
	if !ok {
		t.Fatalf("trigger failed: %s", reason)
	}

	// Let the trigger goroutine finish
	time.Sleep(50 * time.Millisecond)

	run = ReadSourceRun("failing_source")
	if run.State != "success" {
		t.Fatalf("expected recovered success, got %q", run.State)
	}
	if slackRecoveries.Load() != 1 {
		t.Fatalf("expected exactly 1 Slack recovery notification, got %d", slackRecoveries.Load())
	}
}

func TestAcceptedPartialDependencyUnblocksDownstream(t *testing.T) {
	_, cleanup := setupTestSkillDir(t)
	defer cleanup()

	currTime := time.Date(2026, 11, 6, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return currTime }

	sources := map[string]RefreshSourceConfig{
		"comp_avail": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:  24,
				TreatPartialAs: "accept",
			},
		},
		"retry_dep": {
			Lifecycle: LifecyclePolicy{
				IntervalHours:  24,
				TreatPartialAs: "retry",
			},
		},
		"downstream_of_comp": {
			Lifecycle: LifecyclePolicy{
				IntervalHours: 24,
				DependsOn:     []string{"comp_avail"},
			},
		},
		"downstream_of_retry": {
			Lifecycle: LifecyclePolicy{
				IntervalHours: 24,
				DependsOn:     []string{"retry_dep"},
			},
		},
	}

	downstreamCompRan := false
	downstreamRetryRan := false

	executors := map[string]SourceExecutor{
		"comp_avail": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			return RefreshRun{
				Source:        source,
				State:         "partial",
				Complete:      false,
				ExitCode:      0,
				ExpectedCount: 40,
				FetchedCount:  36,
				CoveragePct:   90.0,
			}
		},
		"retry_dep": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			return RefreshRun{
				Source:        source,
				State:         "partial",
				Complete:      false,
				ExitCode:      0,
				ExpectedCount: 10,
				FetchedCount:  8,
				CoveragePct:   80.0,
			}
		},
		"downstream_of_comp": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			downstreamCompRan = true
			return RefreshRun{Source: source, State: "success", ExitCode: 0, Complete: true}
		},
		"downstream_of_retry": func(ctx context.Context, source, trigger, actor string) RefreshRun {
			downstreamRetryRan = true
			return RefreshRun{Source: source, State: "success", ExitCode: 0, Complete: true}
		},
	}

	orch := NewRefreshOrchestrator(OrchestratorConfig{
		Clock: clock,
		SourcesLoader: func() (map[string]RefreshSourceConfig, error) {
			return sources, nil
		},
		Executors: executors,
	})

	// Tick 1: Upstreams run. comp_avail becomes accepted-partial (State="partial").
	// retry_dep has TreatPartialAs="retry" -> fails/retrying.
	orch.TickOnce(currTime)

	compRun := ReadSourceRun("comp_avail")
	if compRun.State != "partial" {
		t.Fatalf("expected comp_avail to be partial, got %q", compRun.State)
	}

	// Advance clock slightly and Tick:
	// downstream_of_comp should run because comp_avail is an accepted-partial.
	// downstream_of_retry must NOT run because retry_dep has treat_partial_as: "retry".
	for i := 0; i < 5; i++ {
		currTime = currTime.Add(1 * time.Minute)
		orch.TickOnce(currTime)
		if downstreamCompRan {
			break
		}
	}

	if !downstreamCompRan {
		t.Errorf("downstream_of_comp should have run because upstream comp_avail was an accepted partial")
	}
	if downstreamRetryRan {
		t.Errorf("downstream_of_retry ran even though upstream retry_dep has treat_partial_as: retry!")
	}
}


