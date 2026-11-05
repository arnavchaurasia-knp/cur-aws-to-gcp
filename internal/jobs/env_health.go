package jobs

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/facets/cur-web/internal/skill"
)

// DepHealth reports the presence, reachability, and health of an environment dependency.
type DepHealth struct {
	Key     string `json:"key"`
	Status  string `json:"status"` // ok | warn | fail
	Detail  string `json:"detail"`
	Present bool   `json:"present"`
}

var (
	duckdbProbeMu       sync.Mutex
	cachedDuckDBStatus  string
	cachedDuckDBDetail  string
	cachedDuckDBPresent bool
	cachedDuckDBAt      time.Time
)

// redactPresence safely summarizes secret presence without leaking the value.
func redactPresence(v string) string {
	if v == "" {
		return "not configured"
	}
	if len(v) <= 4 {
		return fmt.Sprintf("configured (len %d)", len(v))
	}
	return fmt.Sprintf("configured (len %d, ends ...%s)", len(v), v[len(v)-4:])
}

// CheckEnvDependencies checks all critical runtime dependencies for the pipeline and service.
func CheckEnvDependencies(skillDir, jobsDir, agyPath string) ([]DepHealth, string) {
	if skillDir == "" {
		skillDir = skill.ResolveDir()
	}

	deps := make([]DepHealth, 0, 8)

	// 1. ANTIGRAVITY_API_KEY
	antigravityKey := os.Getenv("ANTIGRAVITY_API_KEY")
	if antigravityKey != "" {
		deps = append(deps, DepHealth{
			Key:     "ANTIGRAVITY_API_KEY",
			Status:  "ok",
			Detail:  redactPresence(antigravityKey),
			Present: true,
		})
	} else {
		deps = append(deps, DepHealth{
			Key:     "ANTIGRAVITY_API_KEY",
			Status:  "fail",
			Detail:  "missing; pipeline reasoning agent will fail authentication",
			Present: false,
		})
	}

	// 2. RESEND_API_KEY
	resendKey := os.Getenv("RESEND_API_KEY")
	if resendKey != "" {
		deps = append(deps, DepHealth{
			Key:     "RESEND_API_KEY",
			Status:  "ok",
			Detail:  redactPresence(resendKey),
			Present: true,
		})
	} else {
		deps = append(deps, DepHealth{
			Key:     "RESEND_API_KEY",
			Status:  "warn",
			Detail:  "not configured; outbound email notifications disabled",
			Present: false,
		})
	}

	// 3. agy binary
	bin := agyPath
	if bin == "" {
		bin = "agy"
	}
	if p, err := exec.LookPath(bin); err == nil {
		deps = append(deps, DepHealth{
			Key:     "agy",
			Status:  "ok",
			Detail:  fmt.Sprintf("found at %s", p),
			Present: true,
		})
	} else {
		deps = append(deps, DepHealth{
			Key:     "agy",
			Status:  "fail",
			Detail:  fmt.Sprintf("not found on PATH (looked for %q)", bin),
			Present: false,
		})
	}

	// 4. Python 3
	pythonBin := "python3"
	if p, err := exec.LookPath(pythonBin); err == nil {
		deps = append(deps, DepHealth{
			Key:     "python3",
			Status:  "ok",
			Detail:  fmt.Sprintf("found at %s", p),
			Present: true,
		})
	} else if p, err := exec.LookPath("python"); err == nil {
		pythonBin = "python"
		deps = append(deps, DepHealth{
			Key:     "python3",
			Status:  "ok",
			Detail:  fmt.Sprintf("found at %s (aliased as python)", p),
			Present: true,
		})
	} else {
		deps = append(deps, DepHealth{
			Key:     "python3",
			Status:  "fail",
			Detail:  "neither python3 nor python found on PATH; catalog & validation will fail",
			Present: false,
		})
	}

	// 5. duckdb (python module)
	duckdbProbeMu.Lock()
	if cachedDuckDBStatus != "" && time.Since(cachedDuckDBAt) < 5*time.Minute {
		deps = append(deps, DepHealth{
			Key:     "python_duckdb",
			Status:  cachedDuckDBStatus,
			Detail:  cachedDuckDBDetail,
			Present: cachedDuckDBPresent,
		})
		duckdbProbeMu.Unlock()
	} else {
		duckdbProbeMu.Unlock()
		// Probe duckdb python module
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		cmd := exec.CommandContext(ctx, pythonBin, "-c", "import duckdb; print(duckdb.__version__)")
		out, err := cmd.Output()
		cancel()

		var status, detail string
		var present bool
		if err == nil {
			status = "ok"
			present = true
			detail = fmt.Sprintf("duckdb module v%s available", strings.TrimSpace(string(out)))
		} else {
			status = "fail"
			present = false
			detail = fmt.Sprintf("python duckdb import failed: %v", err)
		}

		duckdbProbeMu.Lock()
		cachedDuckDBStatus = status
		cachedDuckDBDetail = detail
		cachedDuckDBPresent = present
		cachedDuckDBAt = time.Now()
		duckdbProbeMu.Unlock()

		deps = append(deps, DepHealth{
			Key:     "python_duckdb",
			Status:  status,
			Detail:  detail,
			Present: present,
		})
	}

	// 6. GCP Credentials for catalog refresh
	gcpKey := os.Getenv("GOOGLE_CLOUD_API_KEY")
	if gcpKey == "" {
		gcpKey = os.Getenv("GCP_API_KEY")
	}
	if gcpKey != "" {
		deps = append(deps, DepHealth{
			Key:     "GCP_CREDENTIALS",
			Status:  "ok",
			Detail:  redactPresence(gcpKey),
			Present: true,
		})
	} else {
		deps = append(deps, DepHealth{
			Key:     "GCP_CREDENTIALS",
			Status:  "warn",
			Detail:  "GOOGLE_CLOUD_API_KEY/GCP_API_KEY not set (catalog auto-refresh will be a no-op)",
			Present: false,
		})
	}

	// 7. Skill directory writable
	if isImageBaked(skillDir) {
		deps = append(deps, DepHealth{
			Key:     "skill_dir_writable",
			Status:  "warn",
			Detail:  "image-baked (read-only container layer)",
			Present: true,
		})
	} else {
		testFile := filepath.Join(skillDir, "data", fmt.Sprintf(".write_test_%d", time.Now().UnixNano()))
		if err := os.WriteFile(testFile, []byte("ok"), 0644); err == nil {
			_ = os.Remove(testFile)
			deps = append(deps, DepHealth{
				Key:     "skill_dir_writable",
				Status:  "ok",
				Detail:  "writable (runtime updates supported)",
				Present: true,
			})
		} else {
			deps = append(deps, DepHealth{
				Key:     "skill_dir_writable",
				Status:  "fail",
				Detail:  fmt.Sprintf("directory not writable: %v", err),
				Present: false,
			})
		}
	}

	// 8. Data disk free space
	targetDir := jobsDir
	if targetDir == "" {
		targetDir = filepath.Dir(skillDir)
	}
	freeBytes, err := getDiskFreeSpaceBytes(targetDir)
	if err == nil {
		freeGB := float64(freeBytes) / (1024 * 1024 * 1024)
		if freeBytes < 50*1024*1024 { // < 50MB
			deps = append(deps, DepHealth{
				Key:     "disk_free_space",
				Status:  "fail",
				Detail:  fmt.Sprintf("critical disk space: %.2f MB free", float64(freeBytes)/(1024*1024)),
				Present: true,
			})
		} else if freeBytes < 1024*1024*1024 { // < 1GB
			deps = append(deps, DepHealth{
				Key:     "disk_free_space",
				Status:  "warn",
				Detail:  fmt.Sprintf("low disk space: %.2f MB free (<1GB threshold)", float64(freeBytes)/(1024*1024)),
				Present: true,
			})
		} else {
			deps = append(deps, DepHealth{
				Key:     "disk_free_space",
				Status:  "ok",
				Detail:  fmt.Sprintf("%.1f GB free", freeGB),
				Present: true,
			})
		}
	} else {
		deps = append(deps, DepHealth{
			Key:     "disk_free_space",
			Status:  "warn",
			Detail:  fmt.Sprintf("unable to check disk space: %v", err),
			Present: true,
		})
	}

	// Roll-up status
	envStatus := "ok"
	for _, d := range deps {
		if d.Status == "fail" {
			envStatus = "fail"
			break
		}
		if d.Status == "warn" && envStatus != "fail" {
			envStatus = "warn"
		}
	}

	return deps, envStatus
}
