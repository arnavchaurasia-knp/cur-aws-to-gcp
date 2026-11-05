package envmgr

import (
	"os"
	"strings"
	"testing"
)

func TestRegistryIsWellFormed(t *testing.T) {
	seen := map[string]bool{}
	cats := map[string]bool{}
	for _, c := range Categories() {
		cats[c] = true
	}
	for _, v := range All() {
		if v.Key == "" || v.Title == "" || v.Description == "" {
			t.Errorf("%q: key, title and description are required", v.Key)
		}
		if seen[v.Key] {
			t.Errorf("%q registered twice", v.Key)
		}
		seen[v.Key] = true
		if !cats[v.Category] {
			t.Errorf("%q: unknown category %q", v.Key, v.Category)
		}
		if v.Mode == ModeIntegration && v.ManageURL == "" {
			t.Errorf("%q: integration variables need a ManageURL", v.Key)
		}
	}
}

// Sign-in, session and storage settings must never become editable from a web
// form: a bad value there can lock every user out or redirect data.
func TestSecurityCriticalVarsAreNotManaged(t *testing.T) {
	for _, k := range []string{"SESSION_SECRET", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS", "ALLOWED_DOMAINS",
		"DEV_AUTH_BYPASS", "ADMIN_PASSWORD", "DATABASE_URL", "DATA_DIR", "SETTINGS_ENCRYPTION_KEY"} {
		v, ok := Lookup(k)
		if !ok {
			t.Fatalf("%s missing from registry", k)
		}
		if v.Mode == ModeManaged {
			t.Errorf("%s must not be ModeManaged", k)
		}
	}
}

func TestValidate(t *testing.T) {
	key, _ := Lookup("ANTIGRAVITY_API_KEY")
	level, _ := Lookup("LOG_LEVEL")
	session, _ := Lookup("SESSION_SECRET")

	cases := []struct {
		name    string
		v       Var
		value   string
		wantErr string
	}{
		{"ok secret", key, "abcd1234efgh", ""},
		{"empty", key, "", "empty"},
		{"too short secret", key, "abc", "too short"},
		{"newline", key, "abcd1234\nefgh", "single line"},
		{"padded", key, " abcd1234efgh", "spaces"},
		{"option ok", level, "debug", ""},
		{"option bad", level, "verbose", "one of"},
		{"not managed", session, "whatever-long-enough", "can't be changed"},
	}
	for _, c := range cases {
		err := Validate(c.v, c.value)
		if c.wantErr == "" && err != nil {
			t.Errorf("%s: unexpected error %v", c.name, err)
		}
		if c.wantErr != "" && (err == nil || !strings.Contains(err.Error(), c.wantErr)) {
			t.Errorf("%s: want error containing %q, got %v", c.name, c.wantErr, err)
		}
	}
}

func TestPreviewNeverRevealsSecrets(t *testing.T) {
	key, _ := Lookup("ANTIGRAVITY_API_KEY")
	session, _ := Lookup("SESSION_SECRET")
	secret := "sk-live-ABCDEFGHIJKL9876"
	if p := Preview(key, secret); p != "••••9876" {
		t.Errorf("managed secret preview = %q, want last 4 only", p)
	}
	if p := Preview(session, secret); strings.Contains(p, "9876") {
		t.Errorf("deployment secret preview leaked characters: %q", p)
	}
	port, _ := Lookup("PORT")
	if p := Preview(port, "8080"); p != "8080" {
		t.Errorf("non-secret preview = %q", p)
	}
}

func TestOverrideLifecycleRestoresServerValue(t *testing.T) {
	ResetForTest()
	t.Setenv("AGY_PATH", "/usr/local/bin/agy")
	CaptureBaseline()

	hookRuns := 0
	RegisterApplyHook("AGY_PATH", func() { hookRuns++ })
	defer RegisterApplyHook("AGY_PATH", nil)

	if err := ApplyOverride("AGY_PATH", "/opt/agy/agy"); err != nil {
		t.Fatal(err)
	}
	if got := os.Getenv("AGY_PATH"); got != "/opt/agy/agy" {
		t.Fatalf("override not applied, got %q", got)
	}
	if !HasOverride("AGY_PATH") {
		t.Fatal("HasOverride should be true")
	}
	v, _ := Lookup("AGY_PATH")
	if sv, _, _ := ServerValue(v); sv != "/usr/local/bin/agy" {
		t.Fatalf("server value should stay the startup value, got %q", sv)
	}

	ClearOverride("AGY_PATH")
	if got := os.Getenv("AGY_PATH"); got != "/usr/local/bin/agy" {
		t.Fatalf("clear should restore startup value, got %q", got)
	}
	if hookRuns != 2 {
		t.Fatalf("hook should run on apply and clear, ran %d times", hookRuns)
	}
}

func TestClearRestoresUnsetWhenServerHadNoValue(t *testing.T) {
	ResetForTest()
	os.Unsetenv("GOOGLE_CLOUD_AVAILABILITY_PROJECT")
	CaptureBaseline()
	if err := ApplyOverride("GOOGLE_CLOUD_AVAILABILITY_PROJECT", "my-project"); err != nil {
		t.Fatal(err)
	}
	ClearOverride("GOOGLE_CLOUD_AVAILABILITY_PROJECT")
	if _, ok := os.LookupEnv("GOOGLE_CLOUD_AVAILABILITY_PROJECT"); ok {
		t.Fatal("variable should be unset again after clearing")
	}
}

func TestApplyOverrideRejectsNonManaged(t *testing.T) {
	if err := ApplyOverride("SESSION_SECRET", "x"); err != ErrNotManaged {
		t.Fatalf("want ErrNotManaged, got %v", err)
	}
	if err := ApplyOverride("PATH", "/tmp"); err != ErrNotManaged {
		t.Fatalf("unregistered vars must be rejected, got %v", err)
	}
}

func TestIgnoredPresent(t *testing.T) {
	t.Setenv("AGY_API_KEY", "something")
	found := false
	for _, iv := range IgnoredPresent() {
		if iv.Key == "AGY_API_KEY" && iv.UseInstead == "ANTIGRAVITY_API_KEY" {
			found = true
		}
	}
	if !found {
		t.Fatal("AGY_API_KEY should be reported as ignored")
	}
}
