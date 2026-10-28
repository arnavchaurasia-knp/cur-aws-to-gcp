package jobs

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/facets/cur-web/internal/auth"
	"github.com/go-chi/chi/v5"
)

// LogEntry represents a structured pipeline or engine log line.
type LogEntry struct {
	Timestamp string `json:"timestamp,omitempty"`
	Level     string `json:"level"`
	Logger    string `json:"logger,omitempty"`
	Message   string `json:"message"`
	Raw       string `json:"raw"`
}

// LogResponse is returned by GET /api/jobs/{id}/logs
type LogResponse struct {
	JobID     string      `json:"job_id"`
	Stream    string      `json:"stream"`
	Available bool        `json:"available"`
	Source    string      `json:"source"` // "live" or "digest"
	Truncated bool        `json:"truncated,omitempty"`
	Total     int         `json:"total"`
	Offset    int         `json:"offset"`
	Limit     int         `json:"limit"`
	Entries   []LogEntry  `json:"entries,omitempty"`
	Lines     []string    `json:"lines,omitempty"`
	Records   []any       `json:"records,omitempty"`
	Digest    any         `json:"digest,omitempty"`
}

// Regex for Python log_utils format: "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
// Example: "2026-10-28T13:45:00  INFO      ingest.py  Processing started"
var logLineRegex = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+([A-Z]+)\s+([^\s]+)\s+(.*)$`)

func parseLogLine(raw string) LogEntry {
	m := logLineRegex.FindStringSubmatch(raw)
	if m != nil {
		return LogEntry{
			Timestamp: m[1],
			Level:     strings.ToLower(strings.TrimSpace(m[2])),
			Logger:    m[3],
			Message:   m[4],
			Raw:       raw,
		}
	}
	// Try parsing level if present
	upper := strings.ToUpper(raw)
	lvl := "unknown"
	if strings.Contains(upper, "ERROR") || strings.Contains(upper, "CRITICAL") {
		lvl = "error"
	} else if strings.Contains(upper, "WARN") {
		lvl = "warning"
	} else if strings.Contains(upper, "INFO") {
		lvl = "info"
	} else if strings.Contains(upper, "DEBUG") {
		lvl = "debug"
	}
	return LogEntry{
		Level:   lvl,
		Message: raw,
		Raw:     raw,
	}
}

var levelPriority = map[string]int{
	"debug":    1,
	"info":     2,
	"warning":  3,
	"warn":     3,
	"error":    4,
	"critical": 5,
}

func levelMatches(entryLevel, minLevel string) bool {
	if minLevel == "" || minLevel == "debug" {
		return true
	}
	if entryLevel == "unknown" {
		return true // Always include unparseable lines as instructed
	}
	minP := levelPriority[strings.ToLower(minLevel)]
	entryP := levelPriority[strings.ToLower(entryLevel)]
	return entryP >= minP
}

// JobLogs handles GET /api/jobs/{id}/logs
func (h *Handler) JobLogs(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	id := chi.URLParam(r, "id")
	if !isValidJobID(id) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	job, err := h.db.GetJob(id)
	if err != nil || job == nil || !h.canAccess(sess, job) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	jobDir := filepath.Clean(filepath.Join(h.jobsDir, id))
	if !strings.HasPrefix(jobDir, filepath.Clean(h.jobsDir)+string(filepath.Separator)) {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	stream := r.URL.Query().Get("stream")
	if stream == "" {
		stream = "pipeline"
	}

	limit := 200
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 {
			limit = n
		}
	}
	if limit > 2000 {
		limit = 2000
	}

	offset := 0
	if o := r.URL.Query().Get("offset"); o != "" {
		if n, err := strconv.Atoi(o); err == nil && n >= 0 {
			offset = n
		}
	}

	w.Header().Set(contentTypeHeader, contentTypeJSON)

	// Check if directory exists
	dirInfo, statErr := os.Stat(jobDir)
	dirExists := (statErr == nil && dirInfo.IsDir())

	if !dirExists {
		// Rule 3 & 1.4: Job directory deleted. Try serving saved log digest.
		digest, _ := h.db.GetJobLogDigest(id, "initial")
		if digest != nil {
			var lines []string
			if digest.LastLines != "" {
				lines = strings.Split(digest.LastLines, "\n")
			}
			json.NewEncoder(w).Encode(LogResponse{
				JobID:     id,
				Stream:    stream,
				Available: true,
				Source:    "digest",
				Truncated: true,
				Total:     len(lines),
				Lines:     lines,
				Digest:    digest,
			})
			return
		}
		// No directory and no digest -> available: false, HTTP 200
		json.NewEncoder(w).Encode(LogResponse{
			JobID:     id,
			Stream:    stream,
			Available: false,
			Source:    "none",
		})
		return
	}

	switch stream {
	case "engine":
		filePath := filepath.Join(jobDir, "agy.log")
		if _, err := os.Stat(filePath); os.IsNotExist(err) {
			json.NewEncoder(w).Encode(LogResponse{
				JobID:     id,
				Stream:    stream,
				Available: false,
				Source:    "live",
			})
			return
		}
		lines := readLines(filePath)
		total := len(lines)
		sliced := sliceItems(lines, offset, limit)
		json.NewEncoder(w).Encode(LogResponse{
			JobID:     id,
			Stream:    stream,
			Available: true,
			Source:    "live",
			Total:     total,
			Offset:    offset,
			Limit:     limit,
			Lines:     sliced,
		})

	case "diagnostics":
		filePath := filepath.Join(jobDir, "projection-audit", "diagnostics.jsonl")
		if _, err := os.Stat(filePath); os.IsNotExist(err) {
			json.NewEncoder(w).Encode(LogResponse{
				JobID:     id,
				Stream:    stream,
				Available: false,
				Source:    "live",
			})
			return
		}
		records := readJSONL(filePath)
		total := len(records)
		sliced := sliceItems(records, offset, limit)
		json.NewEncoder(w).Encode(LogResponse{
			JobID:     id,
			Stream:    stream,
			Available: true,
			Source:    "live",
			Total:     total,
			Offset:    offset,
			Limit:     limit,
			Records:   sliced,
		})

	default: // "pipeline"
		filePath := filepath.Join(jobDir, "projection-audit", "debug.log")
		if _, err := os.Stat(filePath); os.IsNotExist(err) {
			// Check if agy.log can fallback
			altPath := filepath.Join(jobDir, "agy.log")
			if _, err := os.Stat(altPath); os.IsNotExist(err) {
				json.NewEncoder(w).Encode(LogResponse{
					JobID:     id,
					Stream:    stream,
					Available: false,
					Source:    "live",
				})
				return
			}
			filePath = altPath
		}

		minLevel := r.URL.Query().Get("level")
		scriptFilter := strings.ToLower(r.URL.Query().Get("script"))
		searchQuery := strings.ToLower(r.URL.Query().Get("q"))

		rawLines := readLines(filePath)
		var filtered []LogEntry
		for _, raw := range rawLines {
			entry := parseLogLine(raw)
			if !levelMatches(entry.Level, minLevel) {
				continue
			}
			if scriptFilter != "" && !strings.Contains(strings.ToLower(entry.Logger), scriptFilter) {
				continue
			}
			if searchQuery != "" && !strings.Contains(strings.ToLower(raw), searchQuery) {
				continue
			}
			filtered = append(filtered, entry)
		}

		total := len(filtered)
		sliced := sliceItems(filtered, offset, limit)
		json.NewEncoder(w).Encode(LogResponse{
			JobID:     id,
			Stream:    stream,
			Available: true,
			Source:    "live",
			Total:     total,
			Offset:    offset,
			Limit:     limit,
			Entries:   sliced,
		})
	}
}

func readLines(path string) []string {
	f, err := os.Open(path)
	if err != nil {
		return []string{}
	}
	defer f.Close()

	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		lines = append(lines, sc.Text())
	}
	return lines
}

func readJSONL(path string) []any {
	f, err := os.Open(path)
	if err != nil {
		return []any{}
	}
	defer f.Close()

	var records []any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var rec any
		if err := json.Unmarshal([]byte(line), &rec); err == nil {
			records = append(records, rec)
		} else {
			records = append(records, map[string]any{"raw": line})
		}
	}
	return records
}

func sliceItems[T any](items []T, offset, limit int) []T {
	n := len(items)
	if offset >= n {
		return []T{}
	}
	end := offset + limit
	if end > n {
		end = n
	}
	return items[offset:end]
}
