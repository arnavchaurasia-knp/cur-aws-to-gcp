// Package logger initialises the global slog logger and bridges stdlib log to it.
// Call Init() once at startup. All log.Printf / slog.Info calls after that
// produce JSON lines consumable by GCP Cloud Logging.
package logger

import (
	"log"
	"log/slog"
	"os"
	"strings"
)

// level is shared by the handler so the log level can change at runtime
// (System Health → Settings & Secrets → LOG_LEVEL) without a restart.
var level slog.LevelVar

// ApplyLevelFromEnv re-reads LOG_LEVEL (info|debug|warn|error; default info)
// and applies it immediately.
func ApplyLevelFromEnv() {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("LOG_LEVEL"))) {
	case "debug":
		level.Set(slog.LevelDebug)
	case "warn", "warning":
		level.Set(slog.LevelWarn)
	case "error":
		level.Set(slog.LevelError)
	default:
		level.Set(slog.LevelInfo)
	}
}

// Init sets the default slog logger (JSON unless LOG_FORMAT=text) and redirects
// the stdlib log package to slog so existing log.Printf calls are also structured.
func Init() {
	ApplyLevelFromEnv()
	opts := &slog.HandlerOptions{Level: &level}

	var h slog.Handler
	if strings.EqualFold(os.Getenv("LOG_FORMAT"), "text") {
		h = slog.NewTextHandler(os.Stdout, opts)
	} else {
		h = slog.NewJSONHandler(os.Stdout, opts)
	}

	l := slog.New(h)
	slog.SetDefault(l)

	// Bridge stdlib log → slog so legacy log.Printf / log.Fatal calls are structured.
	log.SetFlags(0)
	log.SetOutput(&bridge{l: l})
}

type bridge struct{ l *slog.Logger }

func (b *bridge) Write(p []byte) (int, error) {
	msg := strings.TrimRight(string(p), "\n")
	// Emit at Error for messages that look like fatal/error conditions so they
	// surface in severity-filtered monitoring (e.g. GCP Cloud Logging ERROR+).
	// log.Fatal* calls os.Exit(1) after this write; the level matters for ops.
	lower := strings.ToLower(msg)
	if strings.Contains(lower, "error") || strings.Contains(lower, "fail") ||
		strings.Contains(lower, "fatal") || strings.Contains(lower, "panic") {
		b.l.Error(msg)
	} else {
		b.l.Info(msg)
	}
	return len(p), nil
}
