package jobs

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/facets/cur-web/internal/db"
)

// ComputeJobAnalytics extracts and materializes the analytics rollup for a job.
func ComputeJobAnalytics(job *db.Job, jobDir string, database *db.DB) (*db.JobAnalytics, []db.JobAnalyticsBreakdown, error) {
	if job == nil {
		return nil, nil, fmt.Errorf("job is nil")
	}

	dbPath := filepath.Join(jobDir, "projection-audit", "projection.duckdb")
	if _, err := os.Stat(dbPath); err != nil {
		dbPath = filepath.Join(jobDir, "projection.duckdb")
		if _, err := os.Stat(dbPath); err != nil {
			// No duckdb file found. Return minimal rollup from jobs table.
			return minimalJobAnalytics(job), nil, nil
		}
	}

	createdStr := job.CreatedAt.Format("2006-01-02 15:04:05")
	rollup := &db.JobAnalytics{
		JobID:              job.ID,
		Prospect:           job.Prospect,
		Owner:              job.Owner,
		CreatedAt:          createdStr,
		Currency:           "USD",
		EngineVersion:      job.EngineVersion,
		AssumptionsVersion: job.AssumptionsVersion,
		ConfidenceBand:     "medium",
		RolledUpAt:         time.Now().UTC().Format(time.RFC3339),
	}

	var breakdowns []db.JobAnalyticsBreakdown

	hasCatalog, _ := tableExists(dbPath, "aws_li_catalog")
	if !hasCatalog {
		return minimalJobAnalytics(job), nil, nil
	}

	// 1. Currency
	hasCurrency, _ := tableExists(dbPath, "bill_currency")
	if hasCurrency {
		if curOut, err := runDuckDB(dbPath, "SELECT currency FROM bill_currency LIMIT 1"); err == nil {
			var curRows []map[string]any
			if json.Unmarshal(curOut, &curRows) == nil && len(curRows) > 0 {
				if c, ok := curRows[0]["currency"].(string); ok && c != "" {
					rollup.Currency = c
				}
			}
		}
	}

	// 2. AWS Spend and Workload
	spendSQL := `
		SELECT
			COALESCE(SUM(aws_amortized_cost), 0.0) AS total_spend,
			COALESCE(SUM(CASE WHEN COALESCE(is_workload, TRUE) = TRUE THEN aws_amortized_cost ELSE 0.0 END), 0.0) AS workload_spend,
			COUNT(*) AS total_rows
		FROM aws_li_catalog
	`
	if spendOut, err := runDuckDB(dbPath, spendSQL); err == nil {
		var sRows []map[string]any
		if json.Unmarshal(spendOut, &sRows) == nil && len(sRows) > 0 {
			rollup.AWSSpend = nullableFloatVal(sRows[0]["total_spend"])
			rollup.AWSWorkload = nullableFloatVal(sRows[0]["workload_spend"])
			if n, ok := sRows[0]["total_rows"].(float64); ok {
				rollup.TotalRows = int(n)
			}
		}
	}
	if rollup.AWSSpend == 0 && job.AWSSpend != nil {
		rollup.AWSSpend = *job.AWSSpend
	}

	// 3. GCP Costs and Confidence from run_results or gcp_projection
	runs, _ := QueryRunResults(dbPath)
	if len(runs) > 0 {
		r := runs[0]
		rollup.GCPOD = r.GCPOD
		rollup.GCP1yrCUD = r.GCP1yrCUD
		rollup.GCP3yrCUD = r.GCP3yrCUD
		if r.Confidence != nil && *r.Confidence != "" {
			rollup.ConfidenceBand = *r.Confidence
		}
	}

	hasProj, _ := tableExists(dbPath, "gcp_projection")
	if (rollup.GCPOD == nil || *rollup.GCPOD == 0) && hasProj {
		projSQL := `
			SELECT
				COALESCE(SUM(gcp_projected_cost), 0.0) AS gcp_od,
				COALESCE(SUM(gcp_cost_1yr_cud), 0.0) AS gcp_1yr,
				COALESCE(SUM(gcp_cost_3yr_cud), 0.0) AS gcp_3yr
			FROM gcp_projection
			WHERE COALESCE(is_workload, TRUE) = TRUE
		`
		if pOut, err := runDuckDB(dbPath, projSQL); err == nil {
			var pRows []map[string]any
			if json.Unmarshal(pOut, &pRows) == nil && len(pRows) > 0 {
				od := nullableFloatVal(pRows[0]["gcp_od"])
				c1 := nullableFloatVal(pRows[0]["gcp_1yr"])
				c3 := nullableFloatVal(pRows[0]["gcp_3yr"])
				rollup.GCPOD = &od
				rollup.GCP1yrCUD = &c1
				rollup.GCP3yrCUD = &c3
			}
		}
	}

	if rollup.GCPOD == nil && job.GCPOD != nil {
		rollup.GCPOD = job.GCPOD
	}

	if rollup.GCPOD != nil {
		odVal := *rollup.GCPOD
		sav := rollup.AWSSpend - odVal
		rollup.SavingsOD = &sav
		if rollup.AWSSpend > 0 {
			savPct := (sav / rollup.AWSSpend) * 100
			rollup.SavingsPctOD = &savPct
		}
		if rollup.GCP1yrCUD == nil {
			c1 := odVal * 0.70
			rollup.GCP1yrCUD = &c1
		}
		if rollup.GCP3yrCUD == nil {
			c3 := odVal * 0.50
			rollup.GCP3yrCUD = &c3
		}
	}

	// 4. Quality & Strategy breakdown from aws_li_to_gcp_li
	hasMapping, _ := tableExists(dbPath, "aws_li_to_gcp_li")
	if hasMapping {
		mSQL := `
			SELECT
				COUNT(CASE WHEN strategy IN ('map', 'break_down') THEN 1 END) AS mapped_rows,
				COUNT(CASE WHEN strategy IN ('passthrough', 'needs_reprice') THEN 1 END) AS passthrough_rows,
				COUNT(CASE WHEN strategy = 'ignore' THEN 1 END) AS ignored_rows,
				COUNT(CASE WHEN decided_by IS NOT NULL AND (
					LOWER(decided_by) LIKE '%llm%'
					OR LOWER(decided_by) LIKE '%misc%'
					OR LOWER(decided_by) LIKE '%compute_breakdown%'
					OR LOWER(decided_by) LIKE '%managed_db%'
				) THEN 1 END) AS llm_rows
			FROM aws_li_to_gcp_li
		`
		if mOut, err := runDuckDB(dbPath, mSQL); err == nil {
			var mRows []map[string]any
			if json.Unmarshal(mOut, &mRows) == nil && len(mRows) > 0 {
				if v, ok := mRows[0]["mapped_rows"].(float64); ok {
					rollup.MappedRows = int(v)
				}
				if v, ok := mRows[0]["passthrough_rows"].(float64); ok {
					rollup.PassthroughRows = int(v)
				}
				if v, ok := mRows[0]["ignored_rows"].(float64); ok {
					rollup.IgnoredRows = int(v)
				}
				if v, ok := mRows[0]["llm_rows"].(float64); ok {
					rollup.LLMRows = int(v)
				}
			}
		}

		rollup.DeterministicRows = rollup.MappedRows - rollup.LLMRows
		if rollup.DeterministicRows < 0 {
			rollup.DeterministicRows = 0
		}

		// Unmapped USD
		unmapSQL := `
			SELECT COALESCE(SUM(c.aws_amortized_cost), 0.0) AS unmapped_usd
			FROM aws_li_catalog c
			WHERE c.aws_li_key IN (
				SELECT DISTINCT m.aws_li_key
				FROM aws_li_to_gcp_li m
				WHERE m.gcp_service IS NULL
				   OR m.gcp_service = ''
				   OR LOWER(m.gcp_service) LIKE '%manual review%'
				   OR LOWER(m.gcp_service) LIKE '%manual sizing%'
			)
		`
		if uOut, err := runDuckDB(dbPath, unmapSQL); err == nil {
			var uRows []map[string]any
			if json.Unmarshal(uOut, &uRows) == nil && len(uRows) > 0 {
				rollup.UnmappedUSD = nullableFloatVal(uRows[0]["unmapped_usd"])
			}
		}

		if rollup.AWSSpend > 0 {
			rollup.CoveragePct = ((rollup.AWSSpend - rollup.UnmappedUSD) / rollup.AWSSpend) * 100
			if rollup.CoveragePct < 0 {
				rollup.CoveragePct = 0
			}
		} else {
			rollup.CoveragePct = 100.0
		}

		// Pricing matrix coverage
		matSQL := `
			SELECT COALESCE(SUM(c.aws_amortized_cost), 0.0) AS mat_usd
			FROM aws_li_catalog c
			WHERE c.aws_li_key IN (
				SELECT DISTINCT m.aws_li_key
				FROM aws_li_to_gcp_li m
				WHERE m.strategy IN ('map', 'break_down', 'ignore')
				  AND (m.decided_by IS NULL OR (
					LOWER(m.decided_by) NOT LIKE '%llm%'
					AND LOWER(m.decided_by) NOT LIKE '%misc%'
					AND LOWER(m.decided_by) NOT LIKE '%compute_breakdown%'
					AND LOWER(m.decided_by) NOT LIKE '%managed_db%'
				  ))
				  AND m.gcp_service IS NOT NULL
				  AND LOWER(m.gcp_service) NOT LIKE '%manual review%'
			)
		`
		if matOut, err := runDuckDB(dbPath, matSQL); err == nil {
			var matRows []map[string]any
			if json.Unmarshal(matOut, &matRows) == nil && len(matRows) > 0 {
				matUSD := nullableFloatVal(matRows[0]["mat_usd"])
				if rollup.AWSSpend > 0 {
					rollup.MatrixDecidedPct = (matUSD / rollup.AWSSpend) * 100
				} else {
					rollup.MatrixDecidedPct = 100.0
				}
			}
		}
	}

	// 5. SP/RI netting exposure
	spSQL := `
		SELECT COALESCE(SUM(aws_amortized_cost), 0.0) AS sp_exposed
		FROM aws_li_catalog
		WHERE (public_on_demand_cost IS NOT NULL AND public_on_demand_cost > aws_amortized_cost * 1.01)
		   OR (sp_ri_discount_pct IS NOT NULL AND sp_ri_discount_pct > 0.01)
	`
	if spOut, err := runDuckDB(dbPath, spSQL); err == nil {
		var spRows []map[string]any
		if json.Unmarshal(spOut, &spRows) == nil && len(spRows) > 0 {
			rollup.SPRIExposedUSD = nullableFloatVal(spRows[0]["sp_exposed"])
		}
	}

	// 6. Review findings counts
	rollup.FindingsHigh, rollup.FindingsMed, rollup.FindingsLow = extractFindingsCounts(job.ID, jobDir)

	// 7. Catalog vintage
	rollup.CatalogVintage = extractCatalogVintage(jobDir)

	// 8. Dimension Breakdowns (Lens B)
	projJoin := ""
	projCostCol := "0.0"
	if hasProj {
		projJoin = "LEFT JOIN gcp_projection p ON c.aws_li_key = p.aws_li_key"
		projCostCol = "COALESCE(p.gcp_projected_cost, 0.0)"
	}

	// Breakdown 8a: Service
	svcSQL := fmt.Sprintf(`
		SELECT
			COALESCE(NULLIF(c.canonical_service, ''), NULLIF(c.product, ''), 'unknown') AS k,
			COALESCE(SUM(c.aws_amortized_cost), 0.0) AS aws_usd,
			COALESCE(SUM(%s), 0.0) AS gcp_usd,
			COUNT(DISTINCT c.aws_li_key) AS rows_count
		FROM aws_li_catalog c
		%s
		GROUP BY k
		ORDER BY aws_usd DESC
	`, projCostCol, projJoin)
	if svcOut, err := runDuckDB(dbPath, svcSQL); err == nil {
		var sRows []map[string]any
		if json.Unmarshal(svcOut, &sRows) == nil {
			for _, r := range sRows {
				k := strOrEmpty(r["k"])
				a := nullableFloatVal(r["aws_usd"])
				g := nullableFloatVal(r["gcp_usd"])
				rows := 1
				if n, ok := r["rows_count"].(float64); ok {
					rows = int(n)
				}
				breakdowns = append(breakdowns, db.JobAnalyticsBreakdown{
					JobID:      job.ID,
					Dimension:  "service",
					Key:        k,
					AWSUSD:     a,
					GCPUSD:     g,
					SavingsUSD: a - g,
					Rows:       rows,
				})
			}
		}
	}

	// Breakdown 8b: Charge Type
	chargeSQL := fmt.Sprintf(`
		SELECT
			CASE
				WHEN LOWER(COALESCE(c.product, '')) LIKE '%%compute%%' OR LOWER(COALESCE(c.usage_type, '')) LIKE '%%boxusage%%' OR LOWER(COALESCE(c.usage_type, '')) LIKE '%%instance%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%fargate%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%lambda%%' THEN 'compute'
				WHEN LOWER(COALESCE(c.product, '')) LIKE '%%storage%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%s3%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%ebs%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%filestore%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%efs%%' THEN 'storage'
				WHEN LOWER(COALESCE(c.product, '')) LIKE '%%data transfer%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%bandwidth%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%nat%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%vpc%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%elastic load balancing%%' THEN 'network'
				WHEN LOWER(COALESCE(c.product, '')) LIKE '%%rds%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%database%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%aurora%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%redshift%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%dynamodb%%' THEN 'database'
				WHEN LOWER(COALESCE(c.product, '')) LIKE '%%cloudwatch%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%cloudtrail%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%guardduty%%' OR LOWER(COALESCE(c.product, '')) LIKE '%%security%%' THEN 'ops'
				ELSE 'other'
			END AS k,
			COALESCE(SUM(c.aws_amortized_cost), 0.0) AS aws_usd,
			COALESCE(SUM(%s), 0.0) AS gcp_usd,
			COUNT(DISTINCT c.aws_li_key) AS rows_count
		FROM aws_li_catalog c
		%s
		GROUP BY k
		ORDER BY aws_usd DESC
	`, projCostCol, projJoin)
	if chOut, err := runDuckDB(dbPath, chargeSQL); err == nil {
		var cRows []map[string]any
		if json.Unmarshal(chOut, &cRows) == nil {
			for _, r := range cRows {
				k := strOrEmpty(r["k"])
				a := nullableFloatVal(r["aws_usd"])
				g := nullableFloatVal(r["gcp_usd"])
				rows := 1
				if n, ok := r["rows_count"].(float64); ok {
					rows = int(n)
				}
				breakdowns = append(breakdowns, db.JobAnalyticsBreakdown{
					JobID:      job.ID,
					Dimension:  "charge_type",
					Key:        k,
					AWSUSD:     a,
					GCPUSD:     g,
					SavingsUSD: a - g,
					Rows:       rows,
				})
			}
		}
	}

	// Breakdown 8c: Decision Type
	if hasMapping {
		stratJoin := "LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key"
		decSQL := fmt.Sprintf(`
			SELECT
				CASE
					WHEN m.strategy IN ('map', 'break_down') THEN 'SKU'
					WHEN m.strategy IN ('passthrough', 'needs_reprice') THEN 'PASSTHROUGH'
					WHEN m.strategy = 'ignore' THEN 'IGNORE'
					ELSE 'OTHER'
				END AS k,
				COALESCE(SUM(c.aws_amortized_cost), 0.0) AS aws_usd,
				COALESCE(SUM(%s), 0.0) AS gcp_usd,
				COUNT(DISTINCT c.aws_li_key) AS rows_count
			FROM aws_li_catalog c
			%s
			%s
			GROUP BY k
			ORDER BY aws_usd DESC
		`, projCostCol, stratJoin, projJoin)
		if decOut, err := runDuckDB(dbPath, decSQL); err == nil {
			var dRows []map[string]any
			if json.Unmarshal(decOut, &dRows) == nil {
				for _, r := range dRows {
					k := strOrEmpty(r["k"])
					a := nullableFloatVal(r["aws_usd"])
					g := nullableFloatVal(r["gcp_usd"])
					rows := 1
					if n, ok := r["rows_count"].(float64); ok {
						rows = int(n)
					}
					breakdowns = append(breakdowns, db.JobAnalyticsBreakdown{
						JobID:      job.ID,
						Dimension:  "decision_type",
						Key:        k,
						AWSUSD:     a,
						GCPUSD:     g,
						SavingsUSD: a - g,
						Rows:       rows,
					})
				}
			}
		}
	}

	// Breakdown 8d: Region
	regSQL := fmt.Sprintf(`
		SELECT
			COALESCE(NULLIF(c.aws_region, ''), NULLIF(c.gcp_region, ''), 'global') AS k,
			COALESCE(SUM(c.aws_amortized_cost), 0.0) AS aws_usd,
			COALESCE(SUM(%s), 0.0) AS gcp_usd,
			COUNT(DISTINCT c.aws_li_key) AS rows_count
		FROM aws_li_catalog c
		%s
		GROUP BY k
		ORDER BY aws_usd DESC
	`, projCostCol, projJoin)
	if regOut, err := runDuckDB(dbPath, regSQL); err == nil {
		var rgRows []map[string]any
		if json.Unmarshal(regOut, &rgRows) == nil {
			for _, r := range rgRows {
				k := strOrEmpty(r["k"])
				a := nullableFloatVal(r["aws_usd"])
				g := nullableFloatVal(r["gcp_usd"])
				rows := 1
				if n, ok := r["rows_count"].(float64); ok {
					rows = int(n)
				}
				breakdowns = append(breakdowns, db.JobAnalyticsBreakdown{
					JobID:      job.ID,
					Dimension:  "region",
					Key:        k,
					AWSUSD:     a,
					GCPUSD:     g,
					SavingsUSD: a - g,
					Rows:       rows,
				})
			}
		}
	}

	return rollup, breakdowns, nil
}

