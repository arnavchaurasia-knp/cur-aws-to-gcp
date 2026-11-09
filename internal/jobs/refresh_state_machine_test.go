package jobs

import (
	"testing"
	"time"
)

func TestComputeBackoff(t *testing.T) {
	policy := LifecyclePolicy{
		BackoffBaseSeconds: 300,
		BackoffFactor:      3.0,
		BackoffCapSeconds:  21600,
	}

	tests := []struct {
		attempt int
		wantSec float64
	}{
		{attempt: 0, wantSec: 300},   // 300 * 3^0
		{attempt: 1, wantSec: 300},   // 300 * 3^0 = 300
		{attempt: 2, wantSec: 900},   // 300 * 3^1 = 900
		{attempt: 3, wantSec: 2700},  // 300 * 3^2 = 2700
		{attempt: 4, wantSec: 8100},  // 300 * 3^3 = 8100
		{attempt: 5, wantSec: 21600}, // 300 * 3^4 = 24300 -> capped at 21600
	}

	for _, tt := range tests {
		got := ComputeBackoff(policy, tt.attempt)
		want := time.Duration(tt.wantSec * float64(time.Second))
		if got != want {
			t.Errorf("ComputeBackoff(attempt=%d) = %v, want %v", tt.attempt, got, want)
		}
	}
}

