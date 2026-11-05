package jobs

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestCheckRuntimeDependencies_ReportsEveryCheckWithExplanations(t *testing.T) {
	skillDir := t.TempDir()
	_ = os.MkdirAll(filepath.Join(skillDir, "data"), 0o755)

	deps, status := CheckRuntimeDependencies(skillDir, t.TempDir())
	assert.NotEmpty(t, status)

	keys := map[string]bool{}
	for _, d := range deps {
		keys[d.Key] = true
		assert.NotEmpty(t, d.Title, d.Key)
		assert.NotEmpty(t, d.Description, d.Key)
		assert.Contains(t, []string{"ok", "warn", "fail"}, d.Status, d.Key)
		if d.Status == "fail" {
			assert.NotEmpty(t, d.Fix, "%s: a failing check must say how to fix it", d.Key)
		}
	}
	for _, k := range []string{"python", "python_duckdb", "skill_data_writable", "disk_space"} {
		assert.True(t, keys[k], "missing check %s", k)
	}
}

func TestCheckRuntimeDependencies_WritableFolder(t *testing.T) {
	skillDir := t.TempDir()
	_ = os.MkdirAll(filepath.Join(skillDir, "data"), 0o755)
	deps, _ := CheckRuntimeDependencies(skillDir, t.TempDir())
	for _, d := range deps {
		if d.Key == "skill_data_writable" {
			assert.Equal(t, "ok", d.Status)
		}
	}
	// The probe file must be cleaned up.
	entries, _ := os.ReadDir(filepath.Join(skillDir, "data"))
	assert.Empty(t, entries)
}

func TestRedactPresenceNeverRevealsValue(t *testing.T) {
	secret := "super-secret-production-token-998877"
	got := redactPresence(secret)
	assert.NotContains(t, got, "super-secret")
	assert.Contains(t, got, "8877")
	assert.Equal(t, "not configured", redactPresence(""))
}
