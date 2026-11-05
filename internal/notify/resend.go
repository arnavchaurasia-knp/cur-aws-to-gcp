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

	htmlBody := buildJobReadyHTML(displayName, prospect, jobID, reportURL, spendText, baseURL)

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

// StalePinAlertItem describes an invalidated pinned directive for a user.
type StalePinAlertItem struct {
	JobID       string `json:"job_id"`
	DirectiveID string `json:"directive_id"`
	PinnedItem  string `json:"pinned_item"`
	Region      string `json:"region"`
	Question    string `json:"question"`
}

// SendStalePinAlert sends an email to the user notifying them of pinned directives invalidated by a catalog refresh.
// Multiple stale pins for the user are batched into a single email.
func SendStalePinAlert(cfg EmailConfig, toEmail, toName string, items []StalePinAlertItem) error {
	if len(items) == 0 {
		return nil
	}
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	if baseURL == "" {
		baseURL = "http://localhost:8080"
	}

	displayName := toName
	if strings.Contains(displayName, "@") {
		displayName = strings.Split(displayName, "@")[0]
	}
	if displayName == "" {
		displayName = "there"
	}

	primaryJobID := items[0].JobID
	reviewURL := fmt.Sprintf("%s/jobs/%s/review", baseURL, primaryJobID)

	subject := fmt.Sprintf("Action Required: %d pinned item(s) need review following catalog refresh", len(items))
	if len(items) == 1 {
		subject = fmt.Sprintf("Action Required: Pinned %s needs review following catalog refresh", items[0].PinnedItem)
	}

	var textBuilder strings.Builder
	textBuilder.WriteString(fmt.Sprintf("Hi %s,\n\nA recent Google Cloud catalog refresh invalidated %d of your pinned directives:\n\n", displayName, len(items)))
	for _, it := range items {
		textBuilder.WriteString(fmt.Sprintf("• Job %s: %s\n", it.JobID, it.Question))
		textBuilder.WriteString(fmt.Sprintf("  Review: %s/jobs/%s/review\n\n", baseURL, it.JobID))
	}
	textBuilder.WriteString("Your current projections remain unchanged until you choose an option.\n\n— Facets Cloud")

	htmlBody := buildStalePinAlertHTML(displayName, items, baseURL, reviewURL)
	return sendResend(cfg, toEmail, subject, textBuilder.String(), htmlBody)
}

