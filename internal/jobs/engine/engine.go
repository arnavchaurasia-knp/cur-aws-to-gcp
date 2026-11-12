package engine

import (
	"context"
	"time"
)

// TenantID identifies an isolated customer, organization, or user lane.
type TenantID string

// Phase represents an individual pipeline phase to be executed by an engine.
type Phase struct {
	Num             int      `json:"num"`
	Name            string   `json:"name"`
	Prompt          string   `json:"prompt,omitempty"`
	CheckSQL        string   `json:"check_sql,omitempty"`
	CheckName       string   `json:"check_name,omitempty"`
	PostLLMScripts  []string `json:"post_llm_scripts,omitempty"`
}

// PhaseRequest packages all inputs needed to execute a phase's model portion.
type PhaseRequest struct {
	JobDir   string
	Phase    Phase
	Tenant   TenantID
	Deadline time.Time
}

// PhaseResult summarizes the execution outcome and resource usage.
type PhaseResult struct {
	DurationSeconds int   `json:"duration_seconds"`
	TokensIn        int   `json:"tokens_in"`
	TokensOut       int   `json:"tokens_out"`
	Err             error `json:"err,omitempty"`
}

// Engine defines the abstraction for model execution and admission control.
// Deterministic pre/post scripts and gates stay in the orchestrator —
// the Engine only owns the model call and per-tenant admission.
type Engine interface {
	// RunPhase executes exactly one phase's LLM portion.
	RunPhase(ctx context.Context, req PhaseRequest) (PhaseResult, error)

	// Version returns the engine/binary/model version string.
	Version() string

	// Admit blocks until this tenant has quota to make a model call, or ctx is cancelled.
	// For CLIEngine this is a local concurrency semaphore; for APIEngine it is a
	// per-tenant token bucket or quota lane.
	Admit(ctx context.Context, tenant TenantID) (release func(), err error)
}

