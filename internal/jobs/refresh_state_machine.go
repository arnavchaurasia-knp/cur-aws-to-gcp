package jobs

import (
	"fmt"
	"math"
	"time"
)

// Action represents the decision returned by the pure state-machine for a refresh source.
type Action string

const (
	ActionSkip     Action = "skip"
	ActionRun      Action = "run"
	ActionEscalate Action = "escalate"
	ActionWait     Action = "wait"
)

// ComputeBackoff calculates backoff duration according to:
// backoff(n) = min(backoff_cap, backoff_base * backoff_factor^(n-1))
func ComputeBackoff(policy LifecyclePolicy, attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	base := policy.BackoffBaseSeconds
	if base <= 0 {
		base = 300
	}
	factor := policy.BackoffFactor
	if factor <= 0 {
		factor = 1.0
	}
	sec := base * math.Pow(factor, float64(attempt-1))
	if policy.BackoffCapSeconds > 0 && sec > policy.BackoffCapSeconds {
		sec = policy.BackoffCapSeconds
	}
	return time.Duration(sec * float64(time.Second))
}

// nextAction is a pure function that evaluates the current RefreshRun against its LifecyclePolicy
// at the given reference time (now) and dependency status (depsComplete).
// It returns the Action to take and the updated RefreshRun state.
func nextAction(run RefreshRun, policy LifecyclePolicy, now time.Time, depsComplete bool) (Action, RefreshRun) {
	// 1. Paused source
	if run.State == "paused" {
		return ActionSkip, run
	}

	// 2. Running state (check for timeout)
	if run.State == "running" {
		if policy.TimeoutSeconds > 0 && run.StartedAt != "" {
			startedAt, err := time.Parse(time.RFC3339, run.StartedAt)
			if err == nil && now.Sub(startedAt) > time.Duration(policy.TimeoutSeconds)*time.Second {
				// Timed out
				next := run
				next.FinishedAt = now.Format(time.RFC3339)
				next.DurationSeconds = int(now.Sub(startedAt).Seconds())
				next.ExitCode = 124
				next.Reason = fmt.Sprintf("timed out after %ds", policy.TimeoutSeconds)
				next.ConsecutiveFailures++
				if next.Attempt < 1 {
					next.Attempt = 1
				}
				if policy.MaxAttempts > 0 && next.Attempt >= policy.MaxAttempts {
					next.State = "escalated"
					next.Escalated = true
					next.NextRetryAt = ""
					return ActionEscalate, next
				}
				next.State = "retrying"
				backoffDur := ComputeBackoff(policy, next.Attempt)
				next.NextRetryAt = now.Add(backoffDur).Format(time.RFC3339)
				return ActionWait, next
			}
		}
		// Still actively running within timeout
		return ActionWait, run
	}

	// 3. Newly failed or partial (where TreatPartialAs == "retry")
	if run.State == "failed" || (run.State == "partial" && policy.TreatPartialAs == "retry") {
		if run.NextRetryAt == "" {
			att := run.Attempt
			if att < 1 {
				att = 1
			}
			if policy.MaxAttempts > 0 && att >= policy.MaxAttempts {
				next := run
				next.State = "escalated"
				next.Escalated = true
				next.NextRetryAt = ""
				return ActionEscalate, next
			}
			next := run
			next.State = "retrying"
			backoffDur := ComputeBackoff(policy, att)
			next.NextRetryAt = now.Add(backoffDur).Format(time.RFC3339)
			return ActionWait, next
		}
	}

	// 4. Retrying / backing_off (waiting for NextRetryAt or executing retry)
	if run.State == "retrying" || run.State == "backing_off" {
		if run.NextRetryAt != "" {
			retryAt, err := time.Parse(time.RFC3339, run.NextRetryAt)
			if err == nil && now.Before(retryAt) {
				return ActionWait, run
			}
		}
		// NextRetryAt reached
		if !depsComplete {
			return ActionWait, run
		}
		next := run
		next.State = "running"
		next.Attempt++
		next.StartedAt = now.Format(time.RFC3339)
		next.FinishedAt = ""
		next.NextRetryAt = ""
		return ActionRun, next
	}

	// 5. Escalated
	if run.State == "escalated" {
		if policy.IntervalHours > 0 {
			var refTime time.Time
			if run.FinishedAt != "" {
				refTime, _ = time.Parse(time.RFC3339, run.FinishedAt)
			} else if run.StartedAt != "" {
				refTime, _ = time.Parse(time.RFC3339, run.StartedAt)
			}
			if !refTime.IsZero() && now.Sub(refTime) >= time.Duration(policy.IntervalHours)*time.Hour {
				if !depsComplete {
					return ActionWait, run
				}
				next := run
				next.State = "running"
				next.Attempt = 1
				next.StartedAt = now.Format(time.RFC3339)
				next.FinishedAt = ""
				next.NextRetryAt = ""
				return ActionRun, next
			}
		}
		return ActionWait, run
	}

	// 6. Idle / empty (initial state)
	if run.State == "idle" || run.State == "" {
		if !depsComplete {
			return ActionWait, run
		}
		next := run
		next.State = "running"
		next.Attempt = 1
		next.ConsecutiveFailures = 0
		next.Escalated = false
		next.StartedAt = now.Format(time.RFC3339)
		next.FinishedAt = ""
		next.NextRetryAt = ""
		return ActionRun, next
	}

	// 7. Success, skipped, or accepted partial
	next := run
	next.Attempt = 0
	next.ConsecutiveFailures = 0
	next.Escalated = false
	next.NextRetryAt = ""

	if run.FinishedAt == "" {
		if !depsComplete {
			return ActionWait, next
		}
		next.State = "running"
		next.Attempt = 1
		next.StartedAt = now.Format(time.RFC3339)
		return ActionRun, next
	}

	finishedAt, err := time.Parse(time.RFC3339, run.FinishedAt)
	if err != nil {
		if !depsComplete {
			return ActionWait, next
		}
		next.State = "running"
		next.Attempt = 1
		next.StartedAt = now.Format(time.RFC3339)
		return ActionRun, next
	}

	interval := time.Duration(policy.IntervalHours) * time.Hour
	if interval <= 0 {
		interval = 24 * time.Hour
	}

	if now.Sub(finishedAt) < interval {
		return ActionWait, next
	}

	// Due for next scheduled run
	if !depsComplete {
		return ActionWait, next
	}
	next.State = "running"
	next.Attempt = 1
	next.StartedAt = now.Format(time.RFC3339)
	next.FinishedAt = ""
	return ActionRun, next
}

