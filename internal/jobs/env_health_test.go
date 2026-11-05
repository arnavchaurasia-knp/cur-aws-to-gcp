package jobs

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCheckEnvDependencies_RedactionAndStatus(t *testing.T) {
	secretVal := "super-secret-production-token-998877"
	origAntigravity := os.Getenv("ANTIGRAVITY_API_KEY")
	origResend := os.Getenv("RESEND_API_KEY")
	origGCP := os.Getenv("GOOGLE_CLOUD_API_KEY")

	_ = os.Setenv("ANTIGRAVITY_API_KEY", secretVal)
	_ = os.Setenv("RESEND_API_KEY", secretVal)
	_ = os.Setenv("GOOGLE_CLOUD_API_KEY", secretVal)

	defer func() {
		if origAntigravity != "" {
			_ = os.Setenv("ANTIGRAVITY_API_KEY", origAntigravity)
		} else {
			_ = os.Unsetenv("ANTIGRAVITY_API_KEY")
		}
		if origResend != "" {
			_ = os.Setenv("RESEND_API_KEY", origResend)
		} else {
			_ = os.Unsetenv("RESEND_API_KEY")
		}
		if origGCP != "" {
			_ = os.Setenv("GOOGLE_CLOUD_API_KEY", origGCP)
		} else {
			_ = os.Unsetenv("GOOGLE_CLOUD_API_KEY")
		}
	}()

	tempSkill := t.TempDir()
	tempJobs := t.TempDir()
	_ = os.MkdirAll(tempSkill+"/data", 0755)

	deps, status := CheckEnvDependencies(tempSkill, tempJobs, "")
	assert.NotEmpty(t, deps)
	assert.NotEmpty(t, status)

	// Verify serialization NEVER leaks secretVal
	marshaled, err := json.Marshal(deps)
	require.NoError(t, err)
	assert.NotContains(t, string(marshaled), secretVal)

	// Verify each credential key is redacted
	for _, dep := range deps {
		if dep.Key == "ANTIGRAVITY_API_KEY" || dep.Key == "RESEND_API_KEY" || dep.Key == "GCP_CREDENTIALS" {
			assert.True(t, dep.Present)
			assert.Contains(t, dep.Detail, "configured")
			assert.NotContains(t, dep.Detail, secretVal)
		}
	}
}

func TestCheckEnvDependencies_MissingCredentials(t *testing.T) {
	origAntigravity := os.Getenv("ANTIGRAVITY_API_KEY")
	origResend := os.Getenv("RESEND_API_KEY")
	origGCP := os.Getenv("GOOGLE_CLOUD_API_KEY")

	_ = os.Unsetenv("ANTIGRAVITY_API_KEY")
	_ = os.Unsetenv("RESEND_API_KEY")
	_ = os.Unsetenv("GOOGLE_CLOUD_API_KEY")

	defer func() {
		if origAntigravity != "" {
			_ = os.Setenv("ANTIGRAVITY_API_KEY", origAntigravity)
		}
		if origResend != "" {
			_ = os.Setenv("RESEND_API_KEY", origResend)
		}
		if origGCP != "" {
			_ = os.Setenv("GOOGLE_CLOUD_API_KEY", origGCP)
		}
	}()

	tempSkill := t.TempDir()
	tempJobs := t.TempDir()
	_ = os.MkdirAll(tempSkill+"/data", 0755)

	deps, status := CheckEnvDependencies(tempSkill, tempJobs, "")
	assert.Equal(t, "fail", status) // ANTIGRAVITY_API_KEY missing is a hard fail

	for _, d := range deps {
		if d.Key == "ANTIGRAVITY_API_KEY" {
			assert.False(t, d.Present)
			assert.Equal(t, "fail", d.Status)
		}
		if d.Key == "RESEND_API_KEY" {
			assert.False(t, d.Present)
			assert.Equal(t, "warn", d.Status)
		}
	}
}
