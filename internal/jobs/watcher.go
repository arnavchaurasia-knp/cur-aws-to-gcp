package jobs

import (
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/facets/cur-web/internal/db"
	"github.com/facets/cur-web/internal/notify"
	"github.com/google/uuid"
)

const (
	maxAttempts   = 3
	pollInterval  = 5 * time.Second
	finalizeWait  = 2 * time.Second
	staleTimeout  = 5 * time.Minute
)

// Watcher polls a job's claude PID until it dies, then finalizes (mark done,
// retry up to maxAttempts, or mark failed). One Watch goroutine per job —
// either kicked off by the handler when a new job is created, or attached at
// startup to orphans surviving a backend restart.
type Watcher struct {
	db      *db.DB
	spawner *Spawner
	jobsDir string
	notify  NotifyConfig
}

func NewWatcher(database *db.DB, spawner *Spawner, jobsDir string, nc NotifyConfig) *Watcher {
	return &Watcher{db: database, spawner: spawner, jobsDir: jobsDir, notify: nc}
}

// Watch blocks until the job reaches a terminal state. Used for fresh runs
// that may clean-slate retry up to maxAttempts on failure.
func (w *Watcher) Watch(jobID string, pid int) {
	w.watchUntilDead(jobID, pid)
	w.finalize(jobID, true)
}

// WatchOnce blocks until the process dies, then finalizes WITHOUT retry.
// Used for refinement runs — failure must not wipe the original successful
// projection state.
func (w *Watcher) WatchOnce(jobID string, pid int) {
	w.watchUntilDead(jobID, pid)
	w.finalize(jobID, false)
}

// watchUntilDead is the shared polling loop. Two liveness signals each tick:
//   - Process liveness via pidAlive (rejects zombies + missing pids).
//   - Transcript freshness: claude writes to its jsonl on every turn. If the
//     mtime is older than staleTimeout the process is considered hung and we
//     SIGKILL the group so the next tick sees the process gone.
func (w *Watcher) watchUntilDead(jobID string, pid int) {
	tPath := w.transcriptPathFor(jobID)
	// Per-watch activity watermark. Initialized to spawn time (now) so a
	// pre-existing jsonl from a prior run (e.g. when /refine reuses the
	// same session_id) doesn't false-trip stale detection in the first
	// few seconds. Bumped each tick to the latest mtime across parent +
	// sub-agent transcripts.
	lastActivity := time.Now()
	for pidAlive(pid) {
		if m := w.latestTranscriptMtime(tPath); m.After(lastActivity) {
			lastActivity = m
		}
		if time.Since(lastActivity) > staleTimeout {
			log.Printf("watcher %s: no transcript activity for >%s, killing pid %d", jobID, staleTimeout, pid)
			killGroup(pid)
		}
		time.Sleep(pollInterval)
	}
}

// transcriptPathFor resolves the on-disk jsonl path for the job's *current*
// session_id (which may change between attempts). Returns "" if the job is
// gone or has no session yet.
func (w *Watcher) transcriptPathFor(jobID string) string {
	job, err := w.db.GetJob(jobID)
	if err != nil || job.SessionID == "" {
		return ""
	}
	jobDir := filepath.Join(w.jobsDir, jobID)
	p, err := transcriptPath(jobDir, job.SessionID)
	if err != nil {
		return ""
	}
	return p
}

