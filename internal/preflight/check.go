package preflight

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func Check(skillDir string) error {
	// 1. claude on PATH
	if _, err := exec.LookPath("claude"); err != nil {
		return fmt.Errorf("claude not found on PATH: %w", err)
	}
	// 2. skill SKILL.md exists
	skillMD := filepath.Join(skillDir, "SKILL.md")
	if _, err := os.Stat(skillMD); err != nil {
		return fmt.Errorf("skill not found at %s: %w", skillMD, err)
	}
	// 3. required tools
	for _, tool := range []string{"duckdb", "jq", "gzip"} {
		if _, err := exec.LookPath(tool); err != nil {
			return fmt.Errorf("required tool %q not found on PATH: %w", tool, err)
		}
	}
	return nil
}
