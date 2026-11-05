package notify

import (
	"os"
	"strings"
	"testing"
)

func TestBuildJobReadyHTML(t *testing.T) {
	name := "Alice"
	prospect := "Acme Corp"
	jobID := "abc-123-xyz"
	reportURL := "http://localhost:5173/jobs/abc-123-xyz"
	spend := "$12,345.67"
	baseURL := "http://localhost:5173"

	html := buildJobReadyHTML(name, prospect, jobID, reportURL, spend, baseURL)

	if !strings.Contains(html, "FACETS") {
		t.Errorf("expected HTML to contain FACETS brand")
	}
	if !strings.Contains(html, ".CLOUD") {
		t.Errorf("expected HTML to contain .CLOUD brand")
	}
	if !strings.Contains(html, "Acme Corp") {
		t.Errorf("expected HTML to contain prospect name")
	}
	if !strings.Contains(html, "#abc-123-xyz") {
		t.Errorf("expected HTML to contain job ID")
	}
	if !strings.Contains(html, "$12,345.67") {
		t.Errorf("expected HTML to contain spend")
	}
	if !strings.Contains(html, "href=\"http://localhost:5173/jobs/abc-123-xyz\"") {
		t.Errorf("expected HTML to contain CTA button linking to %s", reportURL)
	}
	if !strings.Contains(html, "Facets TCO Analysis Tool") {
		t.Errorf("expected HTML to contain Facets TCO Analysis Tool")
	}
	if !strings.Contains(html, "https://www.facets.cloud/") {
		t.Errorf("expected HTML to link to facets.cloud")
	}
	if !strings.Contains(html, "android-chrome-192x192.png") {
		t.Errorf("expected HTML to contain hosted facets logo png")
	}
	if strings.Contains(html, "Direct link:") {
		t.Errorf("expected HTML to NOT contain direct link")
	}
}

func TestBuildJobFailedHTML(t *testing.T) {
	name := "Bob"
	prospect := "Beta Inc"
	jobID := "job-999"
	logsURL := "http://localhost:5173/jobs/job-999/logs"

	html := buildJobFailedHTML(name, prospect, jobID, logsURL)

	if !strings.Contains(html, "FACETS") {
		t.Errorf("expected HTML to contain FACETS brand")
	}
	if !strings.Contains(html, "Beta Inc") {
		t.Errorf("expected HTML to contain prospect name")
	}
	if !strings.Contains(html, "Report Generation Failed") {
		t.Errorf("expected HTML to indicate failure")
	}
	if !strings.Contains(html, logsURL) {
		t.Errorf("expected HTML to link to logs")
	}
}

func TestLiveResendSend(t *testing.T) {
	if os.Getenv("RESEND_API_KEY") == "" {
		t.Skip("skipping live Resend API test; set RESEND_API_KEY to run")
	}
	cfg := EmailConfig{
		APIKey:  os.Getenv("RESEND_API_KEY"),
		From:    "Facets Cloud <onboarding@resend.dev>",
		BaseURL: "http://localhost:5173",
	}
	err := SendJobReady(cfg, "arnav.chaurasia@facets.cloud", "Arnav", "Stripe Global", "test-job-123", 4500.50)
	if err != nil {
		t.Fatalf("SendJobReady error: %v", err)
	}
}