func buildStalePinAlertHTML(name string, items []StalePinAlertItem, baseURL, primaryReviewURL string) string {
	var rowsHTML strings.Builder
	for _, it := range items {
		jobReviewURL := fmt.Sprintf("%s/jobs/%s/review", baseURL, it.JobID)
		rowsHTML.WriteString(fmt.Sprintf(`
          <tr>
            <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">
              <strong>Job %s</strong> (%s)
            </td>
            <td style="padding: 12px 18px; color: #0F172A; font-size: 13px; text-align: right; border-bottom: 1px solid #EDF2F7;">
              <a href="%s" style="color: #645DF6; text-decoration: underline; font-weight: 600;">Review Pin</a>
            </td>
          </tr>
          <tr>
            <td colspan="2" style="padding: 6px 18px 14px 18px; color: #475569; font-size: 12px; border-bottom: 1px solid #EDF2F7;">
              %s
            </td>
          </tr>`, it.JobID, it.PinnedItem, jobReviewURL, it.Question))
	}

	return fmt.Sprintf(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Catalog Refresh: Pinned Items Review</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F8FAFC; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #F8FAFC; padding: 40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="max-width: 580px; background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
          <tr>
            <td style="padding: 36px 36px 28px 36px;">
              <span style="color: #D97706; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">
                ● Catalog Refresh Notice
              </span>
              <h1 style="margin: 10px 0; font-size: 22px; font-weight: 700; color: #0F172A;">
                Pinned Directive Needs Review
              </h1>
              <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                Hi <strong>%s</strong>, a catalog refresh updated Google Cloud SKU availability. The following pinned item(s) are no longer offered in their target regions:
              </p>
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; margin-bottom: 24px; border-collapse: separate;">
                %s
              </table>
              <p style="font-size: 13px; color: #64748B; margin-bottom: 24px;">
                Your existing projections continue to honor the last-known rates and will not change until you select a preferred replacement in the refinement panel.
              </p>
              <div style="text-align: center; margin: 26px 0;">
                <a href="%s" style="background-color: #645DF6; color: #FFFFFF; text-decoration: none; padding: 13px 30px; border-radius: 8px; font-weight: 600; font-size: 14px; display: inline-block;">
                  Open Refinement Panel &rarr;
                </a>
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`, name, rowsHTML.String(), primaryReviewURL)
}

// SendJobFailed alerts the user that report generation failed.
func SendJobFailed(cfg EmailConfig, toEmail, toName, prospect string, jobID ...string) error {
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	if baseURL == "" {
		baseURL = "http://localhost:8080"
	}
	id := ""
	if len(jobID) > 0 {
		id = jobID[0]
	}

	logsURL := ""
	if id != "" {
		logsURL = fmt.Sprintf("%s/jobs/%s/logs", baseURL, id)
	} else {
		logsURL = fmt.Sprintf("%s/bills", baseURL)
	}

	displayName := toName
	if strings.Contains(displayName, "@") {
		displayName = strings.Split(displayName, "@")[0]
	}
	if displayName == "" {
		displayName = "there"
	}

	subject := fmt.Sprintf("Report generation failed for %s", prospect)
	textBody := fmt.Sprintf(
		"Hi %s,\n\nUnfortunately the cost projection report for %s encountered an issue during processing.\n\nJob ID: %s\nView Logs: %s\n\nOur team has been notified. You can retry the job from the console.\n\n— Facets Cloud (https://www.facets.cloud)",
		displayName, prospect, id, logsURL,
	)

	htmlBody := buildJobFailedHTML(displayName, prospect, id, logsURL)
	return sendResend(cfg, toEmail, subject, textBody, htmlBody)
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

func buildJobReadyHTML(name, prospect, jobID, reportURL, spend, baseURL string) string {
	spendRow := ""
	if spend != "" {
		spendRow = fmt.Sprintf(`
              <tr>
                <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">AWS Monthly Spend</td>
                <td style="padding: 12px 18px; color: #0F172A; font-weight: 700; font-size: 14px; text-align: right; border-bottom: 1px solid #EDF2F7;">%s</td>
              </tr>`, spend)
	}

	return fmt.Sprintf(`<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>Your AWS → GCP Cost Projection is Ready</title>
  <!--[if mso]>
  <noscript>
    <xml>
      <o:OfficeDocumentSettings>
        <o:AllowPNG/>
        <o:PixelsPerInch>96</o:PixelsPerInch>
      </o:OfficeDocumentSettings>
    </xml>
  </noscript>
  <![endif]-->
  <style>
    body, table, td, a { -webkit-text-size-adjust: 100%%; -ms-text-size-adjust: 100%%; }
    table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
    img { -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }
    @media only screen and (max-width: 620px) {
      .email-container { width: 100%% !important; max-width: 100%% !important; border-radius: 0 !important; }
      .mobile-padding { padding-left: 20px !important; padding-right: 20px !important; }
      .mobile-stack { display: block !important; width: 100%% !important; }
    }
  </style>
</head>
<body style="margin: 0; padding: 0; background-color: #0A0D14; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #1E293B;">

  <!-- Hidden Preview Text (Preheader) -->
  <div style="display: none; font-size: 1px; color: #0A0D14; line-height: 1px; max-height: 0px; max-width: 0px; opacity: 0; overflow: hidden; mso-hide: all;">
    Your AWS to GCP cost projection report for %s is ready to view. Access the full line-item analysis and CUD models.
    &#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;
  </div>

  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #0A0D14; padding: 40px 10px;">
    <tr>
      <td align="center">
        <!-- Container -->
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="580" class="email-container" style="max-width: 580px; width: 100%%; background-color: #FFFFFF; border-radius: 14px; overflow: hidden; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45); border: 1px solid #1E293B;">
          
          <!-- Brand Header Banner -->
          <tr>
            <td style="background: linear-gradient(135deg, #0B0F19 0%%, #121024 100%%); padding: 26px 32px; border-bottom: 2px solid #00C2BB;">
              <!-- Facets Brand Logo & Tool Title on Left -->
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%">
                <tr>
                  <td style="vertical-align: middle; width: 44px; padding-right: 14px;">
                    <a href="https://www.facets.cloud/" target="_blank" style="text-decoration: none; display: block;">
                      <img src="https://www.facets.cloud/assets/facets-favicon/android-chrome-192x192.png" width="40" height="40" alt="Facets Logo" style="display: block; border-radius: 8px; border: 0;" />
                    </a>
                  </td>
                  <td style="vertical-align: middle; text-align: left;">
                    <a href="https://www.facets.cloud/" target="_blank" style="text-decoration: none; display: block;">
                      <div style="font-size: 19px; font-weight: 800; letter-spacing: 0.5px; color: #FFFFFF; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.2;">
                        <span style="color: #645DF6;">FACETS</span><span style="color: #00C2BB;">.CLOUD</span>
                      </div>
                      <div style="color: #94A3B8; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; margin-top: 3px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                        Facets TCO Analysis Tool
                      </div>
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Main Content Body -->
          <tr>
            <td class="mobile-padding" style="padding: 36px 36px 28px 36px;">
              
              <!-- Status Pill Badge -->
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-bottom: 20px;">
                <tr>
                  <td style="background-color: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 9999px; padding: 4px 14px;">
                    <span style="color: #065F46; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">
                      ● Analysis Complete
                    </span>
                  </td>
                </tr>
              </table>

              <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 700; color: #0F172A; line-height: 1.35; letter-spacing: -0.3px;">
                Your Cost Projection is Ready
              </h1>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                Hi <strong style="color: #0F172A;">%s</strong>, the AWS to Google Cloud cost projection report for <strong style="color: #0F172A;">%s</strong> has completed processing successfully.
              </p>

              <!-- Summary Card -->
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; margin-bottom: 30px; border-collapse: separate; overflow: hidden;">
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">Target Account / Prospect</td>
                  <td style="padding: 12px 18px; color: #0F172A; font-weight: 600; font-size: 13px; text-align: right; border-bottom: 1px solid #EDF2F7;">%s</td>
                </tr>
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">Job Reference ID</td>
                  <td style="padding: 12px 18px; color: #645DF6; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; font-weight: 600; text-align: right; border-bottom: 1px solid #EDF2F7;">#%s</td>
                </tr>
                %s
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500;">Status</td>
                  <td style="padding: 12px 18px; color: #059669; font-weight: 700; font-size: 13px; text-align: right;">
                    Ready to View ✓
                  </td>
                </tr>
              </table>

              <!-- CTA Button Section with Outlook VML Fallback -->
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="margin: 28px 0 24px 0;">
                <tr>
                  <td align="center">
                    <!--[if mso]>
                    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="%s" style="height:48px;v-text-anchor:middle;width:280px;" arcsize="16%%" stroke="f" fillcolor="#645DF6">
                      <w:anchorlock/>
                      <center style="color:#ffffff;font-family:sans-serif;font-size:14px;font-weight:700;">View Cost Projection Report &rarr;</center>
                    </v:roundrect>
                    <![endif]-->
                    <!--[if !mso]><!-->
                    <a href="%s" target="_blank" style="display: inline-block; background-color: #645DF6; background-image: linear-gradient(135deg, #645DF6 0%%, #4F46E5 100%%); color: #FFFFFF; font-size: 14px; font-weight: 700; text-decoration: none; padding: 14px 34px; border-radius: 8px; box-shadow: 0 4px 14px rgba(100, 93, 246, 0.35); letter-spacing: 0.2px; text-align: center;">
                      View Cost Projection Report &rarr;
                    </a>
                    <!--<![endif]-->
                  </td>
                </tr>
              </table>

              <!-- What's Inside Feature Box -->
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #FAFAFD; border: 1px dashed #D4D4F0; border-radius: 8px; padding: 16px 18px; margin-top: 24px;">
                <tr>
                  <td>
                    <div style="font-size: 12px; font-weight: 700; color: #1E1B4B; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px;">
                      What's Included In This Report:
                    </div>
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="font-size: 12px; color: #475569; line-height: 1.65;">
                      <tr>
                        <td style="padding-bottom: 4px; vertical-align: top; width: 16px; color: #00C2BB;">✔</td>
                        <td style="padding-bottom: 4px; padding-left: 6px;">
                          <strong>1:1 SKU Mapping:</strong> Deterministic AWS line items matched to Google Cloud catalog rates.
                        </td>
                      </tr>
                      <tr>
                        <td style="padding-bottom: 4px; vertical-align: top; width: 16px; color: #00C2BB;">✔</td>
                        <td style="padding-bottom: 4px; padding-left: 6px;">
                          <strong>Commitment Scenarios:</strong> Side-by-side On-Demand, 1-Year CUD, and 3-Year CUD projections.
                        </td>
                      </tr>
                      <tr>
                        <td style="vertical-align: top; width: 16px; color: #00C2BB;">✔</td>
                        <td style="padding-left: 6px;">
                          <strong>Direct Exports:</strong> Download presentation-ready Excel workbooks and audit-ready DuckDB extracts.
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color: #F8FAFC; padding: 24px 32px; text-align: center; font-size: 11px; color: #94A3B8; border-top: 1px solid #E2E8F0;">
              <p style="margin: 0 0 6px 0; color: #64748B;">
                © 2026 <a href="https://www.facets.cloud/" target="_blank" style="color: #645DF6; text-decoration: none; font-weight: 600;">Facets.cloud</a> · All rights reserved.
              </p>
              <p style="margin: 0; line-height: 1.5; color: #94A3B8;">
                This automated cost projection was generated by the Facets Engine.<br>
                To adjust notification preferences, visit <a href="%s/settings/notifications" style="color: #64748B; text-decoration: underline;">Notification Settings</a>.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>`, prospect, name, prospect, prospect, jobID, spendRow, reportURL, reportURL, baseURL)
}

func buildJobFailedHTML(name, prospect, jobID, logsURL string) string {
	jobRow := ""
	if jobID != "" {
		jobRow = fmt.Sprintf(`
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">Job Reference ID</td>
                  <td style="padding: 12px 18px; color: #DC2626; font-family: ui-monospace, monospace; font-size: 12px; font-weight: 600; text-align: right; border-bottom: 1px solid #EDF2F7;">#%s</td>
                </tr>`, jobID)
	}

	return fmt.Sprintf(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Report Generation Status</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0A0D14; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #1E293B;">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #0A0D14; padding: 40px 10px;">
    <tr>
      <td align="center">
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="580" style="max-width: 580px; width: 100%%; background-color: #FFFFFF; border-radius: 14px; overflow: hidden; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45); border: 1px solid #1E293B;">
          <tr>
            <td style="background: linear-gradient(135deg, #0B0F19 0%%, #121024 100%%); padding: 26px 32px; border-bottom: 2px solid #EF4444;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%">
                <tr>
                  <td style="vertical-align: middle; width: 44px; padding-right: 14px;">
                    <a href="https://www.facets.cloud/" target="_blank" style="text-decoration: none; display: block;">
                      <img src="https://www.facets.cloud/assets/facets-favicon/android-chrome-192x192.png" width="40" height="40" alt="Facets Logo" style="display: block; border-radius: 8px; border: 0;" />
                    </a>
                  </td>
                  <td style="vertical-align: middle; text-align: left;">
                    <a href="https://www.facets.cloud/" target="_blank" style="text-decoration: none; display: block;">
                      <div style="font-size: 19px; font-weight: 800; letter-spacing: 0.5px; color: #FFFFFF; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.2;">
                        <span style="color: #645DF6;">FACETS</span><span style="color: #00C2BB;">.CLOUD</span>
                      </div>
                      <div style="color: #94A3B8; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; margin-top: 3px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                        Facets TCO Analysis Tool
                      </div>
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 36px 36px 28px 36px;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-bottom: 16px;">
                <tr>
                  <td style="background-color: #FEF2F2; border: 1px solid #FECACA; border-radius: 9999px; padding: 4px 14px;">
                    <span style="color: #991B1B; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;">
                      ● Processing Issue
                    </span>
                  </td>
                </tr>
              </table>
              <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 700; color: #0F172A;">
                Report Generation Failed
              </h1>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                Hi <strong>%s</strong>, the cost projection analysis for <strong>%s</strong> could not complete. This can happen due to an unreadable billing file format, unmapped cloud provider, or temporary API rate limit.
              </p>
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; margin-bottom: 28px; border-collapse: separate;">
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500; border-bottom: 1px solid #EDF2F7;">Target Prospect</td>
                  <td style="padding: 12px 18px; color: #0F172A; font-weight: 600; font-size: 13px; text-align: right; border-bottom: 1px solid #EDF2F7;">%s</td>
                </tr>
                %s
                <tr>
                  <td style="padding: 12px 18px; color: #64748B; font-size: 13px; font-weight: 500;">Status</td>
                  <td style="padding: 12px 18px; color: #DC2626; font-weight: 700; font-size: 13px; text-align: right;">Failed (Retryable)</td>
                </tr>
              </table>
              <div style="text-align: center; margin: 26px 0;">
                <a href="%s" style="background-color: #0F172A; color: #FFFFFF; text-decoration: none; padding: 13px 30px; border-radius: 8px; font-weight: 600; font-size: 14px; display: inline-block;">
                  Inspect Logs & Retry &rarr;
                </a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background-color: #F8FAFC; padding: 20px 32px; text-align: center; font-size: 11px; color: #94A3B8; border-top: 1px solid #E2E8F0;">
              © 2026 <a href="https://www.facets.cloud/" target="_blank" style="color: #645DF6; text-decoration: none; font-weight: 600;">Facets.cloud</a> · All rights reserved.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`, name, prospect, prospect, jobRow, logsURL)
}
