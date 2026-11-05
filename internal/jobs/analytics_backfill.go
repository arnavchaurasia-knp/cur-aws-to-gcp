package jobs

import (
	"log"
	"log/slog"
	"os"
	"path/filepath"

	"github.com/facets/cur-web/internal/db"
)

// BackfillJobAnalytics walks jobs and materializes job_analytics for any missing rows.
// If forceAll is true, it re-derives the rollup for every done job.
func BackfillJobAnalytics(database *db.DB, jobsDir string, forceAll bool) (int, error) {
	if database == nil {
		return 0, nil
	}

	var jobIDs []string
	if forceAll {
		// Fetch all done jobs
		rows, err := database.ListAllJobs()
		if err != nil {
			return 0, err
		}
		for _, j := range rows {
			if j.Status == "done" {
				jobIDs = append(jobIDs, j.ID)
			}
		}
	} else {
		var err error
		jobIDs, err = database.ListJobIDsForAnalyticsBackfill()
		if err != nil {
			return 0, err
		}
	}

	count := 0
	for _, id := range jobIDs {
		jobDir := filepath.Join(jobsDir, id)
		if _, err := os.Stat(jobDir); err != nil {
			continue
		}

		job, err := database.GetJob(id)
		if err != nil || job == nil {
			continue
		}

		rollup, breakdowns, err := ComputeJobAnalytics(job, jobDir, database)
		if err != nil {
			slog.Warn("analytics backfill failed for job", "job_id", id, "err", err)
			continue
		}

		if err := database.UpsertJobAnalytics(rollup, breakdowns); err != nil {
			slog.Warn("analytics upsert failed for job", "job_id", id, "err", err)
			continue
		}
		count++
	}

	log.Printf("analytics backfill: materialized %d jobs", count)
	return count, nil
}