func TestNextAction_TableTests(t *testing.T) {
	baseTime, err := time.Parse(time.RFC3339, "2026-11-06T12:00:00Z")
	if err != nil {
		t.Fatalf("failed to parse base time: %v", err)
	}

	policy := LifecyclePolicy{
		IntervalHours:      24,
		MaxAttempts:        3,
		BackoffBaseSeconds: 300,
		BackoffFactor:      2.0,
		BackoffCapSeconds:  3600,
		TimeoutSeconds:     600,
		TreatPartialAs:     "retry",
	}

	t.Run("paused -> skip", func(t *testing.T) {
		run := RefreshRun{
			State: "paused",
		}
		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionSkip {
			t.Errorf("expected ActionSkip, got %v", action)
		}
		if next.State != "paused" {
			t.Errorf("expected state paused, got %v", next.State)
		}
	})

	t.Run("deps incomplete -> wait", func(t *testing.T) {
		run := RefreshRun{
			State: "idle",
		}
		action, _ := nextAction(run, policy, baseTime, false)
		if action != ActionWait {
			t.Errorf("expected ActionWait when deps incomplete, got %v", action)
		}
	})

	t.Run("idle -> run when deps complete", func(t *testing.T) {
		run := RefreshRun{
			State: "idle",
		}
		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionRun {
			t.Errorf("expected ActionRun, got %v", action)
		}
		if next.State != "running" || next.Attempt != 1 {
			t.Errorf("expected running attempt 1, got %+v", next)
		}
	})

	t.Run("success -> schedules next & resets attempts", func(t *testing.T) {
		recentFinish := baseTime.Add(-1 * time.Hour).Format(time.RFC3339)
		run := RefreshRun{
			State:      "success",
			FinishedAt: recentFinish,
			Attempt:    2,
		}
		// 1 hour after finish: interval 24h not reached -> wait, reset attempts
		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionWait {
			t.Errorf("expected ActionWait, got %v", action)
		}
		if next.Attempt != 0 {
			t.Errorf("expected attempt 0, got %d", next.Attempt)
		}

		// 25 hours after finish: interval reached -> run
		futureTime := baseTime.Add(25 * time.Hour)
		action2, next2 := nextAction(run, policy, futureTime, true)
		if action2 != ActionRun {
			t.Errorf("expected ActionRun after interval, got %v", action2)
		}
		if next2.State != "running" || next2.Attempt != 1 {
			t.Errorf("expected state running attempt 1, got %+v", next2)
		}
	})

	t.Run("failure under max -> retrying with correct next_retry_at (verify backoff)", func(t *testing.T) {
		run := RefreshRun{
			State:      "failed",
			Attempt:    1,
			FinishedAt: baseTime.Format(time.RFC3339),
		}
		// Attempt 1 -> backoff is 300 * 2^0 = 300s
		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionWait {
			t.Errorf("expected ActionWait, got %v", action)
		}
		if next.State != "retrying" {
			t.Errorf("expected state retrying, got %v", next.State)
		}
		expectedRetry := baseTime.Add(300 * time.Second).Format(time.RFC3339)
		if next.NextRetryAt != expectedRetry {
			t.Errorf("expected NextRetryAt %v, got %v", expectedRetry, next.NextRetryAt)
		}
	})

	t.Run("at max -> escalate", func(t *testing.T) {
		run := RefreshRun{
			State:      "failed",
			Attempt:    3,
			FinishedAt: baseTime.Format(time.RFC3339),
		}
		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionEscalate {
			t.Errorf("expected ActionEscalate, got %v", action)
		}
		if next.State != "escalated" || !next.Escalated {
			t.Errorf("expected escalated state, got %+v", next)
		}
	})

	t.Run("next_retry_at in future -> wait, reached -> run", func(t *testing.T) {
		retryTime := baseTime.Add(10 * time.Minute)
		run := RefreshRun{
			State:       "retrying",
			Attempt:     1,
			NextRetryAt: retryTime.Format(time.RFC3339),
		}

		// Before retry time
		beforeTime := baseTime.Add(5 * time.Minute)
		action, _ := nextAction(run, policy, beforeTime, true)
		if action != ActionWait {
			t.Errorf("expected ActionWait before retry time, got %v", action)
		}

		// At retry time, but deps incomplete -> wait
		actionDepsWait, _ := nextAction(run, policy, retryTime, false)
		if actionDepsWait != ActionWait {
			t.Errorf("expected ActionWait when deps incomplete, got %v", actionDepsWait)
		}

		// At retry time, deps complete -> run
		actionRun, nextRun := nextAction(run, policy, retryTime, true)
		if actionRun != ActionRun {
			t.Errorf("expected ActionRun at retry time, got %v", actionRun)
		}
		if nextRun.State != "running" || nextRun.Attempt != 2 {
			t.Errorf("expected state running with attempt 2, got %+v", nextRun)
		}
	})

	t.Run("timeout -> failed -> retry", func(t *testing.T) {
		startedTime := baseTime.Add(-700 * time.Second) // 700s > TimeoutSeconds 600s
		run := RefreshRun{
			State:     "running",
			Attempt:   1,
			StartedAt: startedTime.Format(time.RFC3339),
		}

		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionWait {
			t.Errorf("expected ActionWait after timeout transition to retry, got %v", action)
		}
		if next.State != "retrying" {
			t.Errorf("expected next state retrying, got %v", next.State)
		}
		if next.ExitCode != 124 {
			t.Errorf("expected exit code 124 for timeout, got %d", next.ExitCode)
		}
		// Consecutive failures incremented
		if next.ConsecutiveFailures != 1 {
			t.Errorf("expected 1 consecutive failure, got %d", next.ConsecutiveFailures)
		}
		// Next retry at baseTime + backoff(1) = baseTime + 300s
		expectedRetry := baseTime.Add(300 * time.Second).Format(time.RFC3339)
		if next.NextRetryAt != expectedRetry {
			t.Errorf("expected retry at %v, got %v", expectedRetry, next.NextRetryAt)
		}
	})

	t.Run("timeout at max attempts -> escalate", func(t *testing.T) {
		startedTime := baseTime.Add(-700 * time.Second)
		run := RefreshRun{
			State:     "running",
			Attempt:   3, // max attempts
			StartedAt: startedTime.Format(time.RFC3339),
		}

		action, next := nextAction(run, policy, baseTime, true)
		if action != ActionEscalate {
			t.Errorf("expected ActionEscalate on timeout at max attempts, got %v", action)
		}
		if next.State != "escalated" || !next.Escalated {
			t.Errorf("expected escalated state, got %+v", next)
		}
	})
}

