package db_test

import (
	"testing"
	"time"

	"github.com/facets/cur-web/internal/db"
)

func TestEquivalenceUsageTableAndOperations(t *testing.T) {
	d, err := db.Open(":memory:")
	if err != nil {
		t.Fatalf("Open(:memory:) failed: %v", err)
	}
	defer d.Close()

	// Initial query should return empty slice without error
	records, err := d.GetEquivalenceUsage()
	if err != nil {
		t.Fatalf("GetEquivalenceUsage on empty db failed: %v", err)
	}
	if len(records) != 0 {
		t.Fatalf("expected 0 records, got %d", len(records))
	}

	// Insert test records
	now := time.Now().UTC().Format(time.RFC3339)
	testRecords := []db.EquivalenceUsageRecord{
		{
			JobID:      "job-1",
			Attribute:  "storage_class",
			AWSValue:   "glacier_flexible",
			GCPValue:   "coldline",
			Scope:      "storage_time",
			RowsUsed:   47,
			USDExposed: 412000.50,
			RecordedAt: now,
		},
		{
			JobID:      "job-1",
			Attribute:  "storage_class",
			AWSValue:   "standard",
			GCPValue:   "standard",
			Scope:      "storage_time",
			RowsUsed:   100,
			USDExposed: 1250.00,
			RecordedAt: now,
		},
	}

	if err := d.RecordEquivalenceUsage(testRecords); err != nil {
		t.Fatalf("RecordEquivalenceUsage failed: %v", err)
	}

	records, err = d.GetEquivalenceUsage()
	if err != nil {
		t.Fatalf("GetEquivalenceUsage failed: %v", err)
	}
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}

	foundGlacier := false
	for _, r := range records {
		if r.JobID == "job-1" && r.AWSValue == "glacier_flexible" && r.GCPValue == "coldline" {
			foundGlacier = true
			if r.RowsUsed != 47 {
				t.Errorf("expected 47 rows, got %d", r.RowsUsed)
			}
			if r.USDExposed != 412000.50 {
				t.Errorf("expected 412000.50 USD, got %f", r.USDExposed)
			}
		}
	}
	if !foundGlacier {
		t.Errorf("did not find inserted glacier_flexible record")
	}
}
