package envmgr

import (
	"os"
	"sync"
)

// State model
//
//	"server value"   = what the process environment held at startup (the host
//	                   environment plus the .env file). Captured once by
//	                   CaptureBaseline before anything else mutates the env.
//	"app override"   = a value an admin saved in Settings & Secrets. Stored
//	                   (encrypted if secret) in the runtime_env DB table and
//	                   applied with os.Setenv at startup and on save.
//	"effective"      = what os.Getenv returns now: the override if present,
//	                   else the server value.
//
// Removing an override restores the server value, so an admin can always get
// back to the deployment's configuration.

var (
	mu         sync.Mutex
	baseline   = map[string]baselineEntry{}
	captured   bool
	overrides  = map[string]bool{}
	loadErrors = map[string]string{}
	applyHooks = map[string]func(){}
)

type baselineEntry struct {
	value string
	set   bool
}

// CaptureBaseline records the startup value of every registry variable (and
// alias). Call it once in main, right after loading .env and before applying
// stored overrides. Calling it again is a no-op.
func CaptureBaseline() {
	mu.Lock()
	defer mu.Unlock()
	if captured {
		return
	}
	for _, v := range registry {
		for _, k := range append([]string{v.Key}, v.Aliases...) {
			val, ok := os.LookupEnv(k)
			baseline[k] = baselineEntry{value: val, set: ok && val != ""}
		}
	}
	captured = true
}

// ResetForTest clears all state. Tests only.
func ResetForTest() {
	mu.Lock()
	defer mu.Unlock()
	baseline = map[string]baselineEntry{}
	captured = false
	overrides = map[string]bool{}
	loadErrors = map[string]string{}
}

// ServerValue returns the value the variable had at startup (before any app
// override). If the primary key was unset, the first set alias is returned
// along with its name.
func ServerValue(v Var) (value, fromKey string, ok bool) {
	mu.Lock()
	defer mu.Unlock()
	if e, found := baseline[v.Key]; found && e.set {
		return e.value, v.Key, true
	}
	for _, a := range v.Aliases {
		if e, found := baseline[a]; found && e.set {
			return e.value, a, true
		}
	}
	if !captured {
		// Not captured (e.g. in tests): fall back to the live environment.
		if val := os.Getenv(v.Key); val != "" && !overrides[v.Key] {
			return val, v.Key, true
		}
	}
	return "", "", false
}

// Effective returns the value the app is using right now.
func Effective(v Var) (value, fromKey string, ok bool) {
	if val := os.Getenv(v.Key); val != "" {
		return val, v.Key, true
	}
	for _, a := range v.Aliases {
		if val := os.Getenv(a); val != "" {
			return val, a, true
		}
	}
	return "", "", false
}

// HasOverride reports whether an app override is active for key.
func HasOverride(key string) bool {
	mu.Lock()
	defer mu.Unlock()
	return overrides[key]
}

// OverrideCount returns how many app overrides are active.
func OverrideCount() int {
	mu.Lock()
	defer mu.Unlock()
	n := 0
	for _, on := range overrides {
		if on {
			n++
		}
	}
	return n
}

// ApplyOverride makes value the effective value of a managed variable,
// immediately, and runs its apply hook (if any).
func ApplyOverride(key, value string) error {
	v, ok := Lookup(key)
	if !ok || v.Mode != ModeManaged {
		return ErrNotManaged
	}
	if err := os.Setenv(key, value); err != nil {
		return err
	}
	mu.Lock()
	overrides[key] = true
	delete(loadErrors, key)
	hook := applyHooks[key]
	mu.Unlock()
	if hook != nil {
		hook()
	}
	return nil
}

// ClearOverride removes the app override and restores the startup value.
func ClearOverride(key string) {
	mu.Lock()
	e, found := baseline[key]
	delete(overrides, key)
	delete(loadErrors, key)
	hook := applyHooks[key]
	mu.Unlock()
	if found && e.set {
		_ = os.Setenv(key, e.value)
	} else {
		_ = os.Unsetenv(key)
	}
	if hook != nil {
		hook()
	}
}

// RecordLoadError notes that a stored override could not be applied at
// startup (typically: it can't be decrypted because the encryption key
// changed). The UI turns this into "re-enter this value".
func RecordLoadError(key, msg string) {
	mu.Lock()
	defer mu.Unlock()
	loadErrors[key] = msg
}

// LoadError returns the startup load error for key, if any.
func LoadError(key string) string {
	mu.Lock()
	defer mu.Unlock()
	return loadErrors[key]
}

// RegisterApplyHook runs fn whenever key's effective value changes through
// ApplyOverride / ClearOverride.
func RegisterApplyHook(key string, fn func()) {
	mu.Lock()
	defer mu.Unlock()
	applyHooks[key] = fn
}

// IgnoredPresent returns the ignored variable names that are actually set.
func IgnoredPresent() []IgnoredVar {
	var out []IgnoredVar
	for _, iv := range ignored {
		if os.Getenv(iv.Key) != "" {
			out = append(out, iv)
		}
	}
	return out
}