// latestTranscriptMtime returns the most recent mtime across the parent
// jsonl and any sub-agent jsonls. Returns zero-time if the parent doesn't
// exist yet (claude hasn't started writing) — the caller's watermark
// stays at its initial spawn value in that case.
//
// We consider sub-agent jsonls because during phases like Phase 2 (5
// parallel sub-agents) the parent jsonl is idle for many minutes while
// children work — only looking at the parent would false-fire on a
// healthy job.
func (w *Watcher) latestTranscriptMtime(path string) time.Time {
	var zero time.Time
	if path == "" {
		return zero
	}
	parentInfo, err := os.Stat(path)
	if err != nil {
		return zero
	}
	latest := parentInfo.ModTime()

	// Walk sub-agent jsonls under <parent>/<session>/subagents/agent-*.jsonl.
	// Path of the parent jsonl is .../<session>.jsonl ; sub-agents live next
	// to it under .../<session>/subagents/.
	subDir := strings.TrimSuffix(path, ".jsonl") + "/subagents"
	if entries, err := os.ReadDir(subDir); err == nil {
		for _, e := range entries {
			if !strings.HasSuffix(e.Name(), ".jsonl") {
				continue
			}
			info, err := e.Info()
			if err != nil {
				continue
			}
			if info.ModTime().After(latest) {
				latest = info.ModTime()
			}
		}
	}
	return latest
}

// killGroup sends SIGKILL to the process group rooted at pid (claude was
// spawned with Setsid so pid is also the pgid). Best-effort; errors are
// logged but not fatal — the next pidAlive tick will sort it out.
func killGroup(pid int) {
	if err := syscall.Kill(-pid, syscall.SIGKILL); err != nil {
		// Fall back to direct PID kill if the negative-pid form fails.
		_ = syscall.Kill(pid, syscall.SIGKILL)
	}
}

func (w *Watcher) finalize(jobID string, allowRetry bool) {
	// Brief grace period in case claude was just finishing the report write.
	time.Sleep(finalizeWait)

	job, err := w.db.GetJob(jobID)
	if err != nil {
		log.Printf("watcher %s: GetJob: %v", jobID, err)
		return
	}
	jobDir := filepath.Join(w.jobsDir, jobID)

	// Explicit structural-failure path: if claude wrote failure.txt, it has
	// diagnosed the input as unprocessable (wrong cloud, corrupted file,
	// unknown shape). Surface that reason cleanly and skip the retry loop —
	// retrying the same broken input would just burn three more attempts on
	// the same error.
	if reason := readFailureReason(jobDir); reason != "" {
		if err := w.db.UpdateJobFailed(jobID, reason); err != nil {
			log.Printf("watcher %s: UpdateJobFailed (failure.txt): %v", jobID, err)
		}
		go notify.SendJobFailed(w.notify.Email, job.Owner, job.Owner, job.Prospect)
		go notify.PostJobFailed(w.notify.SlackURL, job.Prospect, job.Owner, jobID)
		return
	}

	// Success signal: resolveReportPath finds either a versioned
	// report-<run_id>.html (via run_results) or the legacy unsuffixed
	// projection-audit/report.html. The old check (just stat report.html)
	// missed new-skill outputs which only write versioned files, causing
	// the watcher to retry and eventually fail a successful job.
	reportPath, _ := resolveReportPath(jobDir, "")
	if reportPath != "" {
		// Source aws_spend from the duckdb (canonical) and fall back to
		// the HTML regex one release as a safety net for jobs whose
		// duckdb shape diverges from the current skill version.
		dbPath := filepath.Join(jobDir, "projection-audit", "projection.duckdb")
		spend, err := QueryAWSSpend(dbPath)
		if err != nil {
			log.Printf("watcher %s: QueryAWSSpend: %v (will try html fallback)", jobID, err)
		}
		if spend == 0 {
			// Safety net for one release: parse the legacy
			// <div id="aws-total-spend">...</div> element from the
			// HTML report. Drop this once every job has a populated
			// projection.duckdb / aws_li_catalog table.
			if content, err := os.ReadFile(reportPath); err == nil {
				spend = extractAWSSpendFromHTML(string(content))
			}
		}
		if err := w.db.UpdateJobDone(jobID, spend); err != nil {
			log.Printf("watcher %s: UpdateJobDone: %v", jobID, err)
		}
		go notify.SendJobReady(w.notify.Email, job.Owner, job.Owner, job.Prospect, jobID)
		go notify.PostJobSuccess(w.notify.SlackURL, job.Prospect, job.Owner, jobID, spend)
		return
	}

	if !allowRetry || job.Attempts >= maxAttempts {
		tail := tailLog(filepath.Join(jobDir, "claude.log"), 500)
		var errMsg string
		if !allowRetry {
			errMsg = "refinement run exited without updating report.html"
		} else {
			errMsg = "claude exited without report.html after " + strconv.Itoa(job.Attempts) + " attempts"
		}
		if tail != "" {
			errMsg += "; tail: " + tail
		}
		if err := w.db.UpdateJobFailed(jobID, errMsg); err != nil {
			log.Printf("watcher %s: UpdateJobFailed: %v", jobID, err)
		}
		go notify.SendJobFailed(w.notify.Email, job.Owner, job.Owner, job.Prospect)
		go notify.PostJobFailed(w.notify.SlackURL, job.Prospect, job.Owner, jobID)
		return
	}

	// Retry: clean slate the job dir except input.*, new session_id, increment attempts, respawn.
	log.Printf("watcher %s: clean-slate retry (attempt %d/%d)", jobID, job.Attempts+1, maxAttempts)
	if err := wipeJobDir(jobDir); err != nil {
		log.Printf("watcher %s: wipe: %v", jobID, err)
	}
	newSession := uuid.New().String()
	w.db.UpdateJobSessionID(jobID, newSession)
	w.db.IncrementJobAttempts(jobID)
	w.db.UpdateJobRunning(jobID)

	pid, err := w.spawner.Start(jobDir, job.InputExt, newSession)
	if err != nil {
		w.db.UpdateJobFailed(jobID, "retry spawn failed: "+err.Error())
		return
	}
	w.db.UpdateJobPID(jobID, pid)
	w.Watch(jobID, pid)
}

