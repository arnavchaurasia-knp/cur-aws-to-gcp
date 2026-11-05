package db

import (
	"database/sql"
	"fmt"
	"sort"
	"strings"
	"time"
)

// JobAnalytics represents one row of the materialized job_analytics table.
type JobAnalytics struct {
	JobID              string   `json:"job_id"`
	Prospect           string   `json:"prospect"`
	Owner              string   `json:"owner"`
	CreatedAt          string   `json:"created_at"`
	Currency           string   `json:"currency"`
	AWSSpend           float64  `json:"aws_spend"`
	AWSWorkload        float64  `json:"aws_workload"`
	GCPOD              *float64 `json:"gcp_od,omitempty"`
	GCP1yrCUD          *float64 `json:"gcp_1yr_cud,omitempty"`
	GCP3yrCUD          *float64 `json:"gcp_3yr_cud,omitempty"`
	SavingsOD          *float64 `json:"savings_od,omitempty"`
	SavingsPctOD       *float64 `json:"savings_pct_od,omitempty"`
	TotalRows          int      `json:"total_rows"`
	MappedRows         int      `json:"mapped_rows"`
	PassthroughRows    int      `json:"passthrough_rows"`
	IgnoredRows        int      `json:"ignored_rows"`
	LLMRows            int      `json:"llm_rows"`
	DeterministicRows  int      `json:"deterministic_rows"`
	UnmappedUSD        float64  `json:"unmapped_usd"`
	CoveragePct        float64  `json:"coverage_pct"`
	ConfidenceBand     string   `json:"confidence_band"`
	SPRIExposedUSD     float64  `json:"sp_ri_exposed_usd"`
	FindingsHigh       int      `json:"findings_high"`
	FindingsMed        int      `json:"findings_med"`
	FindingsLow        int      `json:"findings_low"`
	MatrixDecidedPct   float64  `json:"matrix_decided_pct"`
	CatalogVintage     string   `json:"catalog_vintage"`
	AssumptionsVersion string   `json:"assumptions_version"`
	EngineVersion      string   `json:"engine_version"`
	RolledUpAt         string   `json:"rolled_up_at"`
}

// JobAnalyticsBreakdown represents a dimension breakdown for a job.
type JobAnalyticsBreakdown struct {
	JobID      string  `json:"job_id"`
	Dimension  string  `json:"dimension"` // 'service' | 'charge_type' | 'decision_type' | 'region'
	Key        string  `json:"key"`
	AWSUSD     float64 `json:"aws_usd"`
	GCPUSD     float64 `json:"gcp_usd"`
	SavingsUSD float64 `json:"savings_usd"`
	Rows       int     `json:"rows"`
}

// AnalyticsFilter specifies filter criteria across all analytics endpoints.
type AnalyticsFilter struct {
	Window   string  // "30d", "90d", "all", or empty
	From     string  // YYYY-MM-DD
	To       string  // YYYY-MM-DD
	MinBill  float64 // Minimum AWS spend
	Region   string  // Regional filter
	Vertical string  // Reserved for industry vertical
}

// AnalyticsSummary extends UsageStats with deal economics (Lens A).
type AnalyticsSummary struct {
	// Operational throughput (secondary/footnote)
	TotalJobs            int         `json:"total_jobs"`
	DoneJobs             int         `json:"done_jobs"`
	FailedJobs           int         `json:"failed_jobs"`
	FailureRate          float64     `json:"failure_rate"`
	AvgProcessingSec     float64     `json:"avg_processing_seconds"`
	JobsByDay            []DayCount  `json:"jobs_by_day"`

	// Economics (headline)
	AnalyzedJobs         int               `json:"analyzed_jobs"`
	TotalAWSSpend        float64           `json:"total_aws_spend"`
	TotalAWSWorkload     float64           `json:"total_aws_workload"`
	TotalGCPOD           float64           `json:"total_gcp_od"`
	TotalGCP1yrCUD       float64           `json:"total_gcp_1yr_cud"`
	TotalGCP3yrCUD       float64           `json:"total_gcp_3yr_cud"`
	TotalSavings         float64           `json:"total_savings"`
	SavingsPct           float64           `json:"savings_pct"`
	Savings1yrCUD        float64           `json:"savings_1yr_cud"`
	SavingsPct1yrCUD     float64           `json:"savings_pct_1yr_cud"`
	Savings3yrCUD        float64           `json:"savings_3yr_cud"`
	SavingsPct3yrCUD     float64           `json:"savings_pct_3yr_cud"`
	AvgSavingsPct        float64           `json:"avg_savings_pct"`
	AvgBillSize          float64           `json:"avg_bill_size"`
	WinRate              float64           `json:"win_rate"`
	WinRate20            float64           `json:"win_rate_20"`
	PortfolioCoveragePct float64           `json:"portfolio_coverage_pct"`
	TotalUnmappedUSD     float64           `json:"total_unmapped_usd"`
	SpendByDay           []SpendDay        `json:"spend_by_day"`
	TopProspects         []ProspectSaving  `json:"top_prospects"`
	SavingsBuckets       []SavingsBucket   `json:"savings_buckets"`
}

