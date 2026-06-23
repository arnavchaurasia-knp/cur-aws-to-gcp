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

// Watcher polls a job's gemini PID until it dies, then finalizes (mark done,
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
//   - Activity freshness: the newer of gemini.log (continuous stdout/stderr)
//     and progress.json (rewritten by the skill at each phase transition). A
//     long phase that goes quiet on stdout still advances progress.json, and a
//     burst of stdout with no phase change still bumps gemini.log — taking the
//     max of both avoids SIGKILLing a job that's genuinely still working. If
//     neither has advanced in staleTimeout the process is considered hung and
//     we SIGKILL the group so the next tick sees it gone.
func (w *Watcher) watchUntilDead(jobID string, pid int) {
	jobDir := filepath.Join(w.jobsDir, jobID)
	// Per-watch activity watermark. Initialized to spawn time (now) so a
	// pre-existing log from a prior run doesn't false-trip stale detection
	// in the first few seconds.
	lastActivity := time.Now()
	for pidAlive(pid) {
		if m := w.activityMtime(jobDir); m.After(lastActivity) {
			lastActivity = m
		}
		if time.Since(lastActivity) > staleTimeout {
			log.Printf("watcher %s: no log activity for >%s, killing pid %d", jobID, staleTimeout, pid)
			killGroup(pid)
		}
		time.Sleep(pollInterval)
	}
}

// activityMtime returns the most recent mtime among the job's liveness
// signals — gemini.log and progress.json. Returns zero-time if neither file
// exists yet (gemini hasn't produced output and the skill hasn't written its
// first phase marker).
func (w *Watcher) activityMtime(jobDir string) time.Time {
	var newest time.Time
	for _, name := range []string{"gemini.log", progressFile} {
		info, err := os.Stat(filepath.Join(jobDir, name))
		if err != nil {
			continue
		}
		if m := info.ModTime(); m.After(newest) {
			newest = m
		}
	}
	return newest
}

// killGroup sends SIGKILL to the process group rooted at pid (gemini was
// spawned with Setsid so pid is also the pgid). Best-effort; errors are
// logged but not fatal — the next pidAlive tick will sort it out.
func killGroup(pid int) {
	if err := syscall.Kill(-pid, syscall.SIGKILL); err != nil {
		// Fall back to direct PID kill if the negative-pid form fails.
		_ = syscall.Kill(pid, syscall.SIGKILL)
	}
}

func (w *Watcher) finalize(jobID string, allowRetry bool) {
	// Brief grace period in case gemini was just finishing the report write.
	time.Sleep(finalizeWait)

	job, err := w.db.GetJob(jobID)
	if err != nil {
		log.Printf("watcher %s: GetJob: %v", jobID, err)
		return
	}
	jobDir := filepath.Join(w.jobsDir, jobID)

	// Explicit structural-failure path: if gemini wrote failure.txt, it has
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
		tail := tailLog(filepath.Join(jobDir, "gemini.log"), 500)
		var errMsg string
		if !allowRetry {
			errMsg = "refinement run exited without updating report.html"
		} else {
			errMsg = "gemini exited without report.html after " + strconv.Itoa(job.Attempts) + " attempts"
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
// "" if the file is absent/empty. gemini writes this when it detects the
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