// wipeJobDir removes everything under jobDir except files starting with "input.".
func wipeJobDir(jobDir string) error {
	entries, err := os.ReadDir(jobDir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "input.") {
			continue
		}
		if err := os.RemoveAll(filepath.Join(jobDir, e.Name())); err != nil {
			return err
		}
	}
	return nil
}

// readFailureReason returns the trimmed contents of <jobDir>/failure.txt, or
// "" if the file is absent/empty. claude writes this when it detects the
// input is structurally unprocessable (wrong cloud, corrupted, etc.) so the
// watcher can mark the job failed with a useful message instead of burning
// three retry attempts on the same broken input.
func readFailureReason(jobDir string) string {
	data, err := os.ReadFile(filepath.Join(jobDir, "failure.txt"))
	if err != nil {
		return ""
	}
	s := strings.TrimSpace(string(data))
	if len(s) > 1000 {
		s = s[:1000] + "…"
	}
	return s
}

// awsSpendRe matches the legacy total-spend element rendered by older
// versions of the projection report. Kept inline here (not exported)
// purely as a one-release safety net while QueryAWSSpend rolls out.
var awsSpendRe = regexp.MustCompile(`id="aws-total-spend"[^>]*>([0-9]+(?:\.[0-9]+)?)<`)

func extractAWSSpendFromHTML(html string) float64 {
	m := awsSpendRe.FindStringSubmatch(html)
	if len(m) < 2 {
		return 0
	}
	v, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return 0
	}
	return v
}

// pidAlive reports whether pid is a *running* process (not zombie/defunct).
// kill(pid, 0) returns nil for both running and zombie processes, so we also
// query `ps -o stat` and treat a state starting with 'Z' as dead.
func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	proc, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	if err := proc.Signal(syscall.Signal(0)); err != nil {
		return false
	}
	out, err := exec.Command("ps", "-o", "stat=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		// Process disappeared between checks — treat as dead.
		return false
	}
	state := strings.TrimSpace(string(out))
	if state == "" || strings.HasPrefix(state, "Z") {
		return false
	}
	return true
}

