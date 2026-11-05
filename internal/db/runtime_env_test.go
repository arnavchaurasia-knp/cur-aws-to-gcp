package db

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRuntimeEnvSecretIsEncryptedAndRoundTrips(t *testing.T) {
	t.Setenv("SETTINGS_ENCRYPTION_KEY", base64.StdEncoding.EncodeToString(make([]byte, 32)))
	d, err := Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()

	secret := "sk-test-0123456789abcdef"
	if err := d.SetRuntimeEnv("ANTIGRAVITY_API_KEY", secret, true, "admin@facets.cloud"); err != nil {
		t.Fatal(err)
	}
	if err := d.SetRuntimeEnv("AGY_PATH", "/opt/agy", false, "admin@facets.cloud"); err != nil {
		t.Fatal(err)
	}

	// The plaintext must not be stored in the value column.
	var plain *string
	if err := d.conn.QueryRow(`SELECT value FROM runtime_env WHERE key='ANTIGRAVITY_API_KEY'`).Scan(&plain); err != nil {
		t.Fatal(err)
	}
	if plain != nil {
		t.Fatalf("secret stored in plaintext column: %q", *plain)
	}

	vals, err := d.LoadRuntimeEnv()
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, v := range vals {
		if v.Err != nil {
			t.Fatalf("%s: %v", v.Key, v.Err)
		}
		got[v.Key] = v.Value
	}
	if got["ANTIGRAVITY_API_KEY"] != secret || got["AGY_PATH"] != "/opt/agy" {
		t.Fatalf("round trip mismatch: %v", got)
	}

	meta, err := d.ListRuntimeEnv()
	if err != nil {
		t.Fatal(err)
	}
	if !meta["ANTIGRAVITY_API_KEY"].IsSecret || meta["AGY_PATH"].IsSecret {
		t.Fatalf("secret flags wrong: %+v", meta)
	}

	if err := d.DeleteRuntimeEnv("AGY_PATH"); err != nil {
		t.Fatal(err)
	}
	meta, _ = d.ListRuntimeEnv()
	if _, ok := meta["AGY_PATH"]; ok {
		t.Fatal("delete did not remove the override")
	}
}

func TestRuntimeEnvSecretRefusedWithoutKey(t *testing.T) {
	t.Setenv("SETTINGS_ENCRYPTION_KEY", "")
	d, err := Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if err := d.SetRuntimeEnv("ANTIGRAVITY_API_KEY", "sk-test-0123456789", true, "a"); err != ErrNoEncryptionKey {
		t.Fatalf("want ErrNoEncryptionKey, got %v", err)
	}
}

func TestEnsureEncryptionKeyGeneratesThenReuses(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SETTINGS_ENCRYPTION_KEY", "")

	src, err := EnsureEncryptionKey(dir)
	if err != nil || src != KeySourceGenerated {
		t.Fatalf("first start: src=%s err=%v", src, err)
	}
	first := os.Getenv("SETTINGS_ENCRYPTION_KEY")
	if !EncryptionAvailable() {
		t.Fatal("key should be usable after generation")
	}
	b, _ := os.ReadFile(filepath.Join(dir, SettingsKeyFileName))
	if strings.TrimSpace(string(b)) != first {
		t.Fatal("key file content should match exported key")
	}

	// Simulate a restart: env cleared, file present.
	t.Setenv("SETTINGS_ENCRYPTION_KEY", "")
	src, err = EnsureEncryptionKey(dir)
	if err != nil || src != KeySourceFile {
		t.Fatalf("restart: src=%s err=%v", src, err)
	}
	if os.Getenv("SETTINGS_ENCRYPTION_KEY") != first {
		t.Fatal("restart must reuse the same key, or saved secrets become unreadable")
	}
}

func TestEnsureEncryptionKeyPrefersEnvAndRejectsBadEnv(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SETTINGS_ENCRYPTION_KEY", base64.StdEncoding.EncodeToString(make([]byte, 32)))
	if src, err := EnsureEncryptionKey(dir); err != nil || src != KeySourceEnv {
		t.Fatalf("src=%s err=%v", src, err)
	}
	if _, err := os.Stat(filepath.Join(dir, SettingsKeyFileName)); !os.IsNotExist(err) {
		t.Fatal("no key file should be written when the env var is set")
	}

	t.Setenv("SETTINGS_ENCRYPTION_KEY", "not-a-valid-key")
	if src, err := EnsureEncryptionKey(dir); err == nil || src != KeySourceNone {
		t.Fatalf("malformed env key must not be silently replaced: src=%s err=%v", src, err)
	}
}
