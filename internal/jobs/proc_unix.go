//go:build !windows

package jobs

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

var sysProcAttr = &syscall.SysProcAttr{Setsid: true}

func killGroup(pid int) {
	if err := syscall.Kill(-pid, syscall.SIGKILL); err != nil {
		_ = syscall.Kill(pid, syscall.SIGKILL)
	}
}

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
	// On Linux, read /proc/<pid>/status directly without requiring ps binary
	if statusBytes, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/status"); err == nil {
		for _, line := range strings.Split(string(statusBytes), "\n") {
			if strings.HasPrefix(line, "State:") {
				fields := strings.Fields(line)
				if len(fields) >= 2 {
					// 'Z' (zombie) and 'X' (dead) are terminated
					return fields[1] != "Z" && fields[1] != "X"
				}
			}
		}
		return true
	}
	// Fallback to ps for macOS / BSD
	out, err := exec.Command("ps", "-o", "stat=", "-p", strconv.Itoa(pid)).Output()
	if err != nil {
		// If ps is not found or fails, proc.Signal(0) succeeding indicates the process exists
		return true
	}
	state := strings.TrimSpace(string(out))
	if state == "" || strings.HasPrefix(state, "Z") {
		return false
	}
	return true
}

func reapOrphanAgents(jobsDir string, activeJobIDs map[string]bool) []int {
	cleanJobsDir := filepath.Clean(jobsDir)
	var reaped []int
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return reaped
	}
	myPid := os.Getpid()
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 || pid == myPid {
			continue
		}
		cmdBytes, err := os.ReadFile(filepath.Join("/proc", entry.Name(), "cmdline"))
		if err != nil {
			continue
		}
		// In /proc/<pid>/cmdline arguments are separated by NUL bytes
		cmdLine := strings.ReplaceAll(string(cmdBytes), "\x00", " ")
		isAgent := strings.Contains(cmdLine, "dangerously-skip-permissions") || strings.Contains(cmdLine, "run_all.py")
		if !isAgent {
			continue
		}
		if !strings.Contains(cmdLine, cleanJobsDir) {
			continue
		}
		isActive := false
		for activeID := range activeJobIDs {
			if activeID != "" && strings.Contains(cmdLine, activeID) {
				isActive = true
				break
			}
		}
		if !isActive {
			killGroup(pid)
			reaped = append(reaped, pid)
		}
	}
	return reaped
}

