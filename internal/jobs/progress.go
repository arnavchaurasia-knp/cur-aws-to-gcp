package jobs

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// Progress is a snapshot of an in-flight claude session, derived from the jsonl
// transcript file claude writes under ~/.claude/projects/<encoded-cwd>/<session>.jsonl.
type Progress struct {
	Events       int    `json:"events"`
	Phase        string `json:"phase"`         // raw last-known phase descriptor (debug)
	PhaseNumber  int    `json:"phase_number"`  // 1-6, derived from transcript signals
	LastActivity string `json:"last_activity"`
	TranscriptOK bool   `json:"transcript_ok"`
}

const totalPhases = 6

var phaseCompleteRe = regexp.MustCompile(`(?i)phase\s*(\d+)\s*complete`)
var phaseMentionRe = regexp.MustCompile(`(?i)phase\s*(\d+)`)

func transcriptPath(jobDir, sessionID string) (string, error) {
	if sessionID == "" {
		return "", errors.New("no session id")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	abs, err := filepath.Abs(jobDir)
	if err != nil {
		return "", err
	}

	// Resolve symlinks so the encoded path matches what the claude subprocess
// sees as its working directory. On macOS, /tmp is a symlink to /private/tmp;
// without this, the watcher checks -tmp-...- but claude writes to
// -private-tmp-...- and the transcript is never found.
if real, err := filepath.EvalSymlinks(abs); err == nil {
    abs = real
}

	encoded := strings.ReplaceAll(abs, "/", "-")
	return filepath.Join(home, ".claude", "projects", encoded, sessionID+".jsonl"), nil
}

// ReadProgress parses the jsonl transcript and returns a small summary.
// Returns Progress{TranscriptOK: false} if the file doesn't exist yet.
func ReadProgress(jobDir, sessionID string) (*Progress, error) {
	path, err := transcriptPath(jobDir, sessionID)
	if err != nil {
		return &Progress{}, err
	}
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return &Progress{}, nil
		}
		return &Progress{}, err
	}
	defer f.Close()

	p := &Progress{TranscriptOK: true}
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1<<20), 32<<20)

	var lastText, lastAgentDesc string
	maxDispatched := 0   // highest "Phase N" parsed from Agent dispatch descriptions
	maxCompleted := 0    // highest "Phase N complete" mention in main-agent text
	maxMentioned := 0    // highest "Phase N" mention anywhere in main-agent text

	for scanner.Scan() {
		p.Events++
		var ev struct {
			Type    string `json:"type"`
			Message struct {
				Content []struct {
					Type  string          `json:"type"`
					Text  string          `json:"text"`
					Name  string          `json:"name"`
					Input json.RawMessage `json:"input"`
				} `json:"content"`
			} `json:"message"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &ev); err != nil {
			continue
		}
		if ev.Type != "assistant" {
			continue
		}
		for _, c := range ev.Message.Content {
			switch c.Type {
			case "text":
				if t := strings.TrimSpace(c.Text); t != "" {
					lastText = t
					for _, m := range phaseCompleteRe.FindAllStringSubmatch(t, -1) {
						if n, err := strconv.Atoi(m[1]); err == nil && n > maxCompleted {
							maxCompleted = n
						}
					}
					for _, m := range phaseMentionRe.FindAllStringSubmatch(t, -1) {
						if n, err := strconv.Atoi(m[1]); err == nil && n > maxMentioned {
							maxMentioned = n
						}
					}
				}
			case "tool_use":
				if c.Name == "Agent" {
					var in struct {
						Description string `json:"description"`
					}
					if err := json.Unmarshal(c.Input, &in); err == nil && in.Description != "" {
						lastAgentDesc = in.Description
						// Some phases (e.g. Phase 2) fan out into multiple
						// parallel sub-agents. Track the *max phase number*
						// across all dispatches, not the count.
						for _, m := range phaseMentionRe.FindAllStringSubmatch(in.Description, -1) {
							if n, err := strconv.Atoi(m[1]); err == nil && n > maxDispatched {
								maxDispatched = n
							}
						}
					}
				}
			}
		}
	}

	// Current step = strongest signal we have:
	//  - phases the agent has explicitly said it completed → next is in flight
	//  - highest phase number parsed from a sub-agent dispatch description
	//    (handles fan-out where Phase 2 spawns 5 parallel mapping agents)
	//  - phase numbers mentioned in main-agent text (catches "moving to Phase 3")
	step := maxCompleted + 1
	if maxDispatched > step {
		step = maxDispatched
	}
	if maxMentioned > step {
		step = maxMentioned
	}
	if step < 1 {
		step = 1
	}
	if step > totalPhases {
		step = totalPhases
	}

	p.LastActivity = lastText
	p.Phase = lastAgentDesc
	p.PhaseNumber = step
	return p, nil
}
