package preflight_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/preflight"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCheck_SkillMissing(t *testing.T) {
	err := preflight.Check("/nonexistent/skill/dir")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "skill not found")
}

func TestCheck_SkillPresent(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte("# test"), 0644)
	os.WriteFile(filepath.Join(dir, "preflight.sh"), []byte("#!/bin/bash\necho ok"), 0755)
	// claude won't be present in test env — just verify skill check passes
	err := preflight.Check(dir)
	// error may occur for claude/tools, but not for skill
	if err != nil {
		assert.NotContains(t, err.Error(), "skill not found")
	}
}
