package config

import "testing"

// TestParseAllowedDomainsUnsetIsRestrictive pins the production defect: an
// unset ALLOWED_DOMAINS returned []string{"*"}, and auth skips the domain
// check entirely when "*" is present (internal/auth/oauth.go:439). So the
// variable nobody ever set silently allowed any Google account to sign in,
// while the ALLOW_ANY_DOMAIN opt-in never fired because it only inspected the
// raw env string, which was empty rather than "*".
func TestParseAllowedDomainsUnsetIsRestrictive(t *testing.T) {
	for _, raw := range []string{"", "   "} {
		got := parseAllowedDomains(raw)
		for _, d := range got {
			if d == "*" {
				t.Fatalf("parseAllowedDomains(%q) = %v — unset must never mean wildcard", raw, got)
			}
		}
		if len(got) == 0 {
			t.Fatalf("parseAllowedDomains(%q) returned an empty list", raw)
		}
		if got[0] != DefaultAllowedDomains[0] {
			t.Errorf("parseAllowedDomains(%q) = %v, want the default allow-list %v",
				raw, got, DefaultAllowedDomains)
		}
	}
}

func TestParseAllowedDomainsExplicitValues(t *testing.T) {
	got := parseAllowedDomains(" Facets.Cloud , example.com ")
	want := []string{"facets.cloud", "example.com"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("index %d: got %q, want %q", i, got[i], want[i])
		}
	}

	// An explicit wildcard must still be representable — it is what
	// ALLOW_ANY_DOMAIN gates. Only the *unset* case changed.
	if w := parseAllowedDomains("*"); len(w) != 1 || w[0] != "*" {
		t.Errorf(`parseAllowedDomains("*") = %v, want ["*"]`, w)
	}
}