// CompositionItem represents an aggregated item in spend composition (Lens B).
type CompositionItem struct {
	Key        string  `json:"key"`
	AWSUSD     float64 `json:"aws_usd"`
	GCPUSD     float64 `json:"gcp_usd"`
	SavingsUSD float64 `json:"savings_usd"`
	SavingsPct float64 `json:"savings_pct"`
	Rows       int     `json:"rows"`
	PctOfAWS   float64 `json:"pct_of_aws"`
}

// CompositionResult holds aggregated composition breakdowns and negative savings items.
type CompositionResult struct {
	Dimension       string            `json:"dimension"`
	Items           []CompositionItem `json:"items"`
	NegativeSavings []CompositionItem `json:"negative_savings"`
}

// ConfidenceItem holds spend and count by confidence level.
type ConfidenceItem struct {
	Band     string  `json:"band"`
	Count    int     `json:"count"`
	AWSUSD   float64 `json:"aws_usd"`
	PctOfUSD float64 `json:"pct_of_usd"`
}

// FindingsSummary aggregates review findings by severity.
type FindingsSummary struct {
	High  int `json:"high"`
	Med   int `json:"med"`
	Low   int `json:"low"`
	Total int `json:"total"`
}

// VintageItem aggregates catalog vintages.
type VintageItem struct {
	Vintage string `json:"vintage"`
	Count   int    `json:"count"`
}

// QualityResult aggregates projection quality metrics (Lens C).
type QualityResult struct {
	TotalRows              int              `json:"total_rows"`
	MappedRows             int              `json:"mapped_rows"`
	PassthroughRows        int              `json:"passthrough_rows"`
	IgnoredRows            int              `json:"ignored_rows"`
	LLMRows                int              `json:"llm_rows"`
	DeterministicRows      int              `json:"deterministic_rows"`
	DeterministicPct       float64          `json:"deterministic_pct"`
	LLMPct                 float64          `json:"llm_pct"`
	PassthroughPct         float64          `json:"passthrough_pct"`
	IgnoredPct             float64          `json:"ignored_pct"`
	TotalUnmappedUSD       float64          `json:"total_unmapped_usd"`
	CoveragePct            float64          `json:"coverage_pct"`
	ConfidenceDistribution []ConfidenceItem `json:"confidence_distribution"`
	FindingsSummary        FindingsSummary  `json:"findings_summary"`
	CatalogVintages        []VintageItem    `json:"catalog_vintages"`
}

// ReviewLoopThroughput tracks status of advisory review findings.
type ReviewLoopThroughput struct {
	TotalFindings int `json:"total_findings"`
	Pending       int `json:"pending"`
	Accepted      int `json:"accepted"`
	Rejected      int `json:"rejected"`
	Dismissed     int `json:"dismissed"`
}

// VersionCountItem holds counts of jobs per version.
type VersionCountItem struct {
	Version string `json:"version"`
	Count   int    `json:"count"`
}

// MethodologyResult aggregates methodology and trust metrics (Lens D).
type MethodologyResult struct {
	MatrixCoveragePct    float64              `json:"matrix_coverage_pct"`
	SPRIExposedUSD       float64              `json:"sp_ri_exposed_usd"`
	SPRIExposedPct       float64              `json:"sp_ri_exposed_pct"`
	ReviewLoopThroughput ReviewLoopThroughput `json:"review_loop_throughput"`
	AssumptionsVersions  []VersionCountItem   `json:"assumptions_versions"`
	EngineVersions       []VersionCountItem   `json:"engine_versions"`
}

