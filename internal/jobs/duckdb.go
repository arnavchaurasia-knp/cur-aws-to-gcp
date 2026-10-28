// internal/jobs/duckdb.go
//
// Thin wrapper around the duckdb CLI for read-only queries against a job's
// projection-audit/projection.duckdb. We shell out via `os/exec` rather than
// pull in a cgo-bound Go driver — each call is ~50ms which is fine for the
// ~5s UI poll cadence, and it keeps the binary small (no ~50MB cgo
// dependency).
package jobs

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// RunResult mirrors one row of the projection-audit/projection.duckdb
// run_results table. Nullable columns use *string / *float64 so they
// marshal as JSON null rather than zero values.
type RunResult struct {
	RunID        string   `json:"run_id"`
	TsUTC        string   `json:"ts_utc"`
	RunType      string   `json:"run_type"`
	Instruction  *string  `json:"instruction"`
	AWSTotal     *float64 `json:"aws_total"`
	GCPOD        *float64 `json:"gcp_od"`
	GCP1yrCUD    *float64 `json:"gcp_1yr_cud"`
	GCP3yrCUD    *float64 `json:"gcp_3yr_cud"`
	ReportHTML   *string  `json:"report_html"`
	ReportMD     *string  `json:"report_md"`
	SummaryMD    *string  `json:"summary_md"`
	MappedRows   *int     `json:"mapped_rows"`
	Passthroughs *int     `json:"passthroughs"`
	Confidence   *string  `json:"confidence"`
}

// runDuckDB invokes the duckdb CLI in readonly+json mode against dbPath and
// returns the raw stdout. Falls back to Python (duckdb module) when the CLI
// binary is not on PATH — covers Windows installs where only pip-installed
// duckdb is available. Returns os.ErrNotExist when dbPath is missing so
// callers can distinguish "no projection.duckdb yet" from real errors.
func runDuckDB(dbPath, sql string) ([]byte, error) {
	if _, err := os.Stat(dbPath); err != nil {
		if os.IsNotExist(err) {
			return nil, os.ErrNotExist
		}
		return nil, err
	}
	// Try the duckdb CLI first.
	if cli, err := exec.LookPath("duckdb"); err == nil {
		cmd := exec.Command(cli, "-readonly", "-json", dbPath, "-c", sql)
		out, err := cmd.Output()
		if err != nil {
			var ee *exec.ExitError
			if errors.As(err, &ee) {
				return nil, fmt.Errorf("duckdb: %w: %s", err, strings.TrimSpace(string(ee.Stderr)))
			}
			return nil, fmt.Errorf("duckdb: %w", err)
		}
		return out, nil
	}
	// Fall back to Python's duckdb module (available wherever pip installed it).
	py := pythonBin()
	pyScript := `import duckdb,json,sys
c=duckdb.connect(sys.argv[1],read_only=True)
rows=c.execute(sys.argv[2]).fetchall()
cols=[d[0] for d in c.description]
print(json.dumps([dict(zip(cols,r)) for r in rows]))
c.close()`
	cmd := exec.Command(py, "-c", pyScript, dbPath, sql)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return nil, fmt.Errorf("duckdb (python fallback): %w: %s", err, strings.TrimSpace(string(ee.Stderr)))
		}
		return nil, fmt.Errorf("duckdb (python fallback): %w", err)
	}
	return out, nil
}

// tableExists checks whether the named table is present in the main schema.
// Used to distinguish "no run_results table yet" (legacy job) from a real
// query error.
var allowedTables = map[string]bool{
	"run_results":      true,
	"aws_li_catalog":   true,
	"bill_currency":    true,
}

func tableExists(dbPath, table string) (bool, error) {
	if !allowedTables[table] {
		return false, fmt.Errorf("tableExists: unknown table %q", table)
	}
	out, err := runDuckDB(dbPath, fmt.Sprintf(
		"SELECT 1 AS x FROM information_schema.tables WHERE table_schema='main' AND table_name='%s'",
		table,
	))
	if err != nil {
		return false, err
	}
	// duckdb -json emits an empty string when the result is zero rows
	// (rather than "[]"). Treat that as "not found".
	if len(out) == 0 || strings.TrimSpace(string(out)) == "" {
		return false, nil
	}
	var rows []map[string]any
	if err := json.Unmarshal(out, &rows); err != nil {
		return false, fmt.Errorf("parse duckdb json: %w", err)
	}
	return len(rows) > 0, nil
}