func minimalJobAnalytics(job *db.Job) *db.JobAnalytics {
	awsVal := 0.0
	if job.AWSSpend != nil {
		awsVal = *job.AWSSpend
	}
	createdStr := job.CreatedAt.Format("2006-01-02 15:04:05")
	rollup := &db.JobAnalytics{
		JobID:              job.ID,
		Prospect:           job.Prospect,
		Owner:              job.Owner,
		CreatedAt:          createdStr,
		Currency:           "USD",
		AWSSpend:           awsVal,
		AWSWorkload:        awsVal,
		GCPOD:              job.GCPOD,
		TotalRows:          1,
		MappedRows:         1,
		CoveragePct:        100.0,
		ConfidenceBand:     "medium",
		EngineVersion:      job.EngineVersion,
		AssumptionsVersion: job.AssumptionsVersion,
		RolledUpAt:         time.Now().UTC().Format(time.RFC3339),
	}
	if job.GCPOD != nil {
		od := *job.GCPOD
		sav := awsVal - od
		rollup.SavingsOD = &sav
		if awsVal > 0 {
			savPct := (sav / awsVal) * 100
			rollup.SavingsPctOD = &savPct
		}
		c1 := od * 0.70
		c3 := od * 0.50
		rollup.GCP1yrCUD = &c1
		rollup.GCP3yrCUD = &c3
	}
	return rollup
}