// UpsertJobAnalytics writes or updates a job's rollup row and its breakdowns in a transaction.
func (d *DB) UpsertJobAnalytics(row *JobAnalytics, breakdowns []JobAnalyticsBreakdown) error {
	tx, err := d.conn.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if row.RolledUpAt == "" {
		row.RolledUpAt = time.Now().UTC().Format(time.RFC3339)
	}

	_, err = tx.Exec(`
		INSERT INTO job_analytics (
			job_id, prospect, owner, created_at, currency,
			aws_spend, aws_workload, gcp_od, gcp_1yr_cud, gcp_3yr_cud,
			savings_od, savings_pct_od,
			total_rows, mapped_rows, passthrough_rows, ignored_rows,
			llm_rows, deterministic_rows, unmapped_usd, coverage_pct, confidence_band,
			sp_ri_exposed_usd, findings_high, findings_med, findings_low,
			matrix_decided_pct, catalog_vintage, assumptions_version, engine_version, rolled_up_at
		) VALUES (
			?, ?, ?, ?, ?,
			?, ?, ?, ?, ?,
			?, ?,
			?, ?, ?, ?,
			?, ?, ?, ?, ?,
			?, ?, ?, ?,
			?, ?, ?, ?, ?
		)
		ON CONFLICT(job_id) DO UPDATE SET
			prospect = excluded.prospect,
			owner = excluded.owner,
			created_at = excluded.created_at,
			currency = excluded.currency,
			aws_spend = excluded.aws_spend,
			aws_workload = excluded.aws_workload,
			gcp_od = excluded.gcp_od,
			gcp_1yr_cud = excluded.gcp_1yr_cud,
			gcp_3yr_cud = excluded.gcp_3yr_cud,
			savings_od = excluded.savings_od,
			savings_pct_od = excluded.savings_pct_od,
			total_rows = excluded.total_rows,
			mapped_rows = excluded.mapped_rows,
			passthrough_rows = excluded.passthrough_rows,
			ignored_rows = excluded.ignored_rows,
			llm_rows = excluded.llm_rows,
			deterministic_rows = excluded.deterministic_rows,
			unmapped_usd = excluded.unmapped_usd,
			coverage_pct = excluded.coverage_pct,
			confidence_band = excluded.confidence_band,
			sp_ri_exposed_usd = excluded.sp_ri_exposed_usd,
			findings_high = excluded.findings_high,
			findings_med = excluded.findings_med,
			findings_low = excluded.findings_low,
			matrix_decided_pct = excluded.matrix_decided_pct,
			catalog_vintage = excluded.catalog_vintage,
			assumptions_version = excluded.assumptions_version,
			engine_version = excluded.engine_version,
			rolled_up_at = excluded.rolled_up_at
	`,
		row.JobID, row.Prospect, row.Owner, row.CreatedAt, row.Currency,
		row.AWSSpend, row.AWSWorkload, row.GCPOD, row.GCP1yrCUD, row.GCP3yrCUD,
		row.SavingsOD, row.SavingsPctOD,
		row.TotalRows, row.MappedRows, row.PassthroughRows, row.IgnoredRows,
		row.LLMRows, row.DeterministicRows, row.UnmappedUSD, row.CoveragePct, row.ConfidenceBand,
		row.SPRIExposedUSD, row.FindingsHigh, row.FindingsMed, row.FindingsLow,
		row.MatrixDecidedPct, row.CatalogVintage, row.AssumptionsVersion, row.EngineVersion, row.RolledUpAt,
	)
	if err != nil {
		return fmt.Errorf("upsert job_analytics: %w", err)
	}

	_, err = tx.Exec(`DELETE FROM job_analytics_breakdown WHERE job_id = ?`, row.JobID)
	if err != nil {
		return fmt.Errorf("clear breakdown: %w", err)
	}

	if len(breakdowns) > 0 {
		stmt, err := tx.Prepare(`
			INSERT INTO job_analytics_breakdown (job_id, dimension, key, aws_usd, gcp_usd, savings_usd, rows)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		`)
		if err != nil {
			return fmt.Errorf("prepare breakdown insert: %w", err)
		}
		defer stmt.Close()

		for _, b := range breakdowns {
			if _, err := stmt.Exec(row.JobID, b.Dimension, b.Key, b.AWSUSD, b.GCPUSD, b.SavingsUSD, b.Rows); err != nil {
				return fmt.Errorf("insert breakdown row: %w", err)
			}
		}
	}

	return tx.Commit()
}

