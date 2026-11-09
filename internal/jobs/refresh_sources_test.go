package jobs

import (
	"testing"
)

func TestLoadRefreshSources(t *testing.T) {
	sources, err := LoadRefreshSources()
	if err != nil {
		t.Fatalf("LoadRefreshSources failed: %v", err)
	}

	if len(sources) < 2 {
		t.Fatalf("expected at least 2 sources, got %d", len(sources))
	}

	catalog, ok := sources["gcp_catalog"]
	if !ok {
		t.Fatalf("gcp_catalog source missing from refresh_sources.json")
	}
	if catalog.Lifecycle.IntervalHours != 168 {
		t.Errorf("expected interval_hours 168 for gcp_catalog, got %v", catalog.Lifecycle.IntervalHours)
	}
	if catalog.Lifecycle.MaxAttempts != 4 {
		t.Errorf("expected max_attempts 4 for gcp_catalog, got %v", catalog.Lifecycle.MaxAttempts)
	}
	if catalog.Lifecycle.BackoffBaseSeconds != 300 {
		t.Errorf("expected backoff_base_seconds 300, got %v", catalog.Lifecycle.BackoffBaseSeconds)
	}
	if catalog.Lifecycle.TreatPartialAs != "retry" {
		t.Errorf("expected treat_partial_as 'retry', got %q", catalog.Lifecycle.TreatPartialAs)
	}

	comp, ok := sources["compute_availability"]
	if !ok {
		t.Fatalf("compute_availability source missing from refresh_sources.json")
	}
	if comp.Lifecycle.IntervalHours != 24 {
		t.Errorf("expected interval_hours 24 for compute_availability, got %v", comp.Lifecycle.IntervalHours)
	}
	if comp.Lifecycle.MaxAttempts != 3 {
		t.Errorf("expected max_attempts 3 for compute_availability, got %v", comp.Lifecycle.MaxAttempts)
	}
	if comp.Lifecycle.TreatPartialAs != "accept" {
		t.Errorf("expected treat_partial_as 'accept', got %q", comp.Lifecycle.TreatPartialAs)
	}

	domains, ok := sources["assumption_domains"]
	if !ok {
		t.Fatalf("assumption_domains source missing from refresh_sources.json")
	}
	if len(domains.Lifecycle.DependsOn) != 2 {
		t.Errorf("expected 2 depends_on for assumption_domains, got %v", domains.Lifecycle.DependsOn)
	}

	cudCov, ok := sources["cud_coverage"]
	if !ok {
		t.Fatalf("cud_coverage source missing from refresh_sources.json")
	}
	if len(cudCov.Lifecycle.DependsOn) != 1 || cudCov.Lifecycle.DependsOn[0] != "gcp_catalog" {
		t.Errorf("expected depends_on [gcp_catalog] for cud_coverage, got %v", cudCov.Lifecycle.DependsOn)
	}
	if cudCov.Lifecycle.TreatPartialAs != "retry" {
		t.Errorf("expected treat_partial_as 'retry' for cud_coverage, got %q", cudCov.Lifecycle.TreatPartialAs)
	}
	if cudCov.CompletenessThreshold != 100.0 {
		t.Errorf("expected completeness_threshold 100.0 for cud_coverage, got %v", cudCov.CompletenessThreshold)
	}

	// Test LoadRefreshSource helper
	catCfg, found := LoadRefreshSource("gcp_catalog")
	if !found || catCfg.ExpectedSet == "" {
		t.Errorf("LoadRefreshSource(gcp_catalog) failed: found=%v, cfg=%+v", found, catCfg)
	}

	_, notFound := LoadRefreshSource("nonexistent_source")
	if notFound {
		t.Errorf("expected nonexistent_source to not be found")
	}
}

func TestAllDeclaredSourcesHaveDefaultExecutors(t *testing.T) {
	sources, err := LoadRefreshSources()
	if err != nil {
		t.Fatalf("LoadRefreshSources failed: %v", err)
	}

	// Construct orchestrator the way production does (no custom executors)
	orch := NewRefreshOrchestrator(OrchestratorConfig{})

	for sourceName := range sources {
		if _, ok := orch.executors[sourceName]; !ok {
			t.Errorf("declared source %q has no registered default executor in RefreshOrchestrator", sourceName)
		}
	}
}

