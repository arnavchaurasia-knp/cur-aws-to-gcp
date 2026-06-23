package jobs

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// Progress is a snapshot of an in-flight gemini session, derived from the
// progress.json file the skill writes to the job directory at each phase
// transition.
type Progress struct {
	Events       int    `json:"events"`
	Phase        string `json:"phase"`         // last-known phase name (debug)
	PhaseNumber  int    `json:"phase_number"`  // 1-6, written by the skill
	LastActivity string `json:"last_activity"`
	TranscriptOK bool   `json:"transcript_ok"` // true when progress.json exists
}

const totalPhases = 6

// progressFile is the file the skill writes into the job working directory.
const progressFile = "progress.json"

// skillProgress mirrors the JSON the skill writes at each phase transition.
type skillProgress struct {
	Phase        int    `json:"phase"`
	PhaseName    string `json:"phase_name"`
	LastActivity string `json:"last_activity"`
}

// ReadProgress reads progress.json from the job directory. The second argument
// (sessionID) is kept for API compatibility with callers but is unused —
// Gemini CLI manages session state internally.
// Returns Progress{TranscriptOK: false} if the file doesn't exist yet.
func ReadProgress(jobDir, _ string) (*Progress, error) {
	data, err := os.ReadFile(filepath.Join(jobDir, progressFile))
	if err != nil {
		if os.IsNotExist(err) {
			return &Progress{}, nil
		}
		return &Progress{}, err
	}

	var sp skillProgress
	if err := json.Unmarshal(data, &sp); err != nil {
		// File exists but not yet valid JSON (partial write) — report alive
		// but no phase info yet.
		return &Progress{TranscriptOK: true}, nil
	}

	phase := sp.Phase
	if phase < 1 {
		phase = 1
	}
	if phase > totalPhases {
		phase = totalPhases
	}

	return &Progress{
		TranscriptOK: true,
		Phase:        sp.PhaseName,
		PhaseNumber:  phase,
		LastActivity: sp.LastActivity,
	}, nil
}
