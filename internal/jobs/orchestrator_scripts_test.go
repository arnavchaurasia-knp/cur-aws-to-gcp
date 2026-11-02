package jobs

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestValidatePhaseScripts_RealSkillDir(t *testing.T) {
	// Find skill directory relative to internal/jobs
	skillDir, err := filepath.Abs(filepath.Join("..", "..", "skill", "aws-gcp-cost-projection"))
	if err != nil {
		t.Fatalf("failed to resolve skill dir: %v", err)
	}

	for _, ext := range []string{".csv", ".parquet", ".zip"} {
		if err := ValidatePhaseScripts(skillDir, ext); err != nil {
			t.Fatalf("ValidatePhaseScripts failed for ext %s against real skill dir: %v", ext, err)
		}
	}
}

func TestValidatePhaseScripts_MissingScriptFails(t *testing.T) {
	tmpDir := t.TempDir()

	err := ValidatePhaseScripts(tmpDir, ".csv")
	if err == nil {
		t.Fatalf("expected error when scripts are missing in %s, got nil", tmpDir)
	}
	if !strings.Contains(err.Error(), "referenced phase scripts missing from skillDir") {
		t.Fatalf("unexpected error message: %v", err)
	}
}

func TestValidatePhaseScripts_EmptySkillDir(t *testing.T) {
	err := ValidatePhaseScripts("", ".csv")
	if err == nil {
		t.Fatalf("expected error for empty skillDir, got nil")
	}
}
