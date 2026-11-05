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

// createStubAgy writes a fake agy that prints a version or "OK". Tests never
// call the real model.
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

// isolateEngineEnv makes the spawner config path the one used (AGY_PATH from
// the developer's real environment would otherwise win) and sets the key.
func isolateEngineEnv(t *testing.T, apiKey string) {
	t.Helper()
	InvalidateEngineProbeCache()
	t.Setenv("AGY_PATH", "")
	t.Setenv("AGY_HEALTH_PROBE_DISABLED", "")
	t.Setenv("ANTIGRAVITY_API_KEY", apiKey)
}

func TestProbeEngine_MissingBinary(t *testing.T) {
	isolateEngineEnv(t, "test-secret-key-12345")
	spawner := NewSpawner(SpawnerConfig{AGYPath: filepath.Join(t.TempDir(), "nonexistent_agy_bin")})
	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "fail", probe.State)
	assert.False(t, probe.BinaryFound)
	assert.Contains(t, probe.Detail, "agy tool not found")
}

func TestProbeEngine_MissingAuth(t *testing.T) {
	isolateEngineEnv(t, "")
	spawner := NewSpawner(SpawnerConfig{AGYPath: createStubAgy(t)})
	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "fail", probe.State)
	assert.False(t, probe.AuthPresent)
	assert.Contains(t, probe.Detail, "ANTIGRAVITY_API_KEY is not set")
}

func TestProbeEngine_DisabledKillSwitch(t *testing.T) {
	isolateEngineEnv(t, "test-secret-key-12345")
	t.Setenv("AGY_HEALTH_PROBE_DISABLED", "1")
	spawner := NewSpawner(SpawnerConfig{AGYPath: createStubAgy(t)})
	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "unknown", probe.State)
	assert.False(t, probe.RoundTrip)
	assert.Contains(t, probe.Detail, "turned off")
}

func TestProbeEngine_SuccessWithStub(t *testing.T) {
	isolateEngineEnv(t, "test-secret-key-12345")
	spawner := NewSpawner(SpawnerConfig{AGYPath: createStubAgy(t)})
	probe := spawner.ProbeEngine(context.Background(), true)
	assert.Equal(t, "ok", probe.State)
	assert.True(t, probe.BinaryFound)
	assert.True(t, probe.AuthPresent)
	assert.True(t, probe.RoundTrip)
	assert.Contains(t, probe.Version, "1.2.3")
}

// The page-load check must never call the model: no round trip, state unknown.
func TestInspectEngine_DoesNotCallModel(t *testing.T) {
	isolateEngineEnv(t, "test-secret-key-12345")
	spawner := NewSpawner(SpawnerConfig{AGYPath: createStubAgy(t)})
	probe := spawner.InspectEngine(context.Background())
	assert.Equal(t, "unknown", probe.State)
	assert.False(t, probe.RoundTrip)
	assert.Zero(t, probe.LatencyMS)
	assert.True(t, probe.BinaryFound)
}

// AGY_PATH changed in Settings & Secrets must win over the startup config.
func TestAgyBin_LiveEnvWinsOverStartupConfig(t *testing.T) {
	stub := createStubAgy(t)
	spawner := NewSpawner(SpawnerConfig{AGYPath: "/startup/agy"})
	t.Setenv("AGY_PATH", stub)
	assert.Equal(t, stub, spawner.agyBin())
	t.Setenv("AGY_PATH", "")
	assert.Equal(t, "/startup/agy", spawner.agyBin())
}

func TestEngineHistory_NewestFirstAndCapped(t *testing.T) {
	dir := t.TempDir()
	for i := 0; i < engineHistoryMax+5; i++ {
		appendEngineHistory(dir, EngineTestRecord{EngineProbe: EngineProbe{State: "ok", LatencyMS: int64(i)}})
	}
	recs := loadEngineHistory(dir)
	require.Len(t, recs, engineHistoryMax)
	assert.Equal(t, int64(engineHistoryMax+4), recs[0].LatencyMS, "newest first")
}
