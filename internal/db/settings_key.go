package db

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Where the encryption key for saved secrets came from.
const (
	KeySourceEnv       = "env"       // SETTINGS_ENCRYPTION_KEY set by the deployment
	KeySourceFile      = "file"      // loaded from <data dir>/settings-encryption.key
	KeySourceGenerated = "generated" // created on this start and written to that file
	KeySourceNone      = "none"      // could not provide one (see error)
)

// SettingsKeyFileName is the key file kept in the data directory when the
// deployment does not set SETTINGS_ENCRYPTION_KEY.
const SettingsKeyFileName = "settings-encryption.key"

var (
	keyInfoMu  sync.Mutex
	keySource  = KeySourceNone
	keyPath    string
	keyProblem string
)

// EncryptionKeyInfo reports where the key for saved secrets came from, the
// key file path (when a file is used) and any provisioning problem.
func EncryptionKeyInfo() (source, path, problem string) {
	keyInfoMu.Lock()
	defer keyInfoMu.Unlock()
	return keySource, keyPath, keyProblem
}


// EnsureEncryptionKey makes sure secrets saved in the app (Integrations,
// Settings & Secrets) can be encrypted.
//
//   - If SETTINGS_ENCRYPTION_KEY is set and valid, it is used as-is.
//   - Otherwise a key file in dataDir is used, created on first start
//     (32 random bytes, base64, file mode 0600), and exported into the process
//     environment so the existing encryption helpers pick it up.
//
// Why a file instead of refusing: without any key, nobody can save an API key
// in the app at all, and the first experience of the feature is an error. The
// file keeps secrets encrypted at rest (which matters most when DATABASE_URL
// points to a remote database). The trade-off — the key lives on the same
// disk — is surfaced in System Health so production can set the env var.
func EnsureEncryptionKey(dataDir string) (string, error) {
	keyInfoMu.Lock()
	defer keyInfoMu.Unlock()

	if raw := strings.TrimSpace(os.Getenv("SETTINGS_ENCRYPTION_KEY")); raw != "" {
		if validKey(raw) {
			keySource, keyPath, keyProblem = KeySourceEnv, "", ""
			return keySource, nil
		}
		// Present but malformed: do not silently replace a key the operator set.
		keySource, keyPath = KeySourceNone, ""
		keyProblem = "SETTINGS_ENCRYPTION_KEY is set but is not base64 of 32 bytes"
		return keySource, fmt.Errorf("%s", keyProblem)
	}

	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		keySource, keyProblem = KeySourceNone, "cannot create data directory: "+err.Error()
		return keySource, err
	}
	path := filepath.Join(dataDir, SettingsKeyFileName)

	if b, err := os.ReadFile(path); err == nil {
		raw := strings.TrimSpace(string(b))
		if !validKey(raw) {
			keySource, keyPath = KeySourceNone, path
			keyProblem = "key file exists but is not base64 of 32 bytes; refusing to overwrite it"
			return keySource, fmt.Errorf("%s", keyProblem)
		}
		_ = os.Setenv("SETTINGS_ENCRYPTION_KEY", raw)
		keySource, keyPath, keyProblem = KeySourceFile, path, ""
		return keySource, nil
	}

	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		keySource, keyProblem = KeySourceNone, "cannot generate key: "+err.Error()
		return keySource, err
	}
	raw := base64.StdEncoding.EncodeToString(buf)
	// O_EXCL: never clobber a key another process just wrote.
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		keySource, keyPath, keyProblem = KeySourceNone, path, "cannot write key file: "+err.Error()
		return keySource, err
	}
	if _, err := f.WriteString(raw + "\n"); err != nil {
		f.Close()
		keySource, keyPath, keyProblem = KeySourceNone, path, "cannot write key file: "+err.Error()
		return keySource, err
	}
	if err := f.Close(); err != nil {
		keySource, keyPath, keyProblem = KeySourceNone, path, "cannot write key file: "+err.Error()
		return keySource, err
	}
	_ = os.Setenv("SETTINGS_ENCRYPTION_KEY", raw)
	keySource, keyPath, keyProblem = KeySourceGenerated, path, ""
	return keySource, nil
}

func validKey(raw string) bool {
	k, err := base64.StdEncoding.DecodeString(raw)
	return err == nil && len(k) == 32
}
