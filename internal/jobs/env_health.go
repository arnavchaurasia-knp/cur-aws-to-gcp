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

// ─────────────────────────────────────────────────────────────────────────
// Runtime dependencies: the programs and disk the pipeline needs on the
// server itself. Credentials are NOT checked here — they live in the
// Settings & Secrets registry (internal/envmgr) so each is shown once.
// ─────────────────────────────────────────────────────────────────────────

// DepHealth reports one runtime dependency.
type DepHealth struct {
	Key         string `json:"key"`
	Title       string `json:"title"`
	Description string `json:"description"`
	Status      string `json:"status"` // ok | warn | fail
	Detail      string `json:"detail"`
	Present     bool   `json:"present"`
	Fix         string `json:"fix,omitempty"`
}

var (
	duckdbProbeMu  sync.Mutex
	duckdbCached   *DepHealth
	duckdbCachedAt time.Time
)

// redactPresence summarises a secret without revealing it.
func redactPresence(v string) string {
	if v == "" {
		return "not configured"
	}
	if len(v) <= 4 {
		return fmt.Sprintf("configured (len %d)", len(v))
	}
	return fmt.Sprintf("configured (len %d, ends ...%s)", len(v), v[len(v)-4:])
}

// pythonBinary returns python3 if on PATH, else python, else "".
func pythonBinary() (string, string) {
	for _, name := range []string{"python3", "python"} {
		if p, err := exec.LookPath(name); err == nil {
			return name, p
		}
	}
	return "", ""
}

// CheckRuntimeDependencies checks the tools and disk the pipeline needs.
func CheckRuntimeDependencies(skillDir, jobsDir string) ([]DepHealth, string) {
	if skillDir == "" {
		skillDir = skill.ResolveDir()
	}
	deps := make([]DepHealth, 0, 4)

	// 1. Python
	pyName, pyPath := pythonBinary()
	py := DepHealth{
		Key: "python", Title: "Python 3",
		Description: "Runs the pricing scripts in every pipeline phase and the catalog refresh.",
	}
	if pyName != "" {
		py.Status, py.Present, py.Detail = "ok", true, "found at "+pyPath
	} else {
		py.Status, py.Detail = "fail", "neither python3 nor python is on the server's PATH"
		py.Fix = "Install Python 3 on the server and make sure it is on PATH, then restart the backend."
	}
	deps = append(deps, py)

	// 2. DuckDB Python module (cached 5 min — importing takes ~1s)
	deps = append(deps, checkDuckDB(pyName))

	// 3. Skill data folder writable
	wr := DepHealth{
		Key: "skill_data_writable", Title: "Data folder is writable",
		Description: "Needed to refresh the pricing catalog and to save edits to data files.",
	}
	if isImageBaked(skillDir) {
		wr.Status, wr.Present = "warn", true
		wr.Detail = "Data files are baked into the container image (read-only). Edits and catalog refreshes can't be saved here; they ship with each deploy."
	} else {
		probe := filepath.Join(skillDir, "data", fmt.Sprintf(".write_test_%d", time.Now().UnixNano()))
		if err := os.WriteFile(probe, []byte("ok"), 0o644); err == nil {
			_ = os.Remove(probe)
			wr.Status, wr.Present, wr.Detail = "ok", true, "writable: "+filepath.Join(skillDir, "data")
		} else {
			wr.Status, wr.Detail = "fail", fmt.Sprintf("cannot write to %s: %v", filepath.Join(skillDir, "data"), err)
			wr.Fix = "Give the server's user write permission on that folder."
		}
	}
	deps = append(deps, wr)

	// 4. Disk space
	target := jobsDir
	if target == "" {
		target = filepath.Dir(skillDir)
	}
	disk := DepHealth{
		Key: "disk_space", Title: "Free disk space",
		Description: "Each projection job writes its bill, a DuckDB database and reports to disk.",
	}
	if free, err := getDiskFreeSpaceBytes(target); err == nil {
		disk.Present = true
		gb := float64(free) / (1 << 30)
		switch {
		case free < 50<<20:
			disk.Status, disk.Detail = "fail", fmt.Sprintf("only %.0f MB free", float64(free)/(1<<20))
			disk.Fix = "Free up space or delete old jobs — new jobs will fail to write their output."
		case free < 1<<30:
			disk.Status, disk.Detail = "warn", fmt.Sprintf("%.0f MB free (below 1 GB)", float64(free)/(1<<20))
			disk.Fix = "Free up space soon; large bills need several hundred MB each."
		default:
			disk.Status, disk.Detail = "ok", fmt.Sprintf("%.1f GB free", gb)
		}
	} else {
		disk.Status, disk.Detail = "warn", "could not measure free space: "+err.Error()
	}
	deps = append(deps, disk)

	status := "ok"
	for _, d := range deps {
		if d.Status == "fail" {
			return deps, "fail"
		}
		if d.Status == "warn" {
			status = "warn"
		}
	}
	return deps, status
}

func checkDuckDB(pythonName string) DepHealth {
	duckdbProbeMu.Lock()
	if duckdbCached != nil && time.Since(duckdbCachedAt) < 5*time.Minute {
		d := *duckdbCached
		duckdbProbeMu.Unlock()
		return d
	}
	duckdbProbeMu.Unlock()

	d := DepHealth{
		Key: "python_duckdb", Title: "DuckDB for Python",
		Description: "The database engine every pipeline phase and the pricing catalog use.",
	}
	if pythonName == "" {
		d.Status, d.Detail = "fail", "can't check — Python is missing"
		d.Fix = "Install Python 3 first."
	} else {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		out, err := exec.CommandContext(ctx, pythonName, "-c", "import duckdb; print(duckdb.__version__)").Output()
		cancel()
		if err == nil {
			d.Status, d.Present = "ok", true
			d.Detail = "version " + strings.TrimSpace(string(out))
		} else {
			d.Status, d.Detail = "fail", "import duckdb failed: "+err.Error()
			d.Fix = "Run: pip install duckdb   (with the same Python the server uses)"
		}
	}

	duckdbProbeMu.Lock()
	duckdbCached, duckdbCachedAt = &d, time.Now()
	duckdbProbeMu.Unlock()
	return d
}
