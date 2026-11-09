package db

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"fmt"
	"log"
	"net/url"
	"strings"

	_ "github.com/tursodatabase/libsql-client-go/libsql" // registers the libsql driver for Turso cloud SQLite
	_ "modernc.org/sqlite"                              // registers the sqlite3 driver for local database/sql
)

type DB struct {
	conn *sql.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    prospect   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    input_ext  TEXT,
    aws_spend  REAL,
    error      TEXT,
    session_id TEXT,
    agent_pid INTEGER DEFAULT 0,
    attempts   INTEGER DEFAULT 1,
    gcp_od     REAL,
    engine     TEXT,
    engine_version TEXT,
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now')),
    cancelled_at DATETIME
);

CREATE TABLE IF NOT EXISTS users (
    email      TEXT PRIMARY KEY,
    role       TEXT NOT NULL DEFAULT 'viewer',
    admin_granted_by TEXT,
    admin_granted_at DATETIME,
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now')),
    email_notifications INTEGER NOT NULL DEFAULT 1,
    slack_notifications INTEGER NOT NULL DEFAULT 0,
    default_export_format TEXT DEFAULT 'html',
    default_engine TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_email TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_id   TEXT,
    details     TEXT,
    created_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS report_shares (
    token      TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    created_by TEXT,
    created_at DATETIME DEFAULT (datetime('now')),
    expires_at DATETIME
);

CREATE TABLE IF NOT EXISTS deleted_jobs (
    id         TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    prospect   TEXT NOT NULL,
    status     TEXT NOT NULL,
    input_ext  TEXT,
    aws_spend  REAL,
    gcp_od     REAL,
    error      TEXT,
    session_id TEXT,
    created_at DATETIME,
    deleted_at DATETIME DEFAULT (datetime('now')),
    deleted_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assumptions (
    key           TEXT PRIMARY KEY,
    value         REAL NOT NULL,
    default_value REAL NOT NULL,
    min_value     REAL NOT NULL,
    max_value     REAL NOT NULL,
    unit          TEXT,
    title         TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'other',
    scope         TEXT NOT NULL DEFAULT 'global',
    updated_by    TEXT,
    updated_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assumption_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    old_value   REAL NOT NULL,
    new_value   REAL NOT NULL,
    reason      TEXT,
    changed_by  TEXT NOT NULL,
    changed_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equivalence_usage (
    job_id       TEXT NOT NULL,
    attribute    TEXT NOT NULL,
    aws_value    TEXT NOT NULL,
    gcp_value    TEXT NOT NULL,
    scope        TEXT NOT NULL,
    rows_used    INTEGER NOT NULL,
    usd_exposed  REAL NOT NULL,
    recorded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_settings (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    value_type    TEXT NOT NULL,
    default_value TEXT NOT NULL,
    min_value     REAL,
    max_value     REAL,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    group_name    TEXT NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    updated_by    TEXT,
    updated_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS report_setting_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL,
    old_value  TEXT NOT NULL,
    new_value  TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_diagnostics (
    job_id       TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    error_count  INTEGER NOT NULL DEFAULT 0,
    warn_count   INTEGER NOT NULL DEFAULT 0,
    error_usd    REAL    NOT NULL DEFAULT 0,
    workload_usd REAL    NOT NULL DEFAULT 0,
    recorded_at  DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, run_id)
);

CREATE TABLE IF NOT EXISTS job_log_digest (
    job_id        TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    error_count   INTEGER NOT NULL DEFAULT 0,
    warn_count    INTEGER NOT NULL DEFAULT 0,
    first_error   TEXT,
    last_lines    TEXT,
    recorded_at   DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, run_id)
);

CREATE TABLE IF NOT EXISTS uploaded_bills (
    content_hash TEXT PRIMARY KEY,
    input_ext    TEXT NOT NULL,
    file_size    INTEGER NOT NULL,
    file_name    TEXT,
    created_at   DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    priority    TEXT NOT NULL DEFAULT 'medium',
    category    TEXT NOT NULL DEFAULT 'general',
    created_by  TEXT NOT NULL,
    created_at  DATETIME DEFAULT (datetime('now')),
    updated_at  DATETIME DEFAULT (datetime('now')),
    resolved_at DATETIME,
    resolved_by TEXT
);

CREATE TABLE IF NOT EXISTS user_profiles (
    email       TEXT PRIMARY KEY,
    name        TEXT,
    phone       TEXT,
    company     TEXT,
    department  TEXT,
    time_zone   TEXT DEFAULT 'UTC',
    updated_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_tokens (
    key_id       TEXT PRIMARY KEY,
    user_email   TEXT NOT NULL,
    token_hash   TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    scopes       TEXT NOT NULL DEFAULT 'jobs:read',
    created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
    last_used_at DATETIME,
    expires_at   DATETIME,
    revoked_at   DATETIME,
    created_ip   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_email) REFERENCES users(email)
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_email);

CREATE TABLE IF NOT EXISTS integration_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    secret_enc  BLOB,
    is_secret   INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- App-level overrides for operational environment variables, edited in
-- System Health → Settings & Secrets. Same shape and encryption as
-- integration_settings. Only keys in internal/envmgr's registry (ModeManaged)
-- are ever written here.
CREATE TABLE IF NOT EXISTS runtime_env (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    secret_enc  BLOB,
    is_secret   INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_analytics (
    job_id              TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    prospect            TEXT,
    owner               TEXT,
    created_at          TEXT,
    currency            TEXT DEFAULT 'USD',
    aws_spend           REAL,
    aws_workload        REAL,
    gcp_od              REAL,
    gcp_1yr_cud         REAL,
    gcp_3yr_cud         REAL,
    savings_od          REAL,
    savings_pct_od      REAL,
    total_rows          INTEGER,
    mapped_rows         INTEGER,
    passthrough_rows    INTEGER,
    ignored_rows        INTEGER,
    llm_rows            INTEGER,
    deterministic_rows  INTEGER,
    unmapped_usd        REAL,
    coverage_pct        REAL,
    confidence_band     TEXT,
    sp_ri_exposed_usd   REAL,
    findings_high       INTEGER,
    findings_med        INTEGER,
    findings_low        INTEGER,
    matrix_decided_pct  REAL,
    catalog_vintage     TEXT,
    assumptions_version TEXT,
    engine_version      TEXT,
    rolled_up_at        TEXT
);

CREATE TABLE IF NOT EXISTS job_analytics_breakdown (
    job_id       TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    dimension    TEXT,
    key          TEXT,
    aws_usd      REAL,
    gcp_usd      REAL,
    savings_usd  REAL,
    rows         INTEGER,
    PRIMARY KEY (job_id, dimension, key)
);

CREATE INDEX IF NOT EXISTS idx_job_analytics_created ON job_analytics(created_at);
CREATE INDEX IF NOT EXISTS idx_job_analytics_bd_dim ON job_analytics_breakdown(dimension);`

// Open opens a database connection. If remoteURL is provided and non-empty (e.g. Turso libsql://),
// it connects to the cloud libSQL database. Otherwise, it opens the local SQLite file at path.
func Open(path string, remoteURL ...string) (*DB, error) {
	var conn *sql.DB
	var err error

	remote := ""
	if len(remoteURL) > 0 {
		remote = strings.TrimSpace(remoteURL[0])
	}

	if remote != "" {
		conn, err = sql.Open("libsql", remote)
		if err != nil {
			return nil, err
		}
	} else {
		// WAL + a 5s busy_timeout so concurrent writers (the per-job watcher
		// goroutines and the HTTP handlers) don't hit an instant SQLITE_BUSY. Without
		// this a lost UpdateJobDone leaves a finished job stuck showing "running".
		// Pin to a single connection so writes serialize cleanly through WAL.
		conn, err = sql.Open("sqlite", path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
		if err != nil {
			return nil, err
		}
		conn.SetMaxOpenConns(1)
	}
	if _, err := conn.Exec(schema); err != nil {
		return nil, err
	}
	// Idempotent migrations for pre-existing dbs.
	// Ignored errors: "duplicate column" = already applied.
	for _, ddl := range []string{
		`ALTER TABLE jobs ADD COLUMN session_id TEXT`,
		`ALTER TABLE jobs ADD COLUMN agent_pid INTEGER DEFAULT 0`,
		`ALTER TABLE jobs ADD COLUMN attempts INTEGER DEFAULT 1`,
		`ALTER TABLE jobs ADD COLUMN cancelled_at DATETIME`,
		`ALTER TABLE users ADD COLUMN password_hash TEXT`,
		`ALTER TABLE users ADD COLUMN email_notifications INTEGER NOT NULL DEFAULT 1`,
		`ALTER TABLE users ADD COLUMN slack_notifications INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE users ADD COLUMN slack_webhook_url TEXT DEFAULT ''`,
		`ALTER TABLE users ADD COLUMN slack_channel TEXT DEFAULT ''`,
		`ALTER TABLE users ADD COLUMN api_token TEXT DEFAULT ''`,
		`ALTER TABLE users ADD COLUMN admin_granted_by TEXT`,
		`ALTER TABLE users ADD COLUMN admin_granted_at DATETIME`,
		`ALTER TABLE jobs ADD COLUMN assumptions_version TEXT`,
		`ALTER TABLE jobs ADD COLUMN engine TEXT`,
		`ALTER TABLE jobs ADD COLUMN engine_version TEXT`,
		`ALTER TABLE jobs ADD COLUMN gcp_od REAL`,
		`ALTER TABLE users ADD COLUMN default_export_format TEXT DEFAULT 'html'`,
		`ALTER TABLE users ADD COLUMN default_engine TEXT DEFAULT ''`,
		`ALTER TABLE assumptions ADD COLUMN category TEXT NOT NULL DEFAULT 'other'`,
		`CREATE TABLE IF NOT EXISTS uploaded_bills (
			content_hash TEXT PRIMARY KEY,
			input_ext    TEXT NOT NULL,
			file_size    INTEGER NOT NULL,
			file_name    TEXT,
			created_at   DATETIME DEFAULT (datetime('now'))
		)`,
		`CREATE TABLE IF NOT EXISTS job_log_digest (
			job_id        TEXT NOT NULL,
			run_id        TEXT NOT NULL,
			error_count   INTEGER NOT NULL DEFAULT 0,
			warn_count    INTEGER NOT NULL DEFAULT 0,
			first_error   TEXT,
			last_lines    TEXT,
			recorded_at   DATETIME DEFAULT (datetime('now')),
			PRIMARY KEY (job_id, run_id)
		)`,
		`CREATE TABLE IF NOT EXISTS review_findings (
			id                TEXT PRIMARY KEY,
			job_id            TEXT NOT NULL,
			aws_li_key        TEXT NOT NULL,
			canonical_service TEXT NOT NULL,
			charge_type       TEXT NOT NULL,
			finding_type      TEXT NOT NULL,
			severity          TEXT NOT NULL,
			aws_od            REAL NOT NULL DEFAULT 0.0,
			gcp_od            REAL NOT NULL DEFAULT 0.0,
			ratio             REAL NOT NULL DEFAULT 0.0,
			evidence_json     TEXT NOT NULL,
			suggested_action  TEXT NOT NULL,
			status            TEXT NOT NULL DEFAULT 'pending',
			action_note       TEXT,
			resolved_by       TEXT,
			resolved_at       DATETIME,
			created_at        DATETIME DEFAULT (datetime('now'))
		)`,
		`ALTER TABLE uploaded_bills ADD COLUMN owner TEXT DEFAULT ''`,
		`CREATE TABLE IF NOT EXISTS job_line_item_overrides (
			job_id        TEXT NOT NULL,
			row_key       TEXT NOT NULL,
			suppressed    INTEGER DEFAULT 0,
			gcp_target    TEXT DEFAULT '',
			override_rate REAL DEFAULT -1,
			pinned_key    TEXT DEFAULT '',
			pinned_val    TEXT DEFAULT '',
			note          TEXT DEFAULT '',
			updated_by    TEXT NOT NULL,
			updated_at    DATETIME DEFAULT (datetime('now')),
			PRIMARY KEY (job_id, row_key)
		)`,
	} {
		if _, err := conn.Exec(ddl); err != nil {
			msg := err.Error()
			if !strings.Contains(msg, "duplicate column") && !strings.Contains(msg, "already exists") {
				return nil, err
			}
		}
	}

	// One-shot repair for existing Facets-domain rows still at 'viewer' to 'admin'.
	// Guarded by schema_migrations so demoting a user to viewer persists across restarts.
	var migrationApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'repair_facets_viewer_roles_2026_10'`).Scan(&migrationApplied); err != nil {
		return nil, err
	}
	if migrationApplied == 0 {
		tx, err := conn.Begin()
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec(`
			UPDATE users
			SET role = 'admin', updated_at = datetime('now')
			WHERE role = 'viewer'
			  AND (email LIKE '%@facets.cloud' OR email LIKE '%@facets.io')
		`); err != nil {
			tx.Rollback()
			return nil, err
		}
		if _, err := tx.Exec(`
			INSERT INTO schema_migrations (name, applied_at) VALUES ('repair_facets_viewer_roles_2026_10', datetime('now'))
		`); err != nil {
			tx.Rollback()
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
	}

	// Backfill assumption categories for pre-existing assumption rows
	var categoryBackfillApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'backfill_assumption_categories_2026_10'`).Scan(&categoryBackfillApplied); err == nil && categoryBackfillApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET category = 'compute_sizing' WHERE key IN ('glue_dpu_vcpu', 'glue_dpu_ram_gib', 'aurora_acu_vcpu', 'aurora_acu_ram_gib', 'lambda_mb_per_vcpu');
				UPDATE assumptions SET category = 'commitment' WHERE key IN ('aws_ri_1yr_discount_pct', 'aws_ri_3yr_discount_pct', 'aws_sp_discount_pct', 'aws_spot_discount_pct');
				UPDATE assumptions SET category = 'other' WHERE key = 'looker_studio_pro_seat_usd';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('backfill_assumption_categories_2026_10', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Merge URL-encoded ("%40") ghost user rows created by the AdminSetUserRole
	// defect before the path param was decoded. Guarded by its own migration row.
	if err := migrateMergeURLEncodedUserRows(conn); err != nil {
		return nil, err
	}

	// Enable assumption and data-gaps sections in pre-existing report_settings rows
	var assumptionSectionsApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'enable_assumption_sections_2026_10'`).Scan(&assumptionSectionsApplied); err == nil && assumptionSectionsApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE report_settings SET value = 'true', default_value = 'true'
				WHERE key IN ('section_assumptions', 'section_data_gaps');
				INSERT INTO schema_migrations (name, applied_at) VALUES ('enable_assumption_sections_2026_10', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Materialized rollup tables for corpus analytics (WORKORDER_analytics_platform)
	var analyticsMigrationApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'create_job_analytics_2026_11'`).Scan(&analyticsMigrationApplied); err == nil && analyticsMigrationApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				CREATE TABLE IF NOT EXISTS job_analytics (
					job_id              TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
					prospect            TEXT,
					owner               TEXT,
					created_at          TEXT,
					currency            TEXT DEFAULT 'USD',
					aws_spend           REAL,
					aws_workload        REAL,
					gcp_od              REAL,
					gcp_1yr_cud         REAL,
					gcp_3yr_cud         REAL,
					savings_od          REAL,
					savings_pct_od      REAL,
					total_rows          INTEGER,
					mapped_rows         INTEGER,
					passthrough_rows    INTEGER,
					ignored_rows        INTEGER,
					llm_rows            INTEGER,
					deterministic_rows  INTEGER,
					unmapped_usd        REAL,
					coverage_pct        REAL,
					confidence_band     TEXT,
					sp_ri_exposed_usd   REAL,
					findings_high       INTEGER,
					findings_med        INTEGER,
					findings_low        INTEGER,
					matrix_decided_pct  REAL,
					catalog_vintage     TEXT,
					assumptions_version TEXT,
					engine_version      TEXT,
					rolled_up_at        TEXT
				);
				CREATE TABLE IF NOT EXISTS job_analytics_breakdown (
					job_id       TEXT REFERENCES jobs(id) ON DELETE CASCADE,
					dimension    TEXT,
					key          TEXT,
					aws_usd      REAL,
					gcp_usd      REAL,
					savings_usd  REAL,
					rows         INTEGER,
					PRIMARY KEY (job_id, dimension, key)
				);
				CREATE INDEX IF NOT EXISTS idx_job_analytics_created ON job_analytics(created_at);
				CREATE INDEX IF NOT EXISTS idx_job_analytics_bd_dim ON job_analytics_breakdown(dimension);
				INSERT INTO schema_migrations (name, applied_at) VALUES ('create_job_analytics_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Align assumption bounds with authoritative data/assumption_domains.json
	var domainBoundsApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'align_assumption_domain_bounds_2026_11'`).Scan(&domainBoundsApplied); err == nil && domainBoundsApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET min_value = 4.0, max_value = 16.0 WHERE key = 'aurora_acu_ram_gib';
				UPDATE assumptions SET min_value = 0.5, max_value = 2.0 WHERE key = 'aurora_acu_vcpu';
				UPDATE assumptions SET max_value = 1.0 WHERE key = 'rds_iops_included_in_storage';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('align_assumption_domain_bounds_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Calibrate nat_vms_per_gateway default 32 -> 36 (drives AWS-OD/GCP-OD ratio
	// to ~1 on representative bills). Always refresh default_value; bump value only
	// where it is still the old default (32) so a deliberate admin edit is preserved.
	var natCalibApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'calibrate_nat_vms_per_gateway_2026_11'`).Scan(&natCalibApplied); err == nil && natCalibApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET value = 36.0, updated_at = datetime('now') WHERE key = 'nat_vms_per_gateway' AND value = 32.0;
				UPDATE assumptions SET default_value = 36.0 WHERE key = 'nat_vms_per_gateway';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('calibrate_nat_vms_per_gateway_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Lambda: re-derive mb_per_vcpu bounds from Cloud Run's CPU:memory cap
	// (max 4096 MB/vCPU, not 10240 — Cloud Run rejects >4 GiB/vCPU) and move the
	// default to 2500 (CPU right-sizing within Cloud Run's envelope, calibrated to
	// AWS-OD/GCP-OD parity). Bump value only where it is still the old default (1769).
	var lambdaCalibApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'calibrate_lambda_mb_per_vcpu_2026_11'`).Scan(&lambdaCalibApplied); err == nil && lambdaCalibApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET value = 2500.0, updated_at = datetime('now') WHERE key = 'lambda_mb_per_vcpu' AND value = 1769.0;
				UPDATE assumptions SET default_value = 2500.0, min_value = 512.0, max_value = 4096.0 WHERE key = 'lambda_mb_per_vcpu';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('calibrate_lambda_mb_per_vcpu_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// DAX/MemoryDB: tighten the lower bound to the smallest real EC2 R-family
	// memory node (2 GiB) now that these domains are grounded in AWS node specs.
	var cacheNodeBoundsApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'align_cache_node_bounds_2026_11'`).Scan(&cacheNodeBoundsApplied); err == nil && cacheNodeBoundsApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET min_value = 2.0 WHERE key IN ('dax_node_gib', 'memorydb_node_gib');
				INSERT INTO schema_migrations (name, applied_at) VALUES ('align_cache_node_bounds_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// DynamoDB RCU/WCU: default to passthrough (0 = carry AWS cost, ratio 1)
	// per pricing_matrix.json — the old default of 1.0 assumed 100% capacity
	// utilization and over-projected ~5-8x against Firestore per-op rates. Lower
	// the floor to 0 so passthrough is selectable, and move value only where it is
	// still the old default (1.0) so a deliberate admin op-mapping edit is kept.
	var ddbPassthroughApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'calibrate_dynamodb_passthrough_2026_11'`).Scan(&ddbPassthroughApplied); err == nil && ddbPassthroughApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET value = 0.0, updated_at = datetime('now') WHERE key IN ('dynamodb_rcu_to_reads_sec', 'dynamodb_wcu_to_writes_sec') AND value = 1.0;
				UPDATE assumptions SET default_value = 0.0, min_value = 0.0 WHERE key IN ('dynamodb_rcu_to_reads_sec', 'dynamodb_wcu_to_writes_sec');
				INSERT INTO schema_migrations (name, applied_at) VALUES ('calibrate_dynamodb_passthrough_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Inferentia: no public Inferentia-vs-TPU benchmark exists, so default to
	// passthrough (0 = carry AWS cost, ratio 1) rather than asserting a price or
	// performance equivalence. Lower the floor to 0 and move value only where it
	// is still a prior default (1.0 original, or 0.19 interim parity value).
	var infPassthroughApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'calibrate_inferentia_passthrough_2026_11'`).Scan(&infPassthroughApplied); err == nil && infPassthroughApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET value = 0.0, updated_at = datetime('now') WHERE key = 'inferentia_chip_to_tpu' AND value IN (1.0, 0.19);
				UPDATE assumptions SET default_value = 0.0, min_value = 0.0 WHERE key = 'inferentia_chip_to_tpu';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('calibrate_inferentia_passthrough_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Looker Studio Pro was renamed back to Data Studio Pro (Google, Apr 2026).
	// Refresh the display title/rationale/unit on existing DBs (the key is stable).
	var dspRenameApplied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'rename_looker_to_data_studio_pro_2026_11'`).Scan(&dspRenameApplied); err == nil && dspRenameApplied == 0 {
		tx, err := conn.Begin()
		if err == nil {
			_, _ = tx.Exec(`
				UPDATE assumptions SET title = 'Data Studio Pro seat rate', rationale = 'Data Studio Pro (formerly Looker Studio Pro; Google reverted the name Apr 2026) — $9 per user per project per month published list price', unit = 'USD/user/project/mo' WHERE key = 'looker_studio_pro_seat_usd';
				INSERT INTO schema_migrations (name, applied_at) VALUES ('rename_looker_to_data_studio_pro_2026_11', datetime('now'));
			`)
			_ = tx.Commit()
		}
	}

	// Seed assumptions table with today's values (INSERT OR IGNORE — never
	// overwrite an admin's edit on restart).
	if _, err := conn.Exec(`
		INSERT OR IGNORE INTO assumptions (key, value, default_value, min_value, max_value, unit, title, rationale, category) VALUES
			('glue_dpu_vcpu', 4.0, 4.0, 1.0, 32.0, 'vCPU', 'Glue DPU to vCPU', '1 AWS Glue standard DPU (G.1X worker) = 4 vCPU per AWS Glue worker-type spec', 'compute_sizing'),
			('glue_dpu_ram_gib', 16.0, 16.0, 1.0, 256.0, 'GiB', 'Glue DPU to RAM', '1 AWS Glue standard DPU (G.1X worker) = 16 GiB per AWS Glue worker-type spec', 'compute_sizing'),
			('looker_studio_pro_seat_usd', 9.0, 9.0, 0.0, 100.0, 'USD/user/project/mo', 'Data Studio Pro seat rate', 'Data Studio Pro (formerly Looker Studio Pro; Google reverted the name Apr 2026) — $9 per user per project per month published list price', 'other'),
			('aurora_acu_vcpu', 2.0, 2.0, 0.5, 2.0, 'vCPU', 'Aurora ACU to vCPU', '1 Aurora Serverless v2 ACU ≈ 2 vCPU (empirical: 16 ACU ≈ 4 vCPU ≈ db.r5.xlarge)', 'compute_sizing'),
			('aurora_acu_ram_gib', 16.0, 16.0, 4.0, 16.0, 'GiB', 'Aurora ACU to RAM', '1 Aurora Serverless v2 ACU ≈ 16 GiB RAM (empirical: 16 ACU ≈ 32 GiB ≈ db.r5.xlarge)', 'compute_sizing'),
			('lambda_mb_per_vcpu', 2500.0, 2500.0, 512.0, 4096.0, 'MB', 'Lambda MB per vCPU (Cloud Run sizing)', 'Memory-per-vCPU used to size Cloud Run CPU for migrated Lambda functions. AWS allocates 1 vCPU/1769 MB; Cloud Run caps memory at 4096 MB/vCPU. 2500 = CPU right-sizing within that envelope, calibrated to AWS-OD/GCP-OD parity', 'compute_sizing'),
			('aws_ri_1yr_discount_pct', 0.38, 0.38, 0.0, 0.90, 'fraction', 'AWS 1-Year RI Discount', 'Published typical AWS 1-year reserved instance discount rate (~38%)', 'commitment'),
			('aws_ri_3yr_discount_pct', 0.52, 0.52, 0.0, 0.90, 'fraction', 'AWS 3-Year RI Discount', 'Published typical AWS 3-year reserved instance discount rate (~52%)', 'commitment'),
			('aws_sp_discount_pct', 0.30, 0.30, 0.0, 0.90, 'fraction', 'AWS Savings Plans Discount', 'Published typical AWS Compute Savings Plans discount rate (~30%)', 'commitment'),
			('aws_spot_discount_pct', 0.70, 0.70, 0.0, 0.95, 'fraction', 'AWS Spot Discount', 'Published typical AWS Spot instance discount rate (~70%)', 'commitment'),
			('nat_vms_per_gateway', 36.0, 36.0, 1.0, 200.0, 'VMs', 'Cloud NAT assumed VMs per gateway', 'Number of VMs assumed behind each NAT gateway for Cloud NAT per-VM-hour pricing; 36 calibrated to drive AWS-OD/GCP-OD ratio to 1 on representative bills', 'networking'),
			('dax_node_gib', 16.0, 16.0, 2.0, 512.0, 'GiB', 'DAX node RAM GiB', 'Assumed RAM for a DAX node when CUR lacks the type. DAX runs on EC2 R-family nodes (large=16 GiB, the common/default node, up to 16xlarge=512 GiB). GCP output capped by Memorystore M5 tier (~300 GiB).', 'compute_sizing'),
			('memorydb_node_gib', 16.0, 16.0, 2.0, 512.0, 'GiB', 'MemoryDB node RAM GiB', 'Assumed RAM for a MemoryDB node when CUR lacks the type. MemoryDB runs on EC2 R-family nodes (large=16 GiB default, up to 16xlarge=512 GiB). GCP output capped by Memorystore M5 tier (~300 GiB).', 'compute_sizing'),
			('inferentia_chip_to_tpu', 0.0, 0.0, 0.0, 10.0, 'ratio', 'Inferentia chip to Cloud TPU v5e ratio', '0 = passthrough at AWS cost (default): no public Inferentia-vs-TPU benchmark exists, so passthrough avoids asserting an unverified equivalence. Set >0 to map to Cloud TPU v5e at a declared price ratio (~0.19 = inf1.xlarge price parity).', 'compute_sizing'),
			('trainium_chip_to_tpu', 1.0, 1.0, 0.1, 10.0, 'ratio', 'Trainium chip to Cloud TPU v5e ratio', 'Assumed chip equivalence factor between AWS Trainium and Cloud TPU v5e', 'compute_sizing'),
			('dynamodb_rcu_to_reads_sec', 0.0, 0.0, 0.0, 100.0, 'ratio', 'DynamoDB RCU to Reads/sec', '0 = passthrough at AWS cost (default): DynamoDB→Firestore op volume is workload-dependent, and translating 1 RCU as 3600 reads/hr at Firestore op rates over-projects ~8x. Set >0 to map to Cloud Firestore Read Ops (reads/sec per RCU).', 'storage'),
			('dynamodb_wcu_to_writes_sec', 0.0, 0.0, 0.0, 100.0, 'ratio', 'DynamoDB WCU to Writes/sec', '0 = passthrough at AWS cost (default): DynamoDB→Firestore op volume is workload-dependent, and translating 1 WCU as 3600 writes/hr at Firestore op rates over-projects ~5x. Set >0 to map to Cloud Firestore Entity Writes (writes/sec per WCU).', 'storage'),
			('rds_iops_included_in_storage', 1.0, 1.0, 0.0, 1.0, 'flag', 'RDS Provisioned IOPS included in storage', '1.0 if provisioned IOPS are treated as included in Cloud SQL storage rate ($0), 0.0 to price separately', 'storage'),
			('fx_max_staleness_days', 7.0, 7.0, 1.0, 90.0, 'days', 'FX max staleness', 'Maximum age in days before FX rate is considered stale', 'pricing_policy')
			-- Cognito MAU estimation is LLM-guided with no deterministic call site; re-add this key when a Cognito mapper exists.
	`); err != nil {
		return nil, fmt.Errorf("seed assumptions: %w", err)
	}

	// Seed report_settings with starter toggles (INSERT OR IGNORE — never
	// overwrite an admin's edit on restart).
	if _, err := conn.Exec(`
		INSERT OR IGNORE INTO report_settings (key, value, value_type, default_value, min_value, max_value, title, description, group_name, sort_order) VALUES
			-- group 'columns' (bool)
			('col_gcp_cud_1yr', 'true', 'bool', 'true', NULL, NULL, 'GCP 1-year CUD', 'Column showing GCP cost with a 1-year commitment', 'columns', 1),
			('col_gcp_cud_3yr', 'true', 'bool', 'true', NULL, NULL, 'GCP 3-year CUD', 'Column showing GCP cost with a 3-year commitment', 'columns', 2),
			('col_diff', 'true', 'bool', 'true', NULL, NULL, 'Difference', 'AWS cost minus GCP on-demand', 'columns', 3),
			('col_pct_of_aws', 'true', 'bool', 'true', NULL, NULL, 'GCP as % of AWS', 'GCP on-demand as a percentage of AWS', 'columns', 4),
			('col_line_items', 'false', 'bool', 'false', NULL, NULL, 'Line item count', 'How many bill lines rolled into this row', 'columns', 5),
			('col_region', 'false', 'bool', 'false', NULL, NULL, 'AWS Region', 'AWS source region (the GCP target region is always shown)', 'columns', 6),
			-- group 'sections' (bool)
			('section_discounts', 'true', 'bool', 'true', NULL, NULL, 'Discounts and credits', 'AWS discounts applied to this bill', 'sections', 1),
			('section_wins_losses', 'true', 'bool', 'true', NULL, NULL, 'Where GCP wins and loses', 'Per-service winners and losers', 'sections', 2),
			('section_assumptions', 'true', 'bool', 'true', NULL, NULL, 'Assumptions used', 'The assumption values this run used', 'sections', 3),
			('section_data_gaps', 'true', 'bool', 'true', NULL, NULL, 'Data gaps', 'Rows that could not be fully priced', 'sections', 4),
			('section_coverage', 'true', 'bool', 'true', NULL, NULL, 'Coverage statement', 'Customer-facing line stating what share of workload spend was priced against a specific GCP SKU', 'sections', 5),
			-- group 'rows' (number)
			('row_min_cost_usd', '0', 'number', '0', 0.0, 1000000.0, 'Hide rows below this cost', 'Rows cheaper than this are not listed. Totals still include them.', 'rows', 1),
			-- group 'engine' (enum/string)
			('default_engine', 'gemini-3.8-flash', 'string', 'gemini-3.8-flash', NULL, NULL, 'Default Engine', 'The model used when the user does not choose', 'engine', 1),
			('allowed_engines', 'gemini-3.8-flash,gemini-2.5-pro,gemini-2.5-flash', 'string', 'gemini-3.8-flash,gemini-2.5-pro,gemini-2.5-flash', NULL, NULL, 'Allowed Engines', 'Comma-separated list of engines the user may pick from', 'engine', 2),
			-- group 'quotas' (number)
			('max_jobs_per_day', '20', 'number', '20', 1.0, 10000.0, 'Max Jobs Per Day', 'Maximum jobs a non-admin user may submit in a rolling 24-hour window', 'quotas', 1),
			('max_concurrent_jobs', '2', 'number', '2', 1.0, 100.0, 'Max Concurrent Jobs', 'Maximum concurrent running jobs for a non-admin user', 'quotas', 2)
	`); err != nil {
		return nil, fmt.Errorf("seed report_settings: %w", err)
	}

	return &DB{conn: conn}, nil
}

func (d *DB) Close() error {
	return d.conn.Close()
}

// Conn returns the underlying *sql.DB, exposed for tests and one-off migrations.
func (d *DB) Conn() *sql.DB {
	return d.conn
}

// ExecForTest executes arbitrary SQL (test helper only).
func (d *DB) ExecForTest(query string, args ...interface{}) (sql.Result, error) {
	return d.conn.Exec(query, args...)
}

// QueryRowForTest returns a *sql.Row for arbitrary SELECTs (test helper only).
func (d *DB) QueryRowForTest(query string, args ...interface{}) *sql.Row {
	return d.conn.QueryRow(query, args...)
}

// AssumptionCategories is the canonical closed list of assumption categories.
var AssumptionCategories = []string{
	"compute_sizing",
	"licensing",
	"storage",
	"networking",
	"commitment",
	"pricing_policy",
	"other",
}

// IsValidAssumptionCategory reports whether cat is a recognized category.
func IsValidAssumptionCategory(cat string) bool {
	for _, c := range AssumptionCategories {
		if c == cat {
			return true
		}
	}
	return false
}

// Assumption represents a single pricing assumption row.
type Assumption struct {
	Key          string            `json:"key"`
	Value        float64           `json:"value"`
	DefaultValue float64           `json:"default_value"`
	MinValue     float64           `json:"min_value"`
	MaxValue     float64           `json:"max_value"`
	Unit         string            `json:"unit"`
	Title        string            `json:"title"`
	Rationale    string            `json:"rationale"`
	Category     string            `json:"category"`
	Scope        string            `json:"scope"`
	Domain       *AssumptionDomain `json:"domain,omitempty"`
	UpdatedBy    string            `json:"updated_by,omitempty"`
	UpdatedAt    string            `json:"updated_at,omitempty"`
}

// GetAssumptions returns all assumption rows.
func (d *DB) GetAssumptions() ([]*Assumption, error) {
	rows, err := d.conn.Query(`SELECT key, value, default_value, min_value, max_value, unit, title, rationale, category, scope, COALESCE(updated_by,''), COALESCE(updated_at,'') FROM assumptions ORDER BY key`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	domains, _ := LoadAssumptionDomains()

	var assumptions []*Assumption
	for rows.Next() {
		var a Assumption
		if err := rows.Scan(&a.Key, &a.Value, &a.DefaultValue, &a.MinValue, &a.MaxValue, &a.Unit, &a.Title, &a.Rationale, &a.Category, &a.Scope, &a.UpdatedBy, &a.UpdatedAt); err != nil {
			return nil, err
		}
		if domains != nil {
			a.Domain = domains[a.Key]
		}
		assumptions = append(assumptions, &a)
	}
	return assumptions, nil
}

// CreateAssumption inserts a new assumption row, validating category.
func (d *DB) CreateAssumption(a *Assumption, createdBy string) error {
	if a == nil {
		return fmt.Errorf("nil assumption")
	}
	if a.Category == "" {
		a.Category = "other"
	}
	if !IsValidAssumptionCategory(a.Category) {
		return fmt.Errorf("invalid category: %s", a.Category)
	}
	if a.Scope == "" {
		a.Scope = "global"
	}
	_, err := d.conn.Exec(`
		INSERT INTO assumptions (key, value, default_value, min_value, max_value, unit, title, rationale, category, scope, updated_by, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, a.Key, a.Value, a.DefaultValue, a.MinValue, a.MaxValue, a.Unit, a.Title, a.Rationale, a.Category, a.Scope, createdBy)
	return err
}

// AssumptionChange represents a single row in the assumption_changes audit table.
type AssumptionChange struct {
	ID         int     `json:"id"`
	Key        string  `json:"key"`
	OldValue   float64 `json:"old_value"`
	NewValue   float64 `json:"new_value"`
	Reason     string  `json:"reason"`
	ChangedBy  string  `json:"changed_by"`
	ChangedAt  string  `json:"changed_at"`
}

// GetAssumptionHistory returns assumption changes, newest first.
func (d *DB) GetAssumptionHistory() ([]*AssumptionChange, error) {
	rows, err := d.conn.Query(`SELECT id, key, old_value, new_value, reason, changed_by, changed_at FROM assumption_changes ORDER BY changed_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var changes []*AssumptionChange
	for rows.Next() {
		var c AssumptionChange
		if err := rows.Scan(&c.ID, &c.Key, &c.OldValue, &c.NewValue, &c.Reason, &c.ChangedBy, &c.ChangedAt); err != nil {
			return nil, err
		}
		changes = append(changes, &c)
	}
	return changes, nil
}

// UpdateAssumption updates an assumption value within bounds, records the
// change in assumption_changes, and returns the new version hash. All in one
// transaction.
func (d *DB) UpdateAssumption(key string, value float64, reason, changedBy string, category ...string) (string, error) {
	// Validate bounds
	var minV, maxV float64
	err := d.conn.QueryRow(`SELECT min_value, max_value FROM assumptions WHERE key = ?`, key).Scan(&minV, &maxV)
	if err == sql.ErrNoRows {
		return "", fmt.Errorf("assumption not found: %s", key)
	}
	if err != nil {
		return "", err
	}
	if value < minV || value > maxV {
		return "", fmt.Errorf("value %g out of range [%g, %g]", value, minV, maxV)
	}

	cat := ""
	if len(category) > 0 && category[0] != "" {
		cat = category[0]
		if !IsValidAssumptionCategory(cat) {
			return "", fmt.Errorf("invalid category: %s", cat)
		}
	}

	// Validate ratio groups against the shared registry (data/ratio_registry.json),
	// which is generated from candidate_pool.py — so adding a GCP machine family
	// widens the allowed set here with no change to this file.
	if err := d.validateRatioGroups(key, value); err != nil {
		return "", err
	}

	tx, err := d.conn.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	var oldValue float64
	if err := tx.QueryRow(`SELECT value FROM assumptions WHERE key = ?`, key).Scan(&oldValue); err != nil {
		return "", err
	}

	if cat != "" {
		if _, err := tx.Exec(`UPDATE assumptions SET value = ?, category = ?, updated_by = ?, updated_at = datetime('now') WHERE key = ?`, value, cat, changedBy, key); err != nil {
			return "", err
		}
	} else {
		if _, err := tx.Exec(`UPDATE assumptions SET value = ?, updated_by = ?, updated_at = datetime('now') WHERE key = ?`, value, changedBy, key); err != nil {
			return "", err
		}
	}
	if _, err := tx.Exec(`INSERT INTO assumption_changes (key, old_value, new_value, reason, changed_by) VALUES (?, ?, ?, ?, ?)`, key, oldValue, value, reason, changedBy); err != nil {
		return "", err
	}

	if err := tx.Commit(); err != nil {
		return "", err
	}

	return d.assumptionsVersionHash()
}

// ResetAssumptions reverts every assumption's value to its default_value, records
// each change in assumption_changes, and returns the new version hash. All in one
// transaction. Mirrors ResetReportSettings.
func (d *DB) ResetAssumptions(changedBy string) (string, error) {
	tx, err := d.conn.Begin()
	if err != nil {
		return "", err
	}
	defer tx.Rollback()

	rows, err := tx.Query(`SELECT key, value, default_value FROM assumptions`)
	if err != nil {
		return "", err
	}
	type kv struct {
		key           string
		oldVal, defVal float64
	}
	var pairs []kv
	for rows.Next() {
		var k string
		var ov, dv float64
		if err := rows.Scan(&k, &ov, &dv); err != nil {
			rows.Close()
			return "", err
		}
		pairs = append(pairs, kv{k, ov, dv})
	}
	rows.Close()

	for _, p := range pairs {
		if p.oldVal == p.defVal {
			continue // already at default — no write, no audit noise
		}
		if _, err := tx.Exec(`UPDATE assumptions SET value = ?, updated_by = ?, updated_at = datetime('now') WHERE key = ?`,
			p.defVal, changedBy, p.key); err != nil {
			return "", err
		}
		if _, err := tx.Exec(`INSERT INTO assumption_changes (key, old_value, new_value, reason, changed_by) VALUES (?, ?, ?, ?, ?)`,
			p.key, p.oldVal, p.defVal, "Reset to default", changedBy); err != nil {
			return "", err
		}
	}

	if err := tx.Commit(); err != nil {
		return "", err
	}

	return d.assumptionsVersionHash()
}

// AssumptionsVersionHash returns the public version hash for the assumption set.
func (d *DB) AssumptionsVersionHash() (string, error) {
	return d.assumptionsVersionHash()
}

// assumptionsVersionHash returns SHA-256 of sorted key=value pairs, first 12 hex chars.
func (d *DB) assumptionsVersionHash() (string, error) {
	rows, err := d.conn.Query(`SELECT key, value FROM assumptions ORDER BY key`)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var parts []string
	for rows.Next() {
		var key string
		var value float64
		if err := rows.Scan(&key, &value); err != nil {
			return "", err
		}
		parts = append(parts, fmt.Sprintf("%s=%g", key, value))
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, ";")))
	return hex.EncodeToString(sum[:])[:12], nil
}

// MergeURLEncodedUserRows collapses ghost rows whose email still carries a
// URL-encoded "%40" into the genuine row for the decoded address. Runs once,
// guarded by its own migration row. Exported for tests.
// migrateMergeURLEncodedUserRows runs the merge once at startup, guarded by a
// schema_migrations row. The merge itself is naturally idempotent — once the
// ghosts are gone there is nothing left matching '%' — so the guard is only an
// optimisation, which is why MergeURLEncodedUserRows below is callable
// directly and does not consult it.
func migrateMergeURLEncodedUserRows(conn *sql.DB) error {
	var applied int
	if err := conn.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE name = 'merge_urlencoded_user_rows_2026_10'`).Scan(&applied); err != nil {
		return err
	}
	if applied != 0 {
		return nil
	}
	return MergeURLEncodedUserRows(conn)
}

func MergeURLEncodedUserRows(conn *sql.DB) error {
	tx, err := conn.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// Find ghost rows whose email contains a literal percent sign.
	rows, err := tx.Query(`SELECT email, role, admin_granted_by, admin_granted_at, created_at, updated_at FROM users WHERE email LIKE '%\%%' ESCAPE '\'`)
	if err != nil {
		return err
	}
	defer rows.Close()

	type ghost struct {
		email          string
		role           string
		grantedBy      string
		grantedAt      string
		createdAt      string
		updatedAt      string
	}
	var ghosts []ghost
	for rows.Next() {
		var g ghost
		var grantedBy, grantedAt sql.NullString
		if err := rows.Scan(&g.email, &g.role, &grantedBy, &grantedAt, &g.createdAt, &g.updatedAt); err != nil {
			return err
		}
		g.grantedBy = grantedBy.String
		g.grantedAt = grantedAt.String
		ghosts = append(ghosts, g)
	}

	for _, g := range ghosts {
		decoded, err := url.PathUnescape(g.email)
		if err != nil {
			log.Printf("[merge_urlencoded_user_rows] %q: path unescape failed: %v; leaving row alone", g.email, err)
			continue
		}
		if strings.Contains(decoded, "%") {
			log.Printf("[merge_urlencoded_user_rows] %q: decodes to %q which still contains %%; leaving row alone", g.email, decoded)
			continue
		}
		decoded = strings.ToLower(strings.TrimSpace(decoded))
		if decoded == "" || !strings.Contains(decoded, "@") || strings.HasPrefix(decoded, "@") || strings.HasSuffix(decoded, "@") {
			log.Printf("[merge_urlencoded_user_rows] %q: decodes to invalid address %q; leaving row alone", g.email, decoded)
			continue
		}

		// Does a genuine row already exist for the decoded address?
		var genuineRole string
		var genuineGrantedBy, genuineGrantedAt sql.NullString
		var genuineCreatedAt, genuineUpdatedAt string
		err = tx.QueryRow(`SELECT role, admin_granted_by, admin_granted_at, created_at, updated_at FROM users WHERE email = ?`, decoded).Scan(&genuineRole, &genuineGrantedBy, &genuineGrantedAt, &genuineCreatedAt, &genuineUpdatedAt)
		if err == sql.ErrNoRows {
			// No genuine row: rename the ghost row to the decoded address.
			if _, err := tx.Exec(`UPDATE users SET email = ?, updated_at = datetime('now') WHERE email = ?`, decoded, g.email); err != nil {
				return err
			}
			if _, err := tx.Exec(`INSERT INTO admin_audit (actor_email, action, target_id, details) VALUES (?, 'user.merge_encoded', ?, ?)`, "system", decoded, fmt.Sprintf("renamed ghost %q to %q (role=%s)", g.email, decoded, g.role)); err != nil {
				return err
			}
			log.Printf("[merge_urlencoded_user_rows] %q -> %q: no genuine row, renamed (role=%s)", g.email, decoded, g.role)
			continue
		}
		if err != nil {
			return err
		}

		// Merge: ghost into genuine, then delete ghost.
		newRole := genuineRole
		if genuineRole == "viewer" && g.role == "admin" {
			newRole = "admin"
		}
		newGrantedBy := genuineGrantedBy.String
		if newGrantedBy == "" {
			newGrantedBy = g.grantedBy
		}
		newGrantedAt := genuineGrantedAt.String
		if newGrantedAt == "" {
			newGrantedAt = g.grantedAt
		}
		// keep later updated_at
		newUpdatedAt := genuineUpdatedAt
		if g.updatedAt > newUpdatedAt {
			newUpdatedAt = g.updatedAt
		}

		if _, err := tx.Exec(`
			UPDATE users SET role = ?, admin_granted_by = ?, admin_granted_at = ?, updated_at = ? WHERE email = ?
		`, newRole, newGrantedBy, newGrantedAt, newUpdatedAt, decoded); err != nil {
			return err
		}
		if _, err := tx.Exec(`DELETE FROM users WHERE email = ?`, g.email); err != nil {
			return err
		}
		if _, err := tx.Exec(`INSERT INTO admin_audit (actor_email, action, target_id, details) VALUES (?, 'user.merge_encoded', ?, ?)`, "system", decoded, fmt.Sprintf("merged ghost %q (role=%s) into genuine %q (role=%s -> %s, granted_by=%s)", g.email, g.role, decoded, genuineRole, newRole, newGrantedBy)); err != nil {
			return err
		}
		log.Printf("[merge_urlencoded_user_rows] %q -> %q: merged (role %s -> %s, granted_by=%s)", g.email, decoded, g.role, newRole, newGrantedBy)
	}

	if err := tx.Commit(); err != nil {
		return err
	}

	if _, err := conn.Exec(`INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES ('merge_urlencoded_user_rows_2026_10', datetime('now'))`); err != nil {
		return err
	}
	return nil
}

