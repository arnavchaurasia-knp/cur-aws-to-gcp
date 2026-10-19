package preflight

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/facets/cur-web/internal/skill"
)

// Check verifies that the host has everything the server needs before it
// starts accepting jobs.
func Check() error {
	// 1. agy — honour AGY_PATH env var (same resolution as the spawner) before
	// falling back to PATH lookup.
	agyPath := os.Getenv("AGY_PATH")
	if agyPath != "" {
		if _, err := os.Stat(agyPath); err != nil {
			if _, lookErr := exec.LookPath(agyPath); lookErr != nil {
				log.Printf("WARNING: preflight: AGY_PATH=%q not found (%v) — agy LLM features unavailable, auth/UI unaffected", agyPath, err)
			}
		}
	} else if _, err := exec.LookPath("agy"); err != nil {
		log.Printf("WARNING: preflight: agy not found on PATH — agy LLM features unavailable, auth/UI unaffected")
	}
	// 2. skill installed — uses the same resolution logic as the spawner
	skillDir := skill.ResolveDir()
	skillMD := filepath.Join(skillDir, "SKILL.md")
	if _, err := os.Stat(skillMD); err != nil {
		return fmt.Errorf(
			"skill not found at %s — run: rsync -a <path-to-skill-dir>/ %s/: %w",
			skillMD, skillDir, err,
		)
	}
	// 3. required tools for the skill pipeline — warn only; server still starts
	// so auth and UI work even without the full pipeline toolchain.
	if _, err := exec.LookPath("python3"); err != nil {
		if _, err := exec.LookPath("python"); err != nil {
			log.Printf("WARNING: preflight: python3/python not found on PATH — job pipeline will fail, auth/UI unaffected")
		}
	}
	for _, tool := range []string{"duckdb", "jq", "gzip"} {
		if _, err := exec.LookPath(tool); err != nil {
			log.Printf("WARNING: preflight: %q not found on PATH — job pipeline will fail, auth/UI unaffected", tool)
		}
	}
	// 4. key scripts are readable
	for _, rel := range []string{"scripts/ingest.py", "scripts/validate_fix.py"} {
		p := filepath.Join(skillDir, rel)
		if _, err := os.Stat(p); err != nil {
			log.Printf("WARNING: preflight: skill script not found: %s — job pipeline will fail, auth/UI unaffected", p)
		}
	}
	return nil
}
