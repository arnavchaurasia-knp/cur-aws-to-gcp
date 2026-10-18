package jobs_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/facets/cur-web/internal/jobs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunValidatorGate_MissingScript(t *testing.T) {
	// Point skill dir at an empty temp dir via SKILL_DIR env
	tmp := t.TempDir()
	orig := os.Getenv("SKILL_DIR")
	defer os.Setenv("SKILL_DIR", orig)
	os.Setenv("SKILL_DIR", tmp)

	outcome, summary := jobs.RunValidatorGate(t.TempDir())
	assert.Equal(t, jobs.ValidatorNotRun, outcome)
	assert.Contains(t, summary, "validator script absent")
}

func TestWriteValidationVerdict(t *testing.T) {
	cases := []struct {
		outcome jobs.ValidatorOutcome
		label   string
	}{
		{jobs.ValidatorClean, "clean"},
		{jobs.ValidatorFailed, "violations"},
		{jobs.ValidatorNotRun, "not_run"},
	}

	for _, tc := range cases {
		jobDir := t.TempDir()
		jobs.WriteValidationVerdict(jobDir, tc.outcome, "test summary output")
		verdictFile := filepath.Join(jobDir, "validation_verdict.txt")
		data, err := os.ReadFile(verdictFile)
		require.NoError(t, err)
		lines := strings.Split(string(data), "\n")
		require.NotEmpty(t, lines)
		assert.Equal(t, tc.label, lines[0])
		assert.Contains(t, string(data), "test summary output")
	}
}

func TestValidatorOutcomeNeverFailsJob(t *testing.T) {
	// Assert by inspection that runValidatorGate and writeValidationVerdict
	// are never connected to UpdateJobFailed in watcher.go.
	data, err := os.ReadFile("watcher.go")
	require.NoError(t, err)
	s := string(data)
	assert.NotContains(t, s, "UpdateJobFailed(jobID, vsummary)")
	assert.NotContains(t, s, "UpdateJobFailed(jobID, summary)")
}

func TestRenderOrchestratorCompiles(t *testing.T) {
	rendered, err := jobs.RenderOrchestratorForTest(".csv")
	require.NoError(t, err)

	tmp := t.TempDir()
	renderedPath := filepath.Join(tmp, "run_all_rendered.py")
	err = os.WriteFile(renderedPath, []byte(rendered), 0644)
	require.NoError(t, err)

	cmd := exec.Command("python", "-m", "py_compile", renderedPath)
	out, err := cmd.CombinedOutput()
	require.NoError(t, err, "rendered orchestrator must compile under python: %s", string(out))

	count := strings.Count(rendered, "?scripts/ensure_full_coverage.py $DB")
	assert.Equal(t, 1, count, "ensure_full_coverage script must be invoked exactly once in rendered template")
}