// QueryRunResults returns all rows from run_results, latest first.
// Returns an empty slice + nil error when the db is missing, the table
// doesn't exist yet, or the table exists but has no rows. The handler
// layer treats all three identically (empty array).
func QueryRunResults(dbPath string) ([]RunResult, error) {
	if _, err := os.Stat(dbPath); err != nil {
		if os.IsNotExist(err) {
			return []RunResult{}, nil
		}
		return nil, err
	}
	exists, err := tableExists(dbPath, "run_results")
	if err != nil {
		return nil, err
	}
	if !exists {
		return []RunResult{}, nil
	}
	out, err := runDuckDB(dbPath, `
		SELECT
		  run_id, ts_utc, run_type, instruction,
		  aws_total, gcp_od, gcp_1yr_cud, gcp_3yr_cud,
		  report_html, report_md, summary_md,
		  mapped_rows, passthroughs, confidence
		FROM run_results
		ORDER BY ts_utc DESC, run_id DESC
	`)
	if err != nil {
		return nil, err
	}
	// duckdb -json emits an empty string for zero-row results, not "[]".
	if len(out) == 0 || strings.TrimSpace(string(out)) == "" {
		return []RunResult{}, nil
	}
	// We unmarshal into a permissive intermediate map because duckdb's
	// json output represents TIMESTAMP/INTEGER as primitives and we want
	// to convert TIMESTAMP to string + INT to *int cleanly.
	var raw []map[string]any
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, fmt.Errorf("parse run_results json: %w", err)
	}
	results := make([]RunResult, 0, len(raw))
	for _, r := range raw {
		results = append(results, mapToRunResult(r))
	}
	return results, nil
}

func mapToRunResult(r map[string]any) RunResult {
	rr := RunResult{
		RunID:   strOrEmpty(r["run_id"]),
		TsUTC:   strOrEmpty(r["ts_utc"]),
		RunType: strOrEmpty(r["run_type"]),
	}
	rr.Instruction = nullableStr(r["instruction"])
	rr.AWSTotal = nullableFloat(r["aws_total"])
	rr.GCPOD = nullableFloat(r["gcp_od"])
	rr.GCP1yrCUD = nullableFloat(r["gcp_1yr_cud"])
	rr.GCP3yrCUD = nullableFloat(r["gcp_3yr_cud"])
	rr.ReportHTML = nullableStr(r["report_html"])
	rr.ReportMD = nullableStr(r["report_md"])
	rr.SummaryMD = nullableStr(r["summary_md"])
	rr.MappedRows = nullableInt(r["mapped_rows"])
	rr.Passthroughs = nullableInt(r["passthroughs"])
	rr.Confidence = nullableStr(r["confidence"])
	return rr
}

func strOrEmpty(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprintf("%v", v)
}

func nullableStr(v any) *string {
	if v == nil {
		return nil
	}
	s, ok := v.(string)
	if !ok {
		s = fmt.Sprintf("%v", v)
	}
	return &s
}

func nullableFloat(v any) *float64 {
	if v == nil {
		return nil
	}
	switch n := v.(type) {
	case float64:
		return &n
	case int:
		f := float64(n)
		return &f
	case int64:
		f := float64(n)
		return &f
	case json.Number:
		f, err := n.Float64()
		if err != nil {
			return nil
		}
		return &f
	}
	return nil
}

func nullableInt(v any) *int {
	if v == nil {
		return nil
	}
	// DuckDB JSON output always produces float64 for numbers via json.Unmarshal.
	// int and int64 cases are unreachable from that path and have been removed.
	if n, ok := v.(float64); ok {
		i := int(n)
		return &i
	}
	return nil
}

// BillCurrency mirrors the bill_currency table written by currency_utils.py.
type BillCurrency struct {
	Currency        string  `json:"currency"`         // e.g. "EUR"; "USD" default
	Symbol          string  `json:"symbol"`           // e.g. "€"
	FXRate          float64 `json:"fx_rate"`           // USD -> display currency (for GCP side)
	AWSFXRate       float64 `json:"aws_fx_rate"`       // USD -> display currency (for AWS side)
	AWSCostCurrency string  `json:"aws_cost_currency"` // currency the stored USD came from
	Label           string  `json:"label"`
	Source          string  `json:"source"`
}

