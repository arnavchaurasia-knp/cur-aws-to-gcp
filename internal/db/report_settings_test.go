package db_test

import (
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func newReportSettingsTestDB(t *testing.T) *db.DB {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	t.Cleanup(func() { d.Close() })
	return d
}

func TestReportSettingsSeedInsertsMissingRows(t *testing.T) {
	d := newReportSettingsTestDB(t)
	settings, err := d.GetReportSettings()
	require.NoError(t, err)
	// Asserting a count breaks every time a setting is added. What matters is
	// that seeding produced rows and that each has a usable shape.
	require.NotEmpty(t, settings)

	byKey := make(map[string]*db.ReportSetting)
	for _, s := range settings {
		byKey[s.Key] = s
	}

	// Check col_gcp_cud_1yr = true
	s, ok := byKey["col_gcp_cud_1yr"]
	require.True(t, ok)
	assert.Equal(t, "true", s.Value)
	assert.Equal(t, "bool", s.ValueType)
	assert.Equal(t, "columns", s.GroupName)

	// Check row_min_cost_usd = 0 (number)
	s, ok = byKey["row_min_cost_usd"]
	require.True(t, ok)
	assert.Equal(t, "0", s.Value)
	assert.Equal(t, "number", s.ValueType)
	assert.NotNil(t, s.MinValue)
	assert.Equal(t, 0.0, *s.MinValue)
	assert.NotNil(t, s.MaxValue)
	assert.Equal(t, 1000000.0, *s.MaxValue)

	// Check section_assumptions = true and section_data_gaps = true
	sAssump, ok := byKey["section_assumptions"]
	require.True(t, ok)
	assert.Equal(t, "true", sAssump.Value)
	assert.Equal(t, "true", sAssump.DefaultValue)

	sGaps, ok := byKey["section_data_gaps"]
	require.True(t, ok)
	assert.Equal(t, "true", sGaps.Value)
	assert.Equal(t, "true", sGaps.DefaultValue)
}

func TestReportSettingsVersionHash(t *testing.T) {
	d := newReportSettingsTestDB(t)

	v1, err := d.ReportSettingsVersionHash()
	require.NoError(t, err)
	v2, err := d.ReportSettingsVersionHash()
	require.NoError(t, err)
	assert.Equal(t, v1, v2)
	assert.Len(t, v1, 12)

	// Change a value → hash should change
	_, err = d.UpdateReportSetting("col_gcp_cud_1yr", "false", "admin@test.com")
	require.NoError(t, err)
	v3, err := d.ReportSettingsVersionHash()
	require.NoError(t, err)
	assert.NotEqual(t, v1, v3)
}

func TestReportSettingsHistory(t *testing.T) {
	d := newReportSettingsTestDB(t)

	// Initially no history
	history, err := d.GetReportSettingHistory()
	require.NoError(t, err)
	assert.Len(t, history, 0)

	// Make a change
	_, err = d.UpdateReportSetting("col_gcp_cud_1yr", "false", "admin@test.com")
	require.NoError(t, err)

	// Should have exactly one change row
	history, err = d.GetReportSettingHistory()
	require.NoError(t, err)
	assert.Len(t, history, 1)
	assert.Equal(t, "col_gcp_cud_1yr", history[0].Key)
	assert.Equal(t, "true", history[0].OldValue)
	assert.Equal(t, "false", history[0].NewValue)
	assert.Equal(t, "admin@test.com", history[0].ChangedBy)
}

func TestReportSettingsUpdateOutOfBounds(t *testing.T) {
	d := newReportSettingsTestDB(t)

	// row_min_cost_usd max is 1000000.0
	_, err := d.UpdateReportSetting("row_min_cost_usd", "9999999", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "out of range")

	// row_min_cost_usd min is 0
	_, err = d.UpdateReportSetting("row_min_cost_usd", "-5", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "out of range")

	// Value should not have changed
	settings, err := d.GetReportSettings()
	require.NoError(t, err)
	for _, s := range settings {
		if s.Key == "row_min_cost_usd" {
			assert.Equal(t, "0", s.Value)
		}
	}
}

func TestReportSettingsUpdateInvalidBool(t *testing.T) {
	d := newReportSettingsTestDB(t)

	// col_gcp_cud_1yr is bool, "maybe" is invalid
	_, err := d.UpdateReportSetting("col_gcp_cud_1yr", "maybe", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "not a valid bool")
}

func TestReportSettingsUpdateUnknownKey(t *testing.T) {
	d := newReportSettingsTestDB(t)
	_, err := d.UpdateReportSetting("nonexistent_key", "true", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "not found")
}

func TestReportSettingsReset(t *testing.T) {
	d := newReportSettingsTestDB(t)

	// Change a value
	_, err := d.UpdateReportSetting("col_gcp_cud_1yr", "false", "admin@test.com")
	require.NoError(t, err)

	// Verify it changed
	settings, err := d.GetReportSettings()
	require.NoError(t, err)
	for _, s := range settings {
		if s.Key == "col_gcp_cud_1yr" {
			assert.Equal(t, "false", s.Value)
		}
	}

	// Reset
	version, err := d.ResetReportSettings("admin@test.com")
	require.NoError(t, err)
	assert.NotEmpty(t, version)

	// Verify it's back to default
	settings, err = d.GetReportSettings()
	require.NoError(t, err)
	for _, s := range settings {
		if s.Key == "col_gcp_cud_1yr" {
			assert.Equal(t, "true", s.Value)
		}
	}
}

func TestReportSettingsHistoryCapAt10(t *testing.T) {
	d := newReportSettingsTestDB(t)

	// Make 15 setting updates in sequence
	for i := 1; i <= 15; i++ {
		val := "true"
		if i%2 == 0 {
			val = "false"
		}
		_, err := d.UpdateReportSetting("col_gcp_cud_1yr", val, "tester@test.com")
		require.NoError(t, err)
	}

	history, err := d.GetReportSettingHistory()
	require.NoError(t, err)

	// Exactly 10 rows should be returned
	assert.Len(t, history, 10, "audit log should strictly retain at most 10 records")

	// Verify descending order (newest change first)
	for i := 0; i < len(history)-1; i++ {
		assert.GreaterOrEqual(t, history[i].ID, history[i+1].ID, "newer changes must have higher ID")
	}

	// 15th change was i=15 (val="true")
	assert.Equal(t, "true", history[0].NewValue)
}

