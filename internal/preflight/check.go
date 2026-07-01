package preflight

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

const skillName = "aws-gcp-cost-projection"

// Check verifies that the host has everything the server needs before it
// starts accepting jobs.
func Check() error {
	// 1. agy on PATH
	if _, err := exec.LookPath("agy"); err != nil {
		return fmt.Errorf("agy not found on PATH: %w", err)
	}
	// 2. skill installed at ~/.gemini/antigravity-cli/skills/<skill>/SKILL.md
	skillMD, err := userSkillPath()
	if err != nil {
		return fmt.Errorf("cannot resolve home dir: %w", err)
	}
	if _, err := os.Stat(skillMD); err != nil {
		return fmt.Errorf(
			"skill not found at %s — run: rsync -a <path-to-skill-dir>/ %s/: %w",
			skillMD, filepath.Dir(skillMD), err,
		)
	}
	// 3. required tools for the skill pipeline
	for _, tool := range []string{"duckdb", "jq", "gzip"} {
		if _, err := exec.LookPath(tool); err != nil {
			return fmt.Errorf("required tool %q not found on PATH: %w", tool, err)
		}
	}
	return nil
}

func userSkillPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".gemini", "antigravity-cli", "skills", skillName, "SKILL.md"), nil
}
