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

func TestBuildStalePinAlertHTML(t *testing.T) {
	name := "Alice"
	baseURL := "http://localhost:5173"
	items := []StalePinAlertItem{
		{
			JobID:       "job-101",
			DirectiveID: "dir-1",
			PinnedItem:  "T2A",
			Region:      "africa-south1",
			Question:    "T2A isn't available in africa-south1 anymore — nearest (us-central1), global, or keep current?",
		},
		{
			JobID:       "job-102",
			DirectiveID: "dir-2",
			PinnedItem:  "C4A",
			Region:      "asia-south2",
			Question:    "C4A isn't available in asia-south2 anymore — nearest (asia-south1), global, or keep current?",
		},
	}
	reviewURL := "http://localhost:5173/jobs/job-101/review"

	html := buildStalePinAlertHTML(name, items, baseURL, reviewURL)
	if !strings.Contains(html, "Catalog Refresh Notice") {
		t.Errorf("expected HTML to contain Catalog Refresh Notice")
	}
	if !strings.Contains(html, "job-101") || !strings.Contains(html, "job-102") {
		t.Errorf("expected HTML to contain both batched job IDs")
	}
	if !strings.Contains(html, "T2A") || !strings.Contains(html, "C4A") {
		t.Errorf("expected HTML to contain pinned item names")
	}
	if !strings.Contains(html, reviewURL) {
		t.Errorf("expected HTML to link to review panel")
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

