package jobs

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func createStubAgy(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	var scriptPath string
	if runtime.GOOS == "windows" {
		scriptPath = filepath.Join(dir, "agy.bat")
		content := "@echo off\r\nif \"%~1\"==\"--version\" (\r\n  echo agy 1.2.3\r\n  exit /b 0\r\n)\r\necho OK\r\nexit /b 0\r\n"
		require.NoError(t, os.WriteFile(scriptPath, []byte(content), 0755))
	} else {
		scriptPath = filepath.Join(dir, "agy")
		content := "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then\n  echo \"agy 1.2.3\"\n  exit 0\nfi\necho \"OK\"\nexit 0\n"
		require.NoError(t, os.WriteFile(scriptPath, []byte(content), 0755))
	}
	return scriptPath
}

func TestProbeEngine_MissingBinary(t *testing.T) {
	InvalidateEngineProbeCache()
	spawner := NewSpawner(SpawnerConfig{
		AGYPath: filepath.Join(t.TempDir(), "nonexistent_agy_bin"),
	})
	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "fail", probe.State)
	assert.False(t, probe.BinaryFound)
	assert.Contains(t, probe.Detail, "not found on PATH")
}

func TestProbeEngine_MissingAuth(t *testing.T) {
	InvalidateEngineProbeCache()
	stub := createStubAgy(t)
	spawner := NewSpawner(SpawnerConfig{AGYPath: stub})

	orig := os.Getenv("ANTIGRAVITY_API_KEY")
	_ = os.Unsetenv("ANTIGRAVITY_API_KEY")
	defer func() {
		if orig != "" {
			_ = os.Setenv("ANTIGRAVITY_API_KEY", orig)
		}
	}()

	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "fail", probe.State)
	assert.False(t, probe.AuthPresent)
	assert.Contains(t, probe.Detail, "ANTIGRAVITY_API_KEY not set")
}

func TestProbeEngine_DisabledKillSwitch(t *testing.T) {
	InvalidateEngineProbeCache()
	stub := createStubAgy(t)
	spawner := NewSpawner(SpawnerConfig{AGYPath: stub})

	origKey := os.Getenv("ANTIGRAVITY_API_KEY")
	_ = os.Setenv("ANTIGRAVITY_API_KEY", "test-secret-key-12345")
	defer func() {
		if origKey != "" {
			_ = os.Setenv("ANTIGRAVITY_API_KEY", origKey)
		} else {
			_ = os.Unsetenv("ANTIGRAVITY_API_KEY")
		}
	}()

	origDisabled := os.Getenv("AGY_HEALTH_PROBE_DISABLED")
	_ = os.Setenv("AGY_HEALTH_PROBE_DISABLED", "1")
	defer func() {
		if origDisabled != "" {
			_ = os.Setenv("AGY_HEALTH_PROBE_DISABLED", origDisabled)
		} else {
			_ = os.Unsetenv("AGY_HEALTH_PROBE_DISABLED")
		}
	}()

	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "unknown", probe.State)
	assert.False(t, probe.RoundTrip)
	assert.Contains(t, probe.Detail, "live probe disabled")
}

func TestProbeEngine_SuccessWithStub(t *testing.T) {
	InvalidateEngineProbeCache()
	stub := createStubAgy(t)
	spawner := NewSpawner(SpawnerConfig{AGYPath: stub})

	origKey := os.Getenv("ANTIGRAVITY_API_KEY")
	_ = os.Setenv("ANTIGRAVITY_API_KEY", "test-secret-key-12345")
	defer func() {
		if origKey != "" {
			_ = os.Setenv("ANTIGRAVITY_API_KEY", origKey)
		} else {
			_ = os.Unsetenv("ANTIGRAVITY_API_KEY")
		}
	}()

	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "ok", probe.State)
	assert.True(t, probe.BinaryFound)
	assert.True(t, probe.AuthPresent)
	assert.True(t, probe.RoundTrip)
	assert.Contains(t, probe.Version, "1.2.3")
}
