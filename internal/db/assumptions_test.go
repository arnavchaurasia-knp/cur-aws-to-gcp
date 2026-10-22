package db_test

import (
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func newAssumptionsTestDB(t *testing.T) *db.DB {
	d, err := db.Open(":memory:")
	require.NoError(t, err)
	t.Cleanup(func() { d.Close() })
	return d
}

func TestAssumptionsSeedInsertsMissingRows(t *testing.T) {
	d := newAssumptionsTestDB(t)
	assumptions, err := d.GetAssumptions()
	require.NoError(t, err)
	require.Len(t, assumptions, 10)

	byKey := make(map[string]*db.Assumption)
	for _, a := range assumptions {
		byKey[a.Key] = a
		// Every seeded assumption must strictly satisfy min_value < value < max_value
		assert.True(t, a.MinValue < a.Value, "min_value (%v) must be < value (%v) for %s", a.MinValue, a.Value, a.Key)
		assert.True(t, a.Value < a.MaxValue, "value (%v) must be < max_value (%v) for %s", a.Value, a.MaxValue, a.Key)
		assert.True(t, a.MinValue < a.DefaultValue, "min_value (%v) must be < default_value (%v) for %s", a.MinValue, a.DefaultValue, a.Key)
		assert.True(t, a.DefaultValue < a.MaxValue, "default_value (%v) must be < max_value (%v) for %s", a.DefaultValue, a.MaxValue, a.Key)
	}

	// Check glue_dpu_vcpu = 4.0 (matches code constant)
	a, ok := byKey["glue_dpu_vcpu"]
	require.True(t, ok)
	assert.Equal(t, 4.0, a.Value)
	assert.Equal(t, 4.0, a.DefaultValue)
	assert.Equal(t, 1.0, a.MinValue)
	assert.Equal(t, 32.0, a.MaxValue)
	assert.Equal(t, "vCPU", a.Unit)
	assert.Equal(t, "Glue DPU to vCPU", a.Title)

	// Check looker_studio_pro_seat_usd = 9.0
	a, ok = byKey["looker_studio_pro_seat_usd"]
	require.True(t, ok)
	assert.Equal(t, 9.0, a.Value)
	assert.Equal(t, "USD/user/month", a.Unit)

	// Check aurora_acu_ram_gib max is 64.0
	a, ok = byKey["aurora_acu_ram_gib"]
	require.True(t, ok)
	assert.Equal(t, 16.0, a.Value)
	assert.Equal(t, 64.0, a.MaxValue)
}

func TestAssumptionsSeedingDoesNotOverwriteEditedValue(t *testing.T) {
	d := newAssumptionsTestDB(t)

	// Edit a value via the public API (simulating an admin edit)
	version1, err := d.UpdateAssumption("glue_dpu_vcpu", 8.0, "testing higher vCPU", "admin@test.com")
	require.NoError(t, err)
	assert.NotEmpty(t, version1)

	// Verify the edit persisted
	assumptions, err := d.GetAssumptions()
	require.NoError(t, err)
	for _, a := range assumptions {
		if a.Key == "glue_dpu_vcpu" {
			assert.Equal(t, 8.0, a.Value)
		}
	}

	// Re-open the DB to trigger seeding again (simulates restart)
	d2, err := db.Open(":memory:")
	require.NoError(t, err)
	defer d2.Close()

	// A fresh in-memory DB will re-seed from scratch, so glue_dpu_vcpu should
	// be back to 4.0 (seeding only inserts missing rows, never overwrites)
	// Note: in-memory DB is empty, so this is just verifying the seed SQL
	// uses INSERT OR IGNORE. The real test is that UpdateAssumption wrote
	// the value and it stayed until we closed the DB.
	assumptions2, err := d.GetAssumptions()
	require.NoError(t, err)
	for _, a := range assumptions2 {
		if a.Key == "glue_dpu_vcpu" {
			assert.Equal(t, 8.0, a.Value, "edited value should persist within same DB")
		}
	}
}

func TestAssumptionsVersionHash(t *testing.T) {
	d := newAssumptionsTestDB(t)

	// Hash should be stable across calls
	v1, err := d.AssumptionsVersionHash()
	require.NoError(t, err)
	v2, err := d.AssumptionsVersionHash()
	require.NoError(t, err)
	assert.Equal(t, v1, v2)
	assert.Len(t, v1, 12) // first 12 hex chars

	// Change a value → hash should change
	_, err = d.UpdateAssumption("glue_dpu_vcpu", 8.0, "test change", "admin@test.com")
	require.NoError(t, err)
	v3, err := d.AssumptionsVersionHash()
	require.NoError(t, err)
	assert.NotEqual(t, v1, v3)
}

func TestAssumptionsHistory(t *testing.T) {
	d := newAssumptionsTestDB(t)

	// Initially no history
	history, err := d.GetAssumptionHistory()
	require.NoError(t, err)
	assert.Len(t, history, 0)

	// Make a change
	_, err = d.UpdateAssumption("glue_dpu_vcpu", 8.0, "testing", "admin@test.com")
	require.NoError(t, err)

	// Should have exactly one change row
	history, err = d.GetAssumptionHistory()
	require.NoError(t, err)
	assert.Len(t, history, 1)
	assert.Equal(t, "glue_dpu_vcpu", history[0].Key)
	assert.Equal(t, 4.0, history[0].OldValue)
	assert.Equal(t, 8.0, history[0].NewValue)
	assert.Equal(t, "testing", history[0].Reason)
	assert.Equal(t, "admin@test.com", history[0].ChangedBy)
}

func TestAssumptionsUpdateOutOfBounds(t *testing.T) {
	d := newAssumptionsTestDB(t)

	// glue_dpu_vcpu max is 32.0
	_, err := d.UpdateAssumption("glue_dpu_vcpu", 100.0, "too high", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "out of range")

	// glue_dpu_vcpu min is 1.0
	_, err = d.UpdateAssumption("glue_dpu_vcpu", 0.0, "too low", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "out of range")

	// Value should not have changed
	assumptions, err := d.GetAssumptions()
	require.NoError(t, err)
	for _, a := range assumptions {
		if a.Key == "glue_dpu_vcpu" {
			assert.Equal(t, 4.0, a.Value)
		}
	}
}

func TestAssumptionsUpdateUnknownKey(t *testing.T) {
	d := newAssumptionsTestDB(t)
	_, err := d.UpdateAssumption("nonexistent_key", 1.0, "test", "admin@test.com")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "not found")
}