// DeleteJobAnalytics deletes analytics rollup and breakdowns for a given job.
func (d *DB) DeleteJobAnalytics(jobID string) error {
	tx, err := d.conn.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	_, _ = tx.Exec(`DELETE FROM job_analytics_breakdown WHERE job_id = ?`, jobID)
	_, err = tx.Exec(`DELETE FROM job_analytics WHERE job_id = ?`, jobID)
	if err != nil {
		return err
	}
	return tx.Commit()
}

// GetJobAnalytics retrieves a single job's rollup row.
func (d *DB) GetJobAnalytics(jobID string) (*JobAnalytics, error) {
	row := &JobAnalytics{}
	var gcpOD, gcp1, gcp3, savOD, savPct sql.NullFloat64
	err := d.conn.QueryRow(`
		SELECT job_id, prospect, owner, created_at, currency,
		       aws_spend, aws_workload, gcp_od, gcp_1yr_cud, gcp_3yr_cud,
		       savings_od, savings_pct_od,
		       total_rows, mapped_rows, passthrough_rows, ignored_rows,
		       llm_rows, deterministic_rows, unmapped_usd, coverage_pct, confidence_band,
		       sp_ri_exposed_usd, findings_high, findings_med, findings_low,
		       matrix_decided_pct, catalog_vintage, assumptions_version, engine_version, rolled_up_at
		FROM job_analytics
		WHERE job_id = ?
	`, jobID).Scan(
		&row.JobID, &row.Prospect, &row.Owner, &row.CreatedAt, &row.Currency,
		&row.AWSSpend, &row.AWSWorkload, &gcpOD, &gcp1, &gcp3,
		&savOD, &savPct,
		&row.TotalRows, &row.MappedRows, &row.PassthroughRows, &row.IgnoredRows,
		&row.LLMRows, &row.DeterministicRows, &row.UnmappedUSD, &row.CoveragePct, &row.ConfidenceBand,
		&row.SPRIExposedUSD, &row.FindingsHigh, &row.FindingsMed, &row.FindingsLow,
		&row.MatrixDecidedPct, &row.CatalogVintage, &row.AssumptionsVersion, &row.EngineVersion, &row.RolledUpAt,
	)
	if err != nil {
		return nil, err
	}
	if gcpOD.Valid { row.GCPOD = &gcpOD.Float64 }
	if gcp1.Valid { row.GCP1yrCUD = &gcp1.Float64 }
	if gcp3.Valid { row.GCP3yrCUD = &gcp3.Float64 }
	if savOD.Valid { row.SavingsOD = &savOD.Float64 }
	if savPct.Valid { row.SavingsPctOD = &savPct.Float64 }
	return row, nil
}

