package jobs

import (
	"context"
	"encoding/json"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/facets/cur-web/internal/skill"
)

// computeAvailabilityStaleDays: refresh daily, not weekly like the Billing
// Catalog (catalogStaleDays). Machine-type rollouts are a live-deployability
// fact that's cheaper to re-check than the Billing Catalog's full SKU/price
// sweep (scripts/verify_compute_availability.py's single project fetch, not
// prefetch_skus.py's per-service crawl) and more valuable to keep current --
// it's the live CORRECTNESS signal apply_static_mappings.py prefers over the
// Billing Catalog's pricing-only view wherever it's configured.
const computeAvailabilityStaleDays = 1

// computeAvailabilityRefreshTimeout bounds the subprocess. The real fetch
// paginates every machine type across every zone in the probe project
// (~90s observed for a real project with ~43 regions); this leaves generous
// headroom for a slower project/network day without risking an indefinite
// hang -- see verify_compute_availability.py's own module docstring for why
// its gcloud fallback is opt-in-only now that this refresher calls it
// unattended, same reasoning as catalogRefreshTimeout.
const computeAvailabilityRefreshTimeout = 5 * time.Minute

// StartComputeAvailabilityRefresher launches a background goroutine that
// keeps data/compute_family_availability.json fresh, the same shape as
// StartCatalogRefresher but on a daily cadence. Requires
// GOOGLE_CLOUD_AVAILABILITY_PROJECT and GOOGLE_CLOUD_AVAILABILITY_TOKEN (a
// pre-provisioned OAuth2 token -- see .env.example) to actually run; if
// either is unset, each tick logs once and skips, never failing the server.
// A stopped or never-configured refresher is harmless: apply_static_
// mappings.py falls straight back to catalog + _KNOWN_PHANTOM_AVAILABILITY
// for Compute Engine, exactly as if this feature didn't exist.
func StartComputeAvailabilityRefresher(ctx context.Context) {
	go func() {
		runComputeAvailabilityRefreshIfStale()

		ticker := time.NewTicker(24 * time.Hour)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				runComputeAvailabilityRefreshIfStale()
			}
		}
	}()
}

// computeAvailabilityAge returns how many days ago
// data/compute_family_availability.json was last fetched. Returns a large
// number if the file is missing or unreadable (never run yet).
func computeAvailabilityAge() int {
	skillDir := skill.ResolveDir()
	path := filepath.Join(skillDir, "data", "compute_family_availability.json")

	data, err := os.ReadFile(path)
	if err != nil {
		return 9999
	}
	var meta struct {
		FetchedAt string `json:"fetched_at"`
	}
	if err := json.Unmarshal(data, &meta); err != nil || meta.FetchedAt == "" {
		return 9999
	}
	ts, err := time.Parse(time.RFC3339, meta.FetchedAt)
	if err != nil {
		return 9999
	}
	return int(time.Since(ts).Hours() / 24)
}

func runComputeAvailabilityRefreshIfStale() {
	age := computeAvailabilityAge()
	if age < computeAvailabilityStaleDays {
		slog.Debug("compute availability data is fresh, skipping refresh", "age_days", age)
		return
	}
	runComputeAvailabilityRefreshNow()
}

// runComputeAvailabilityRefreshNow runs verify_compute_availability.py
// unconditionally, ignoring computeAvailabilityStaleDays. Exported for a
// future admin "refresh now" action, mirroring TriggerCatalogRefresh.
func runComputeAvailabilityRefreshNow() {
	project := os.Getenv("GOOGLE_CLOUD_AVAILABILITY_PROJECT")
	if project == "" {
		slog.Debug("GOOGLE_CLOUD_AVAILABILITY_PROJECT not set, skipping compute availability refresh")
		return
	}
	if os.Getenv("GOOGLE_CLOUD_AVAILABILITY_TOKEN") == "" && os.Getenv("AGY_ALLOW_GCLOUD_LIVE_FETCH") != "1" {
		slog.Debug("no GOOGLE_CLOUD_AVAILABILITY_TOKEN (and gcloud fallback not opted in), " +
			"skipping compute availability refresh")
		return
	}

	skillDir := skill.ResolveDir()
	script := filepath.Join(skillDir, "scripts", "verify_compute_availability.py")
	if _, err := os.Stat(script); err != nil {
		slog.Warn("verify_compute_availability.py not found, skipping refresh", "path", script)
		return
	}

	slog.Info("refreshing live Compute Engine availability data", "age_days", computeAvailabilityAge())

	python := "python3"
	if _, err := exec.LookPath(python); err != nil {
		python = "python"
	}

	ctx, cancel := context.WithTimeout(context.Background(), computeAvailabilityRefreshTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, python, script, "--project", project)
	cmd.Dir = skillDir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			slog.Warn("compute availability refresh timed out, killed subprocess",
				"timeout", computeAvailabilityRefreshTimeout)
		} else {
			slog.Warn("compute availability refresh finished with error", "err", err)
		}
	} else {
		slog.Info("compute availability refresh complete")
	}
}

// GetComputeAvailabilityStatus is the admin-facing view of freshness,
// mirroring GetCatalogStatus.
type ComputeAvailabilityStatus struct {
	AgeDays int  `json:"age_days"`
	Stale   bool `json:"stale"`
}

func GetComputeAvailabilityStatus() ComputeAvailabilityStatus {
	age := computeAvailabilityAge()
	return ComputeAvailabilityStatus{AgeDays: age, Stale: age >= computeAvailabilityStaleDays}
}
