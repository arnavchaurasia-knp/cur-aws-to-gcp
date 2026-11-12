package engine

import (
	"context"
	"testing"
	"time"
)

func TestCLIEngineAdmitAndRelease(t *testing.T) {
	eng := NewCLIEngine(CLIEngineConfig{
		Concurrency: 2,
	})

	ctx := context.Background()
	rel1, err := eng.Admit(ctx, "tenant-a")
	if err != nil {
		t.Fatalf("unexpected admit error: %v", err)
	}

	rel2, err := eng.Admit(ctx, "tenant-b")
	if err != nil {
		t.Fatalf("unexpected admit error: %v", err)
	}

	// 3rd admit should block
	ctxTimeout, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()

	_, err = eng.Admit(ctxTimeout, "tenant-c")
	if err == nil {
		t.Fatalf("expected 3rd admit to time out due to concurrency limit")
	}

	// Release one slot
	rel1()

	// Double-release should be a no-op
	rel1()

	// Now 3rd admit should succeed
	ctxShort, cancelShort := context.WithTimeout(ctx, 100*time.Millisecond)
	defer cancelShort()

	rel3, err := eng.Admit(ctxShort, "tenant-c")
	if err != nil {
		t.Fatalf("expected admit to succeed after release, got %v", err)
	}

	rel2()
	rel3()
}

func TestAPIEngineTenantIsolation(t *testing.T) {
	eng := NewAPIEngine(APIEngineConfig{
		Model: "gemini-3.8-flash",
	})

	ctx := context.Background()

	// Admit tenant-1 up to lane capacity (4)
	var rels []func()
	for i := 0; i < 4; i++ {
		rel, err := eng.Admit(ctx, "tenant-1")
		if err != nil {
			t.Fatalf("tenant-1 admit failed: %v", err)
		}
		rels = append(rels, rel)
	}

	// 5th admit for tenant-1 should block
	ctxTimeout, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	if _, err := eng.Admit(ctxTimeout, "tenant-1"); err == nil {
		t.Fatalf("expected tenant-1 to be capped")
	}

	// tenant-2 should NOT be blocked by tenant-1's usage (isolated quota lane)
	relT2, err := eng.Admit(ctx, "tenant-2")
	if err != nil {
		t.Fatalf("tenant-2 should admit independently: %v", err)
	}
	relT2()

	for _, r := range rels {
		r()
	}
}

func TestCLIEngineRunPhaseEmptyPrompt(t *testing.T) {
	eng := NewCLIEngine(CLIEngineConfig{
		Concurrency: 1,
	})

	res, err := eng.RunPhase(context.Background(), PhaseRequest{
		JobDir: t.TempDir(),
		Phase: Phase{
			Num:    1,
			Name:   "Ingestion",
			Prompt: "",
		},
		Tenant: "local-dev",
	})
	if err != nil {
		t.Fatalf("expected no error for empty prompt: %v", err)
	}
	if res.DurationSeconds < 0 {
		t.Fatalf("invalid duration seconds: %d", res.DurationSeconds)
	}
}
