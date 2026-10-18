package db

import (
	"database/sql"
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
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now')),
    cancelled_at DATETIME
);

CREATE TABLE IF NOT EXISTS users (
    email      TEXT PRIMARY KEY,
    role       TEXT NOT NULL DEFAULT 'viewer',
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now'))
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
);`

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
	} {
		if _, err := conn.Exec(ddl); err != nil {
			msg := err.Error()
			if !strings.Contains(msg, "duplicate column") && !strings.Contains(msg, "already exists") {
				return nil, err
			}
		}
	}
	return &DB{conn: conn}, nil
}

func (d *DB) Close() error {
	return d.conn.Close()
}
