package jobs

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/facets/cur-web/internal/skill"
)

// LifecyclePolicy declares the retry, backoff, dependency, and escalation rules for a refresh source.
type LifecyclePolicy struct {
	IntervalHours                    float64  `json:"interval_hours"`
	MaxAttempts                      int      `json:"max_attempts"`
	BackoffBaseSeconds               float64  `json:"backoff_base_seconds"`
	BackoffFactor                    float64  `json:"backoff_factor"`
	BackoffCapSeconds                float64  `json:"backoff_cap_seconds"`
	TimeoutSeconds                   int      `json:"timeout_seconds"`
	DependsOn                        []string `json:"depends_on"`
	EscalateAfterConsecutiveFailures int      `json:"escalate_after_consecutive_failures"`
	TreatPartialAs                   string   `json:"treat_partial_as"` // "retry" | "accept"
}

// RefreshSourceConfig declares completeness expectations and lifecycle for a provider data source.
type RefreshSourceConfig struct {
	ExpectedSet           string          `json:"expected_set,omitempty"`
	BaselineCounts        string          `json:"baseline_counts,omitempty"`
	CompletenessThreshold float64         `json:"completeness_threshold,omitempty"`
	StaleAfterDays        int             `json:"stale_after_days,omitempty"`
	Refresher             string          `json:"refresher,omitempty"`
	Lifecycle             LifecyclePolicy `json:"lifecycle"`
}

func refreshSourcesPath() string {
	return filepath.Join(skill.ResolveDir(), "data", "refresh_sources.json")
}

// LoadRefreshSources parses data/refresh_sources.json and returns a map of source key -> configuration.
func LoadRefreshSources() (map[string]RefreshSourceConfig, error) {
	path := refreshSourcesPath()
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read refresh_sources.json at %s: %w", path, err)
	}
	var sources map[string]RefreshSourceConfig
	if err := json.Unmarshal(data, &sources); err != nil {
		return nil, fmt.Errorf("failed to parse refresh_sources.json: %w", err)
	}
	return sources, nil
}

// LoadRefreshSource returns the configuration for a specific refresh source, or false if not found.
func LoadRefreshSource(name string) (RefreshSourceConfig, bool) {
	sources, err := LoadRefreshSources()
	if err != nil {
		return RefreshSourceConfig{}, false
	}
	cfg, ok := sources[name]
	return cfg, ok
}

