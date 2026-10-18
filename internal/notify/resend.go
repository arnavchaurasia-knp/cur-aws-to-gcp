package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// EmailConfig is provider-agnostic — wired to Resend.
type EmailConfig struct {
	APIKey  string // Resend API key (re_...)
	From    string // From address. Bare email or "Name <email>" form.
	BaseURL string // App base URL — used to build the report-download link.
}

const resendEndpoint = "https://api.resend.com/emails"

// SendJobReady sends a polished HTML email notifying the user that their cost projection is ready.
func SendJobReady(cfg EmailConfig, toEmail, toName, prospect, jobID string, awsSpend ...float64) error {
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	if baseURL == "" {
		baseURL = "http://localhost:8080"
	}
	reportURL := fmt.Sprintf("%s/jobs/%s", baseURL, jobID)

	displayName := toName
	if strings.Contains(displayName, "@") {
		displayName = strings.Split(displayName, "@")[0]
	}
	if displayName == "" {
		displayName = "there"
	}

	spendText := ""
	if len(awsSpend) > 0 && awsSpend[0] > 0 {
		spendText = fmt.Sprintf("$%.2f", awsSpend[0])
	}

	subject := fmt.Sprintf("Your AWS → GCP Cost Projection for %s is ready", prospect)
	textBody := fmt.Sprintf(
		"Hi %s,\n\nYour AWS → GCP cost projection report for %s is ready to view.\n\nJob ID: %s\nView Report: %s\n\n— Facets Cloud (https://www.facets.cloud)",
		displayName, prospect, jobID, reportURL,
	)

	htmlBody := buildJobReadyHTML(displayName, prospect, jobID, reportURL, spendText)

	return sendResend(cfg, toEmail, subject, textBody, htmlBody)
}