func extractFindingsCounts(jobID, jobDir string) (high int, med int, low int) {
	findingsPath := filepath.Join(jobDir, "projection-audit", "review_findings.json")
	data, err := os.ReadFile(findingsPath)
	if err != nil {
		findingsPath = filepath.Join(jobDir, "review_findings.json")
		data, err = os.ReadFile(findingsPath)
	}
	if err != nil {
		return 0, 0, 0
	}

	var findings []struct {
		Severity string `json:"severity"`
	}
	if json.Unmarshal(data, &findings) != nil {
		return 0, 0, 0
	}

	for _, f := range findings {
		switch strings.ToLower(strings.TrimSpace(f.Severity)) {
		case "high", "critical":
			high++
		case "medium", "med":
			med++
		case "low":
			low++
		}
	}
	return high, med, low
}

func extractCatalogVintage(jobDir string) string {
	diagPath := filepath.Join(jobDir, "projection-audit", "run_diagnostics.json")
	data, err := os.ReadFile(diagPath)
	if err == nil {
		var diag map[string]any
		if json.Unmarshal(data, &diag) == nil {
			if v, ok := diag["catalog_vintage"].(string); ok && v != "" {
				return v
			}
			if v, ok := diag["catalog_vintage_date"].(string); ok && v != "" {
				return v
			}
		}
	}
	return "current"
}
