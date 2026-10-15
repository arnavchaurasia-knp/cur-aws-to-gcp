//go:build windows

package jobs

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
)

var sysProcAttr = &syscall.SysProcAttr{}

func killGroup(pid int) {
	if pid <= 0 {
		return
	}
	// Use taskkill with /T (descendants) and /F (force) to kill full process tree.
	cmd := exec.Command("taskkill", "/PID", strconv.Itoa(pid), "/T", "/F")
	_ = cmd.Run()

	// Direct process kill as fallback in case taskkill fails or was not found
	p, err := os.FindProcess(pid)
	if err == nil {
		_ = p.Kill()
	}
}

var (
	modKernel32          = syscall.NewLazyDLL("kernel32.dll")
	procOpenProcess      = modKernel32.NewProc("OpenProcess")
	procGetExitCodeProc  = modKernel32.NewProc("GetExitCodeProcess")
	procCloseHandle      = modKernel32.NewProc("CloseHandle")
)

const (
	processQueryLimitedInformation = 0x1000
	stillActive                    = 259
)

// pidAlive returns true only when the process with the given PID is actually
// running. On Windows, os.Process.Signal(0) always errors (only os.Interrupt
// is supported), so we use GetExitCodeProcess: it returns STILL_ACTIVE (259)
// for a live process and the real exit code once it has exited.
func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	handle, _, _ := procOpenProcess.Call(
		uintptr(processQueryLimitedInformation),
		0,
		uintptr(pid),
	)
	if handle == 0 {
		return false
	}
	defer procCloseHandle.Call(handle)
	var exitCode uint32
	ret, _, _ := procGetExitCodeProc.Call(handle, uintptr(unsafe.Pointer(&exitCode)))
	return ret != 0 && exitCode == stillActive
}

// reapOrphanAgents scans running processes matching the agent signature
// (Antigravity/agy --dangerously-skip-permissions or python ... run_all.py)
// whose job directory belongs to this jobsDir and whose job id is either not
// in activeJobIDs or is terminal/unknown.
func reapOrphanAgents(jobsDir string, activeJobIDs map[string]bool) []int {
	cleanJobsDir := filepath.Clean(jobsDir)
	var reaped []int

	// Use powershell to query Win32_Process for processes with command line matching agent signatures
	psScript := `Get-CimInstance Win32_Process | Where-Object { 
		($_.CommandLine -match "dangerously-skip-permissions" -or $_.CommandLine -match "run_all\.py")
	} | Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress`

	out, err := exec.Command("powershell", "-NoProfile", "-Command", psScript).Output()
	if err != nil {
		return reaped
	}

	trimmed := strings.TrimSpace(string(out))
	if trimmed == "" || trimmed == "null" {
		return reaped
	}

	type procItem struct {
		ProcessId   int    `json:"ProcessId"`
		CommandLine string `json:"CommandLine"`
	}

	var items []procItem
	if strings.HasPrefix(trimmed, "[") {
		_ = json.Unmarshal([]byte(trimmed), &items)
	} else if strings.HasPrefix(trimmed, "{") {
		var item procItem
		if err := json.Unmarshal([]byte(trimmed), &item); err == nil {
			items = append(items, item)
		}
	}

	myPid := os.Getpid()
	for _, it := range items {
		if it.ProcessId <= 0 || it.ProcessId == myPid {
			continue
		}
		// Process must belong to this product's jobsDir
		cmdLower := strings.ToLower(it.CommandLine)
		jobsDirLower := strings.ToLower(cleanJobsDir)
		if !strings.Contains(cmdLower, jobsDirLower) {
			continue
		}

		// Check if it belongs to any active non-terminal job
		isActive := false
		for activeID := range activeJobIDs {
			if activeID != "" && strings.Contains(cmdLower, strings.ToLower(activeID)) {
				isActive = true
				break
			}
		}

		if !isActive {
			killGroup(it.ProcessId)
			reaped = append(reaped, it.ProcessId)
		}
	}
	return reaped
}
