package db_test

import (
	"path/filepath"
	"testing"

	"github.com/facets/cur-web/internal/db"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestJobAnalytics_CRUD(t *testing.T) {
	dir := t.TempDir()
	database, err := db.Open(filepath.Join(dir, "test.db"))
	require.NoError(t, err)
	t.Cleanup(func() { _ = database.Close() })

	jobID := "job-analytics-test-1"
	err = database.CreateJob(jobID, "owner@example.com", "Acme Corp", "csv", "sess-1")
	require.NoError(t, err)

	odVal := 800.0
	c1Val := 600.0
	c3Val := 400.0
	savOD := 200.0
	savPct := 20.0

	rollup := &db.JobAnalytics{
		JobID:              jobID,
		Prospect:           "Acme Corp",
		Owner:              "owner@example.com",
		CreatedAt:          "2026-11-01 10:00:00",
		Currency:           "USD",
		AWSSpend:           1000.0,
		AWSWorkload:        950.0,
		GCPOD:              &odVal,
		GCP1yrCUD:          &c1Val,
		GCP3yrCUD:          &c3Val,
		SavingsOD:          &savOD,
		SavingsPctOD:       &savPct,
		TotalRows:          100,
		MappedRows:         80,
		PassthroughRows:    15,
		IgnoredRows:        5,
		LLMRows:            10,
		DeterministicRows:  70,
		UnmappedUSD:        50.0,
		CoveragePct:        95.0,
		ConfidenceBand:     "high",
		SPRIExposedUSD:     150.0,
		FindingsHigh:       1,
		FindingsMed:        2,
		FindingsLow:        3,
		MatrixDecidedPct:   85.0,
		CatalogVintage:     "2026-10-25",
		AssumptionsVersion: "hash123",
		EngineVersion:      "v1.2",
	}

	breakdowns := []db.JobAnalyticsBreakdown{
		{
			JobID:      jobID,
			Dimension:  "service",
			Key:        "AmazonEC2",
			AWSUSD:     700.0,
			GCPUSD:     550.0,
			SavingsUSD: 150.0,
			Rows:       50,
		},
		{
			JobID:      jobID,
			Dimension:  "charge_type",
			Key:        "compute",
			AWSUSD:     700.0,
			GCPUSD:     550.0,
			SavingsUSD: 150.0,
			Rows:       50,
		},
		{
			JobID:      jobID,
			Dimension:  "decision_type",
			Key:        "SKU",
			AWSUSD:     800.0,
			GCPUSD:     640.0,
			SavingsUSD: 160.0,
			Rows:       70,
		},
		{
			JobID:      jobID,
			Dimension:  "region",
			Key:        "us-east-1",
			AWSUSD:     1000.0,
			GCPUSD:     800.0,
			SavingsUSD: 200.0,
			Rows:       100,
		},
	}

	// 1. Upsert
	err = database.UpsertJobAnalytics(rollup, breakdowns)
	require.NoError(t, err)

	// 2. Read back
	got, err := database.GetJobAnalytics(jobID)
	require.NoError(t, err)
	assert.Equal(t, jobID, got.JobID)
	assert.Equal(t, "Acme Corp", got.Prospect)
	assert.InDelta(t, 1000.0, got.AWSSpend, 0.001)
	require.NotNil(t, got.GCPOD)
	assert.InDelta(t, 800.0, *got.GCPOD, 0.001)
	assert.Equal(t, "high", got.ConfidenceBand)

	// 3. Summary (Lens A)
	summary, err := database.GetAnalyticsSummary(db.AnalyticsFilter{})
	require.NoError(t, err)
	assert.Equal(t, 1, summary.AnalyzedJobs)
	assert.InDelta(t, 1000.0, summary.TotalAWSSpend, 0.001)
	assert.InDelta(t, 800.0, summary.TotalGCPOD, 0.001)
	assert.InDelta(t, 200.0, summary.TotalSavings, 0.001)
	assert.InDelta(t, 20.0, summary.SavingsPct, 0.001)
	assert.InDelta(t, 100.0, summary.WinRate, 0.001)
	assert.Len(t, summary.TopProspects, 1)

	// 4. Composition (Lens B)
	comp, err := database.GetAnalyticsComposition("service", db.AnalyticsFilter{})
	require.NoError(t, err)
	assert.Equal(t, "service", comp.Dimension)
	require.Len(t, comp.Items, 1)
	assert.Equal(t, "AmazonEC2", comp.Items[0].Key)
	assert.InDelta(t, 700.0, comp.Items[0].AWSUSD, 0.001)

	// 5. Quality (Lens C)
	quality, err := database.GetAnalyticsQuality(db.AnalyticsFilter{})
	require.NoError(t, err)
	assert.Equal(t, 100, quality.TotalRows)
	assert.Equal(t, 80, quality.MappedRows)
	assert.Equal(t, 10, quality.LLMRows)
	assert.Equal(t, 70, quality.DeterministicRows)
	assert.InDelta(t, 95.0, quality.CoveragePct, 0.001)
	assert.Equal(t, 1, quality.FindingsSummary.High)
	assert.Equal(t, 6, quality.FindingsSummary.Total)

	// 6. Methodology (Lens D)
	meth, err := database.GetAnalyticsMethodology(db.AnalyticsFilter{})
	require.NoError(t, err)
	assert.InDelta(t, 150.0, meth.SPRIExposedUSD, 0.001)
	assert.InDelta(t, 15.0, meth.SPRIExposedPct, 0.001)
	assert.InDelta(t, 85.0, meth.MatrixCoveragePct, 0.001)

	// 7. Delete / Archive
	err = database.ArchiveAndDeleteJob(jobID, "admin")
	require.NoError(t, err)
	_, err = database.GetJobAnalytics(jobID)
	assert.Error(t, err)
}
