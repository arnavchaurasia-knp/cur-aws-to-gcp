//go:build !windows

package jobs

import (
	"os"
	"os/exec"
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