// ListJobIDsForAnalyticsBackfill returns all done jobs lacking a job_analytics row.
func (d *DB) ListJobIDsForAnalyticsBackfill() ([]string, error) {
	rows, err := d.conn.Query(`
		SELECT id FROM jobs
		WHERE status = 'done'
		  AND id NOT IN (SELECT job_id FROM job_analytics)
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err == nil {
			ids = append(ids, id)
		}
	}
	return ids, rows.Err()
}

// buildFilterClauses translates an AnalyticsFilter to SQL WHERE fragments and arguments.
func buildFilterClauses(prefix string, filter AnalyticsFilter) (string, []any) {
	var clauses []string
	var args []any

	colCreated := "created_at"
	colAWS := "aws_spend"
	if prefix != "" {
		colCreated = prefix + ".created_at"
		colAWS = prefix + ".aws_spend"
	}

	if filter.Window != "" && filter.Window != "all" {
		switch strings.ToLower(filter.Window) {
		case "30d":
			clauses = append(clauses, fmt.Sprintf("%s >= datetime('now', '-30 days')", colCreated))
		case "90d":
			clauses = append(clauses, fmt.Sprintf("%s >= datetime('now', '-90 days')", colCreated))
		case "180d":
			clauses = append(clauses, fmt.Sprintf("%s >= datetime('now', '-180 days')", colCreated))
		case "1y", "365d":
			clauses = append(clauses, fmt.Sprintf("%s >= datetime('now', '-365 days')", colCreated))
		}
	}

	if filter.From != "" {
		clauses = append(clauses, fmt.Sprintf("%s >= ?", colCreated))
		args = append(args, filter.From)
	}
	if filter.To != "" {
		clauses = append(clauses, fmt.Sprintf("%s <= ?", colCreated))
		args = append(args, filter.To+" 23:59:59")
	}
	if filter.MinBill > 0 {
		clauses = append(clauses, fmt.Sprintf("%s >= ?", colAWS))
		args = append(args, filter.MinBill)
	}

	if len(clauses) == 0 {
		return "", args
	}
	return " AND " + strings.Join(clauses, " AND "), args
}

// GetAnalyticsSummary computes portfolio economics (Lens A) with filters.
func (d *DB) GetAnalyticsSummary(filter AnalyticsFilter) (*AnalyticsSummary, error) {
	stats := &AnalyticsSummary{}

	// Basic operational metrics
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM jobs`).Scan(&stats.TotalJobs)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM jobs WHERE status='done'`).Scan(&stats.DoneJobs)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM jobs WHERE status='failed'`).Scan(&stats.FailedJobs)
	if stats.TotalJobs > 0 {
		stats.FailureRate = float64(stats.FailedJobs) / float64(stats.TotalJobs)
	}
	var avgSec sql.NullFloat64
	_ = d.conn.QueryRow(`SELECT AVG((julianday(updated_at) - julianday(created_at)) * 86400.0) FROM jobs WHERE status='done'`).Scan(&avgSec)
	if avgSec.Valid {
		stats.AvgProcessingSec = avgSec.Float64
	}

	// Daily projection volume
	vRows, err := d.conn.Query(`
		SELECT date(created_at) as day, COUNT(*) FROM jobs
		WHERE created_at >= datetime('now', '-30 days')
		GROUP BY day ORDER BY day
	`)
	if err == nil {
		for vRows.Next() {
			var dc DayCount
			if err := vRows.Scan(&dc.Day, &dc.Count); err == nil {
				stats.JobsByDay = append(stats.JobsByDay, dc)
			}
		}
		vRows.Close()
	}

	// Check if job_analytics table has rows
	var analyticsCount int
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM job_analytics`).Scan(&analyticsCount)

	if analyticsCount == 0 {
		// Fallback to legacy jobs table calculation
		legacyStats, err := d.GetUsageStats()
		if err != nil {
			return nil, err
		}
		stats.AnalyzedJobs = legacyStats.AnalyzedJobs
		stats.TotalAWSSpend = legacyStats.TotalAWSSpend
		stats.TotalGCPOD = legacyStats.TotalGCPOD
		stats.TotalSavings = legacyStats.TotalSavings
		stats.SavingsPct = legacyStats.SavingsPct
		stats.AvgSavingsPct = legacyStats.AvgSavingsPct
		stats.AvgBillSize = legacyStats.AvgBillSize
		stats.SpendByDay = legacyStats.SpendByDay
		stats.TopProspects = legacyStats.TopProspects
		stats.SavingsBuckets = legacyStats.SavingsBuckets
		return stats, nil
	}

	// Query job_analytics with filter
	whereClause, args := buildFilterClauses("", filter)
	query := fmt.Sprintf(`
		SELECT job_id, prospect, created_at, aws_spend, aws_workload,
		       gcp_od, gcp_1yr_cud, gcp_3yr_cud, savings_od, savings_pct_od,
		       coverage_pct, unmapped_usd
		FROM job_analytics
		WHERE aws_spend > 0 %s
	`, whereClause)

	rows, err := d.conn.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("query job_analytics: %w", err)
	}
	defer rows.Close()

	type bucket struct {
		label string
		lo    float64
		hi    float64
	}
	buckets := []bucket{
		{"<0% (costs more)", -1e18, 0},
		{"0–20%", 0, 20},
		{"20–40%", 20, 40},
		{"40–60%", 40, 60},
		{"60%+", 60, 1e18},
	}
	counts := make([]int, len(buckets))

	var sumSavingsPct float64
	var totalCoverageSum float64
	var winCount, win20Count int
	var allProspects []ProspectSaving

	for rows.Next() {
		var jobID, prospect, createdAt string
		var awsSpend, awsWorkload, coveragePct, unmappedUSD float64
		var gcpOD, gcp1, gcp3, savOD, savPct sql.NullFloat64

		if err := rows.Scan(
			&jobID, &prospect, &createdAt, &awsSpend, &awsWorkload,
			&gcpOD, &gcp1, &gcp3, &savOD, &savPct,
			&coveragePct, &unmappedUSD,
		); err != nil {
			return nil, err
		}

		stats.AnalyzedJobs++
		stats.TotalAWSSpend += awsSpend
		stats.TotalAWSWorkload += awsWorkload
		stats.TotalUnmappedUSD += unmappedUSD
		totalCoverageSum += coveragePct

		gcpVal := awsSpend
		if gcpOD.Valid {
			gcpVal = gcpOD.Float64
		}
		stats.TotalGCPOD += gcpVal

		if gcp1.Valid {
			stats.TotalGCP1yrCUD += gcp1.Float64
		} else {
			stats.TotalGCP1yrCUD += gcpVal * 0.70
		}

		if gcp3.Valid {
			stats.TotalGCP3yrCUD += gcp3.Float64
		} else {
			stats.TotalGCP3yrCUD += gcpVal * 0.50
		}

		savings := awsSpend - gcpVal
		pct := 0.0
		if awsSpend > 0 {
			pct = (savings / awsSpend) * 100
		}
		sumSavingsPct += pct

		if pct > 0 {
			winCount++
		}
		if pct >= 20 {
			win20Count++
		}

		for i, b := range buckets {
			if pct >= b.lo && pct < b.hi {
				counts[i]++
				break
			}
		}

		allProspects = append(allProspects, ProspectSaving{
			Prospect:   prospect,
			AWSSpend:   awsSpend,
			GCPOD:      gcpVal,
			Savings:    savings,
			SavingsPct: pct,
			CreatedAt:  createdAt,
		})
	}

	if stats.AnalyzedJobs > 0 {
		stats.TotalSavings = stats.TotalAWSSpend - stats.TotalGCPOD
		if stats.TotalAWSSpend > 0 {
			stats.SavingsPct = (stats.TotalSavings / stats.TotalAWSSpend) * 100
		}
		stats.Savings1yrCUD = stats.TotalAWSSpend - stats.TotalGCP1yrCUD
		if stats.TotalAWSSpend > 0 {
			stats.SavingsPct1yrCUD = (stats.Savings1yrCUD / stats.TotalAWSSpend) * 100
		}
		stats.Savings3yrCUD = stats.TotalAWSSpend - stats.TotalGCP3yrCUD
		if stats.TotalAWSSpend > 0 {
			stats.SavingsPct3yrCUD = (stats.Savings3yrCUD / stats.TotalAWSSpend) * 100
		}
		stats.AvgSavingsPct = sumSavingsPct / float64(stats.AnalyzedJobs)
		stats.AvgBillSize = stats.TotalAWSSpend / float64(stats.AnalyzedJobs)
		stats.WinRate = (float64(winCount) / float64(stats.AnalyzedJobs)) * 100
		stats.WinRate20 = (float64(win20Count) / float64(stats.AnalyzedJobs)) * 100
		stats.PortfolioCoveragePct = totalCoverageSum / float64(stats.AnalyzedJobs)
	}

	for i, b := range buckets {
		stats.SavingsBuckets = append(stats.SavingsBuckets, SavingsBucket{Label: b.label, Count: counts[i]})
	}

	// Leaderboard by savings $
	sort.Slice(allProspects, func(i, j int) bool { return allProspects[i].Savings > allProspects[j].Savings })
	if len(allProspects) > 10 {
		allProspects = allProspects[:10]
	}
	stats.TopProspects = allProspects

	// Spend by day (trend)
	dayQuery := fmt.Sprintf(`
		SELECT date(created_at) AS day,
		       COALESCE(SUM(aws_spend), 0),
		       COALESCE(SUM(COALESCE(gcp_od, aws_spend)), 0)
		FROM job_analytics
		WHERE aws_spend > 0 %s
		GROUP BY day ORDER BY day
	`, whereClause)
	dRows, err := d.conn.Query(dayQuery, args...)
	if err == nil {
		for dRows.Next() {
			var sd SpendDay
			if err := dRows.Scan(&sd.Day, &sd.AWS, &sd.GCP); err == nil {
				stats.SpendByDay = append(stats.SpendByDay, sd)
			}
		}
		dRows.Close()
	}

	return stats, nil
}

// GetAnalyticsComposition aggregates spend composition (Lens B).
func (d *DB) GetAnalyticsComposition(dimension string, filter AnalyticsFilter) (*CompositionResult, error) {
	dim := strings.ToLower(strings.TrimSpace(dimension))
	if dim == "" {
		dim = "service"
	}

	whereClause, args := buildFilterClauses("a", filter)
	query := fmt.Sprintf(`
		SELECT b.key,
		       COALESCE(SUM(b.aws_usd), 0) AS aws_usd,
		       COALESCE(SUM(b.gcp_usd), 0) AS gcp_usd,
		       COALESCE(SUM(b.savings_usd), 0) AS savings_usd,
		       COALESCE(SUM(b.rows), 0) AS rows_count
		FROM job_analytics_breakdown b
		JOIN job_analytics a ON b.job_id = a.job_id
		WHERE b.dimension = ? %s
		GROUP BY b.key
		ORDER BY aws_usd DESC
	`, whereClause)

	allArgs := append([]any{dim}, args...)
	rows, err := d.conn.Query(query, allArgs...)
	if err != nil {
		return nil, fmt.Errorf("query composition: %w", err)
	}
	defer rows.Close()

	var totalAWS float64
	var items []CompositionItem
	var negSavings []CompositionItem

	for rows.Next() {
		var it CompositionItem
		if err := rows.Scan(&it.Key, &it.AWSUSD, &it.GCPUSD, &it.SavingsUSD, &it.Rows); err != nil {
			return nil, err
		}
		if it.AWSUSD > 0 {
			it.SavingsPct = (it.SavingsUSD / it.AWSUSD) * 100
		}
		totalAWS += it.AWSUSD
		items = append(items, it)

		if it.SavingsUSD < 0 {
			negSavings = append(negSavings, it)
		}
	}

	for i := range items {
		if totalAWS > 0 {
			items[i].PctOfAWS = (items[i].AWSUSD / totalAWS) * 100
		}
	}

	sort.Slice(negSavings, func(i, j int) bool {
		return negSavings[i].SavingsUSD < negSavings[j].SavingsUSD // lowest/most negative first
	})

	return &CompositionResult{
		Dimension:       dim,
		Items:           items,
		NegativeSavings: negSavings,
	}, nil
}

// GetAnalyticsQuality aggregates projection quality metrics (Lens C).
func (d *DB) GetAnalyticsQuality(filter AnalyticsFilter) (*QualityResult, error) {
	res := &QualityResult{
		ConfidenceDistribution: []ConfidenceItem{},
		CatalogVintages:        []VintageItem{},
	}

	whereClause, args := buildFilterClauses("", filter)
	query := fmt.Sprintf(`
		SELECT COALESCE(SUM(total_rows), 0),
		       COALESCE(SUM(mapped_rows), 0),
		       COALESCE(SUM(passthrough_rows), 0),
		       COALESCE(SUM(ignored_rows), 0),
		       COALESCE(SUM(llm_rows), 0),
		       COALESCE(SUM(deterministic_rows), 0),
		       COALESCE(SUM(unmapped_usd), 0),
		       COALESCE(SUM(aws_spend), 0),
		       COALESCE(SUM(findings_high), 0),
		       COALESCE(SUM(findings_med), 0),
		       COALESCE(SUM(findings_low), 0)
		FROM job_analytics
		WHERE 1=1 %s
	`, whereClause)

	var totalSpend float64
	err := d.conn.QueryRow(query, args...).Scan(
		&res.TotalRows,
		&res.MappedRows,
		&res.PassthroughRows,
		&res.IgnoredRows,
		&res.LLMRows,
		&res.DeterministicRows,
		&res.TotalUnmappedUSD,
		&totalSpend,
		&res.FindingsSummary.High,
		&res.FindingsSummary.Med,
		&res.FindingsSummary.Low,
	)
	if err != nil {
		return nil, fmt.Errorf("query quality metrics: %w", err)
	}

	res.FindingsSummary.Total = res.FindingsSummary.High + res.FindingsSummary.Med + res.FindingsSummary.Low

	if res.TotalRows > 0 {
		res.DeterministicPct = (float64(res.DeterministicRows) / float64(res.TotalRows)) * 100
		res.LLMPct = (float64(res.LLMRows) / float64(res.TotalRows)) * 100
		res.PassthroughPct = (float64(res.PassthroughRows) / float64(res.TotalRows)) * 100
		res.IgnoredPct = (float64(res.IgnoredRows) / float64(res.TotalRows)) * 100
	}

	if totalSpend > 0 {
		res.CoveragePct = ((totalSpend - res.TotalUnmappedUSD) / totalSpend) * 100
	} else {
		res.CoveragePct = 100.0
	}

	// Confidence distribution
	confQuery := fmt.Sprintf(`
		SELECT COALESCE(NULLIF(confidence_band, ''), 'unknown') AS band,
		       COUNT(*),
		       COALESCE(SUM(aws_spend), 0)
		FROM job_analytics
		WHERE 1=1 %s
		GROUP BY band
		ORDER BY SUM(aws_spend) DESC
	`, whereClause)

	cRows, err := d.conn.Query(confQuery, args...)
	if err == nil {
		for cRows.Next() {
			var ci ConfidenceItem
			if err := cRows.Scan(&ci.Band, &ci.Count, &ci.AWSUSD); err == nil {
				if totalSpend > 0 {
					ci.PctOfUSD = (ci.AWSUSD / totalSpend) * 100
				}
				res.ConfidenceDistribution = append(res.ConfidenceDistribution, ci)
			}
		}
		cRows.Close()
	}

	// Catalog vintages
	vQuery := fmt.Sprintf(`
		SELECT COALESCE(NULLIF(catalog_vintage, ''), 'unknown') AS vintage,
		       COUNT(*)
		FROM job_analytics
		WHERE 1=1 %s
		GROUP BY vintage
		ORDER BY COUNT(*) DESC
	`, whereClause)

	vRows, err := d.conn.Query(vQuery, args...)
	if err == nil {
		for vRows.Next() {
			var vi VintageItem
			if err := vRows.Scan(&vi.Vintage, &vi.Count); err == nil {
				res.CatalogVintages = append(res.CatalogVintages, vi)
			}
		}
		vRows.Close()
	}

	return res, nil
}

// GetAnalyticsMethodology aggregates methodology and trust metrics (Lens D).
func (d *DB) GetAnalyticsMethodology(filter AnalyticsFilter) (*MethodologyResult, error) {
	res := &MethodologyResult{
		AssumptionsVersions: []VersionCountItem{},
		EngineVersions:      []VersionCountItem{},
	}

	whereClause, args := buildFilterClauses("", filter)
	query := fmt.Sprintf(`
		SELECT COALESCE(SUM(aws_spend), 0),
		       COALESCE(SUM(sp_ri_exposed_usd), 0),
		       COALESCE(AVG(matrix_decided_pct), 0)
		FROM job_analytics
		WHERE 1=1 %s
	`, whereClause)

	var totalSpend float64
	err := d.conn.QueryRow(query, args...).Scan(
		&totalSpend,
		&res.SPRIExposedUSD,
		&res.MatrixCoveragePct,
	)
	if err != nil {
		return nil, fmt.Errorf("query methodology metrics: %w", err)
	}

	if totalSpend > 0 {
		res.SPRIExposedPct = (res.SPRIExposedUSD / totalSpend) * 100
	}

	// Review loop findings status from review_findings table
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM review_findings`).Scan(&res.ReviewLoopThroughput.TotalFindings)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM review_findings WHERE status = 'pending'`).Scan(&res.ReviewLoopThroughput.Pending)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM review_findings WHERE status IN ('accepted', 'resolved')`).Scan(&res.ReviewLoopThroughput.Accepted)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM review_findings WHERE status = 'rejected'`).Scan(&res.ReviewLoopThroughput.Rejected)
	_ = d.conn.QueryRow(`SELECT COUNT(*) FROM review_findings WHERE status = 'dismissed'`).Scan(&res.ReviewLoopThroughput.Dismissed)

	// Assumptions versions
	avQuery := fmt.Sprintf(`
		SELECT COALESCE(NULLIF(assumptions_version, ''), 'default') AS ver,
		       COUNT(*)
		FROM job_analytics
		WHERE 1=1 %s
		GROUP BY ver
		ORDER BY COUNT(*) DESC
	`, whereClause)
	avRows, err := d.conn.Query(avQuery, args...)
	if err == nil {
		for avRows.Next() {
			var vi VersionCountItem
			if err := avRows.Scan(&vi.Version, &vi.Count); err == nil {
				res.AssumptionsVersions = append(res.AssumptionsVersions, vi)
			}
		}
		avRows.Close()
	}

	// Engine versions
	evQuery := fmt.Sprintf(`
		SELECT COALESCE(NULLIF(engine_version, ''), 'v1.0') AS ver,
		       COUNT(*)
		FROM job_analytics
		WHERE 1=1 %s
		GROUP BY ver
		ORDER BY COUNT(*) DESC
	`, whereClause)
	evRows, err := d.conn.Query(evQuery, args...)
	if err == nil {
		for evRows.Next() {
			var vi VersionCountItem
			if err := evRows.Scan(&vi.Version, &vi.Count); err == nil {
				res.EngineVersions = append(res.EngineVersions, vi)
			}
		}
		evRows.Close()
	}

	return res, nil
}
