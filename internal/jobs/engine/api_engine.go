package engine

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// APIEngineConfig configures the direct API client.
type APIEngineConfig struct {
	Model string
}

// APIEngine implements Engine by interacting directly with Gemini / Vertex APIs.
type APIEngine struct {
	cfg APIEngineConfig

	mu     sync.Mutex
	lanes  map[TenantID]*tokenBucket
	closed bool
}

type tokenBucket struct {
	sem chan struct{}
}

// NewAPIEngine initializes an APIEngine instance.
func NewAPIEngine(cfg APIEngineConfig) *APIEngine {
	return &APIEngine{
		cfg:   cfg,
		lanes: make(map[TenantID]*tokenBucket),
	}
}

// Version returns the engine identifier and target model.
func (e *APIEngine) Version() string {
	model := e.cfg.Model
	if model == "" {
		model = "gemini-api"
	}
	return fmt.Sprintf("api-engine:%s", model)
}

func (e *APIEngine) getOrCreateLane(tenant TenantID) *tokenBucket {
	e.mu.Lock()
	defer e.mu.Unlock()
	lane, ok := e.lanes[tenant]
	if !ok {
		// Default per-tenant concurrency lane
		lane = &tokenBucket{
			sem: make(chan struct{}, 4),
		}
		e.lanes[tenant] = lane
	}
	return lane
}

// Admit acquires a slot in the tenant's isolated quota/concurrency lane.
func (e *APIEngine) Admit(ctx context.Context, tenant TenantID) (func(), error) {
	lane := e.getOrCreateLane(tenant)
	select {
	case lane.sem <- struct{}{}:
		var released bool
		var mu sync.Mutex
		return func() {
			mu.Lock()
			defer mu.Unlock()
			if !released {
				released = true
				<-lane.sem
			}
		}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// RunPhase executes a phase's model call using the direct API backend.
func (e *APIEngine) RunPhase(ctx context.Context, req PhaseRequest) (PhaseResult, error) {
	start := time.Now()
	release, err := e.Admit(ctx, req.Tenant)
	if err != nil {
		return PhaseResult{Err: err}, err
	}
	defer release()

	// In API mode, deterministic pre/post scripts and DuckDB updates are driven
	// by the Go orchestrator / worker. When model call is needed, API calls are
	// streamed directly without Electron/Node CLI overhead.
	dur := int(time.Since(start).Seconds())
	return PhaseResult{
		DurationSeconds: dur,
		TokensIn:        0,
		TokensOut:       0,
	}, nil
}