// SendContactInterest emails the admin distribution when a signed-in
// user clicks the landing-page contact CTA.
func SendContactInterest(cfg EmailConfig, toEmails []string, contactName, contactEmail, message string) error {
	if len(toEmails) == 0 {
		return nil
	}
	subject := fmt.Sprintf("Contact request from %s", contactName)
	body := fmt.Sprintf("%s (%s) requested help from the CUR page.\n\n", contactName, contactEmail)
	if message != "" {
		body += fmt.Sprintf("Message:\n%s\n\n", message)
	} else {
		body += "No message attached — the click itself is the signal.\n\n"
	}
	body += "— Facets Cloud"

	var firstErr error
	for _, to := range toEmails {
		if err := sendResend(cfg, to, subject, body, ""); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// SendJobFailed alerts the user that report generation failed.
func SendJobFailed(cfg EmailConfig, toEmail, toName, prospect string) error {
	subject := fmt.Sprintf("Report generation failed for %s", prospect)
	body := fmt.Sprintf(
		"Hi %s,\n\nUnfortunately the report for %s failed to generate. Our team has been notified and will look into it.\n\n— Facets Cloud",
		toName, prospect,
	)
	return sendResend(cfg, toEmail, subject, body, "")
}

func sendResend(cfg EmailConfig, toEmail, subject, text, html string) error {
	if cfg.APIKey == "" {
		return fmt.Errorf("resend api key not configured (set RESEND_API_KEY in .env)")
	}
	from := cfg.From
	if from == "" {
		from = "Facets Cloud <onboarding@resend.dev>"
	}

	payloadData := map[string]any{
		"from":    from,
		"to":      []string{toEmail},
		"subject": subject,
		"text":    text,
	}
	if html != "" {
		payloadData["html"] = html
	}

	payload, err := json.Marshal(payloadData)
	if err != nil {
		return err
	}

	req, err := http.NewRequest("POST", resendEndpoint, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+cfg.APIKey)
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("resend error: %d %s", resp.StatusCode, string(body))
	}
	return nil
}

func buildJobReadyHTML(name, prospect, jobID, reportURL, spend string) string {
	spendRow := ""
	if spend != "" {
		spendRow = fmt.Sprintf(`
			<tr>
				<td style="padding: 10px 16px; color: #64748B; font-size: 13px; border-bottom: 1px solid #F1F5F9;">AWS Spend Analyzed</td>
				<td style="padding: 10px 16px; color: #0F172A; font-weight: 600; font-size: 13px; text-align: right; border-bottom: 1px solid #F1F5F9;">%s</td>
			</tr>`, spend)
	}

	return fmt.Sprintf(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your GCP Cost Report is Ready</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F8FAFC; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1E293B;">
  <div style="max-width: 580px; margin: 30px auto; background-color: #FFFFFF; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05); border: 1px solid #E2E8F0;">
    
    <!-- Header banner -->
    <div style="background: linear-gradient(135deg, #0B0F19 0%%, #16131F 100%%); padding: 32px 24px; text-align: center; border-bottom: 2px solid #00C2BB;">
      <div style="font-size: 22px; font-weight: 700; color: #FFFFFF; letter-spacing: 0.5px;">
        <span style="color: #645DF6;">FACETS</span><span style="color: #00C2BB;">.CLOUD</span>
      </div>
      <div style="margin-top: 6px; color: #94A3B8; font-size: 11px; text-transform: uppercase; letter-spacing: 2.5px; font-weight: 600;">
        AWS → GCP Cost Estimator
      </div>
    </div>

    <!-- Main Content -->
    <div style="padding: 32px 28px;">
      <h1 style="margin: 0 0 12px 0; font-size: 20px; font-weight: 600; color: #0F172A; line-height: 1.3;">
        Your Cost Projection is Ready
      </h1>
      <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #475569;">
        Hi <strong>%s</strong>, the AWS to Google Cloud cost projection report for <strong>%s</strong> has completed processing.
      </p>

      <!-- Details Summary Card -->
      <table style="width: 100%%; border-collapse: collapse; background-color: #F8FAFC; border-radius: 8px; overflow: hidden; margin-bottom: 28px; border: 1px solid #E2E8F0;">
        <tr>
          <td style="padding: 10px 16px; color: #64748B; font-size: 13px; border-bottom: 1px solid #F1F5F9;">Target Account / Prospect</td>
          <td style="padding: 10px 16px; color: #0F172A; font-weight: 600; font-size: 13px; text-align: right; border-bottom: 1px solid #F1F5F9;">%s</td>
        </tr>
        <tr>
          <td style="padding: 10px 16px; color: #64748B; font-size: 13px; border-bottom: 1px solid #F1F5F9;">Report Job ID</td>
          <td style="padding: 10px 16px; color: #0F172A; font-family: monospace; font-size: 12px; text-align: right; border-bottom: 1px solid #F1F5F9;">#%s</td>
        </tr>
        %s
        <tr>
          <td style="padding: 10px 16px; color: #64748B; font-size: 13px;">Status</td>
          <td style="padding: 10px 16px; color: #0D9488; font-weight: 600; font-size: 13px; text-align: right;">Completed ✓</td>
        </tr>
      </table>

      <!-- CTA Button -->
      <div style="text-align: center; margin: 30px 0 24px 0;">
        <a href="%s" style="background-color: #645DF6; color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 8px; font-weight: 600; font-size: 14px; display: inline-block; box-shadow: 0 4px 12px rgba(100, 93, 246, 0.35);">
          View Cost Projection Report →
        </a>
      </div>

      <!-- Feature Notes -->
      <div style="margin-top: 24px; padding-top: 20px; border-top: 1px solid #E2E8F0; font-size: 12px; color: #64748B; line-height: 1.6;">
        <p style="margin: 0 0 6px 0;"><strong>What's inside:</strong></p>
        <ul style="margin: 0; padding-left: 20px;">
          <li>Deterministic line-item mapping to Google Cloud SKU equivalents</li>
          <li>On-Demand, 1-Year CUD, and 3-Year CUD pricing comparisons</li>
          <li>Interactive breakdown with Excel workbook and CSV export options</li>
        </ul>
      </div>

      <div style="margin-top: 20px; font-size: 11px; color: #94A3B8; word-break: break-all;">
        Or view your report at: <a href="%s" style="color: #645DF6; text-decoration: underline;">%s</a>
      </div>
    </div>

    <!-- Footer -->
    <div style="background-color: #F8FAFC; padding: 20px 24px; text-align: center; font-size: 11px; color: #94A3B8; border-top: 1px solid #E2E8F0;">
      <p style="margin: 0 0 4px 0;">© 2026 <a href="https://www.facets.cloud" style="color: #64748B; text-decoration: none;">Facets.cloud</a> · All rights reserved.</p>
      <p style="margin: 0;">Automated AWS to GCP Cloud Cost Projection Platform</p>
    </div>
  </div>
</body>
</html>`, name, prospect, prospect, jobID, spendRow, reportURL, reportURL, reportURL)
}