// QueryBillCurrency reads the single bill_currency row. Returns a USD default
// (fx 1.0) when the table or row is absent so callers never special-case nil.
// Defensive: if the column list errors (e.g. old schema missing aws_fx_rate),
// falls back to a minimal column set.
func QueryBillCurrency(dbPath string) (BillCurrency, error) {
	def := BillCurrency{Currency: "USD", Symbol: "$", FXRate: 1.0, AWSFXRate: 1.0, AWSCostCurrency: "USD", Label: "USD", Source: "default"}

	var err error
	var out []byte
	out, err = runDuckDB(dbPath, "SELECT currency, symbol, fx_rate, aws_fx_rate, aws_cost_currency, label, source FROM bill_currency LIMIT 1")
	if err != nil {
		// If the column aws_fx_rate doesn't exist (old schema), retry without it.
		if strings.Contains(err.Error(), "aws_fx_rate") {
			out, err = runDuckDB(dbPath, "SELECT currency, symbol, fx_rate, label, source FROM bill_currency LIMIT 1")
			if err != nil {
				return def, nil
			}
			var rows []map[string]any
			if err := json.Unmarshal(out, &rows); err != nil || len(rows) == 0 {
				return def, nil
			}
			r := rows[0]
			cur := strOrEmpty(r["currency"])
			if cur == "" { cur = "USD" }
			sym := strOrEmpty(r["symbol"])
			if sym == "" { sym = "$" }
			label := strOrEmpty(r["label"])
			if label == "" { label = "USD" }
			source := strOrEmpty(r["source"])
			if source == "" { source = "default" }
			fx := nullableFloatVal(r["fx_rate"])
			if fx == 0 { fx = 1.0 }
			return BillCurrency{
				Currency:        cur,
				Symbol:          sym,
				FXRate:          fx,
				AWSFXRate:       1.0,
				AWSCostCurrency: cur,
				Label:           label,
				Source:          source,
			}, nil
		}
		return def, err
	}
	if len(out) == 0 || strings.TrimSpace(string(out)) == "" {
		return def, nil
	}
	var rows []BillCurrency
	if err := json.Unmarshal(out, &rows); err != nil || len(rows) == 0 {
		return def, nil
	}
	r := rows[0]
	if r.FXRate == 0 { r.FXRate = 1.0 }
	if r.AWSFXRate == 0 { r.AWSFXRate = 1.0 }
	if r.Symbol == "" { r.Symbol = "$" }
	if r.Currency == "" { r.Currency = "USD" }
	return r, nil
}

// nullableFloatVal returns the float64 value or 0.
func nullableFloatVal(v any) float64 {
	if v == nil {
		return 0
	}
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case int64:
		return float64(n)
	case json.Number:
		f, err := n.Float64()
		if err != nil {
			return 0
		}
		return f
	}
	return 0
}

// QueryAWSSpend returns SUM(aws_amortized_cost) FROM aws_li_catalog over ALL
// rows (workload + discount/credit). This matches the actual AWS bill total
// (post-discount net) shown on the invoice. Used by the watcher to populate
// jobs.aws_spend at finalize time without having to re-parse the HTML report.
//
// Previously this filtered to is_workload=TRUE, which returned the
// *pre-discount workload total* — visibly higher than the bill grand total
// when EDP/PRC discounts are present (parv@facets Rooter PDF Test 2 saw
// $18,009.81 vs the bill's $14,503.16; delta was the EDP+PRC discount lines
// classified as is_workload=FALSE).
//
// Returns 0 + nil when:
//   - dbPath is missing (the job dir lacks projection.duckdb)
//   - aws_li_catalog table doesn't exist
//   - the query returns no rows (everything filtered out)
// Other errors (corrupt db, sql error) bubble up.
func QueryAWSSpend(dbPath string) (float64, error) {
	if _, err := os.Stat(dbPath); err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	exists, err := tableExists(dbPath, "aws_li_catalog")
	if err != nil {
		return 0, err
	}
	if !exists {
		return 0, nil
	}
	out, err := runDuckDB(dbPath, `SELECT COALESCE(SUM(aws_amortized_cost), 0) AS total FROM aws_li_catalog`)
	if err != nil {
		return 0, err
	}
	if len(out) == 0 || strings.TrimSpace(string(out)) == "" {
		return 0, nil
	}
	var rows []map[string]any
	if err := json.Unmarshal(out, &rows); err != nil {
		return 0, fmt.Errorf("parse aws_spend json: %w", err)
	}
	if len(rows) == 0 {
		return 0, nil
	}
	if f := nullableFloat(rows[0]["total"]); f != nil {
		return *f, nil
	}
	return 0, nil
}
