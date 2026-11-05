package jobs_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/jobs"
	"github.com/facets/cur-web/internal/notify"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCatalogRefresher_StalePinDetectionAndNotifications(t *testing.T) {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d.Close()

	jobsDir := t.TempDir()
	adminEmails := []string{"admin@facets.cloud"}
	h := jobs.NewHandler(d, jobsDir, nil, nil, jobs.NotifyConfig{
		Email: notify.EmailConfig{
			APIKey:  "test-dummy-key",
			BaseURL: "http://localhost:8080",
		},
	}, adminEmails)

	aliceEmail := "alice@company.com"
	bobEmail := "bob@competitor.com"

	// Alice has notifications enabled
	require.NoError(t, d.UpsertUserSeen(aliceEmail, "viewer"))
	require.NoError(t, d.UpdateUserPreferences(aliceEmail, "gemini-3.8-flash", "csv", true))

	// Bob has notifications disabled
	require.NoError(t, d.UpsertUserSeen(bobEmail, "viewer"))
	require.NoError(t, d.UpdateUserPreferences(bobEmail, "gemini-3.8-flash", "csv", false))

	// Setup Job 1 for Alice with a stale directive (T2A pinned in africa-south1 where it is unavailable)
	jobAliceID := "11111111-1111-1111-1111-111111111111"
	jobAliceDir := filepath.Join(jobsDir, jobAliceID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobAliceDir, "directives"), 0755))
	require.NoError(t, os.MkdirAll(filepath.Join(jobAliceDir, "projection-audit"), 0755))

	dAlice1 := `[
		{"id":"d_alice_t2a","author":"alice@company.com","selector":{"service":"ec2","region":"africa-south1"},"action":{"type":"set_target_family","value":"T2A","params":{"region":"africa-south1"}},"status":"confirmed","confidence":1.0},
		{"id":"d_alice_t2a_2","author":"alice@company.com","selector":{"service":"ec2","region":"africa-south1"},"action":{"type":"set_target_family","value":"T2A","params":{"region":"africa-south1"}},"status":"confirmed","confidence":1.0}
	]`
	require.NoError(t, os.WriteFile(filepath.Join(jobAliceDir, "directives", "alice@company.com.json"), []byte(dAlice1), 0644))

	// Setup Job 2 for Bob with a stale directive
	jobBobID := "22222222-2222-2222-2222-222222222222"
	jobBobDir := filepath.Join(jobsDir, jobBobID)
	require.NoError(t, os.MkdirAll(filepath.Join(jobBobDir, "directives"), 0755))
	require.NoError(t, os.MkdirAll(filepath.Join(jobBobDir, "projection-audit"), 0755))

	dBob := `[
		{"id":"d_bob_t2a","author":"bob@competitor.com","selector":{"service":"ec2","region":"africa-south1"},"action":{"type":"set_target_family","value":"T2A","params":{"region":"africa-south1"}},"status":"confirmed","confidence":1.0}
	]`
	require.NoError(t, os.WriteFile(filepath.Join(jobBobDir, "directives", "bob@competitor.com.json"), []byte(dBob), 0644))

	t.Run("RevalidateDirectivesAfterRefresh detects stale pins", func(t *testing.T) {
		results, err := h.RevalidateDirectivesAfterRefresh()
		require.NoError(t, err)
		require.NotEmpty(t, results)

		// Assert both Alice and Bob had stale pins detected
		var alicePins, bobPins []jobs.StalePinResult
		for _, r := range results {
			if r.UserID == aliceEmail {
				alicePins = append(alicePins, r)
			} else if r.UserID == bobEmail {
				bobPins = append(bobPins, r)
			}
		}

		assert.Len(t, alicePins, 2, "Alice had 2 stale pins (batched)")
		assert.Len(t, bobPins, 1, "Bob had 1 stale pin")

		// Verify clarification written to Alice's thread
		aliceThreadPath := filepath.Join(jobAliceDir, "refinement_threads", "alice@company.com.json")
		if _, err := os.Stat(aliceThreadPath); err != nil {
			aliceThreadPath = filepath.Join(jobAliceDir, "refinement_thread.json")
		}
		require.FileExists(t, aliceThreadPath)
		dataAlice, err := os.ReadFile(aliceThreadPath)
		require.NoError(t, err)

		var threadAlice struct {
			OpenClarifications []struct {
				ID           string `json:"id"`
				Reason       string `json:"reason"`
				Blocking     bool   `json:"blocking"`
				DirectiveRef string `json:"directive_ref"`
			} `json:"open_clarifications"`
		}
		require.NoError(t, json.Unmarshal(dataAlice, &threadAlice))
		require.NotEmpty(t, threadAlice.OpenClarifications)
		assert.Equal(t, "catalog_refresh_invalidated", threadAlice.OpenClarifications[0].Reason)
		assert.True(t, threadAlice.OpenClarifications[0].Blocking)

		// Verify Bob's notification preference (disabled) was checked
		prefsBob, err := d.GetUserPreferences(bobEmail)
		require.NoError(t, err)
		assert.False(t, prefsBob.EmailNotifications, "Bob has notifications disabled")

		prefsAlice, err := d.GetUserPreferences(aliceEmail)
		require.NoError(t, err)
		assert.True(t, prefsAlice.EmailNotifications, "Alice has notifications enabled")
	})
}
