# WORK ORDER — Frontend currency: reflect the bill's real currency, delete the fake toggle

**Audience:** an implementation model with NO prior context on this repo. Follow the steps
literally and in order. Do not redesign. Do not add features not listed here. When a step says
"delete", delete. When unsure, prefer the smallest change that satisfies the acceptance check.

---

## 0. Background you MUST understand before touching anything

This app ingests an AWS bill and projects the cost onto GCP. A bill can arrive in a **non-USD
currency** (EUR, GBP, INR, JPY, …).

**The backend already handles foreign-currency bills correctly and completely.** Do not rebuild
this. Here is how it works — read it, then verify the two file references still match the code:

1. `skill/aws-gcp-cost-projection/scripts/ingest.py` detects the bill currency from the CUR
   `currencyCode` column (or from symbols/text for PDF/Excel bills), then **normalizes every cost
   to USD** in the `aws_amortized_cost` column and stores the native amount + currency code
   alongside. (See the block near the comment "Multi-currency (Directive 1)".)
2. `skill/aws-gcp-cost-projection/scripts/currency_utils.py` resolves the USD↔currency FX rate in
   this priority order: **rate stated on the bill → live API (open.er-api.com) → committed
   fallback table (`data/fx_rates.json`) → hard error** (never silently assumes 1.0). It writes a
   `currency.json` file and a `bill_currency` table into the job's
   `projection-audit/projection.duckdb`.
3. `skill/aws-gcp-cost-projection/scripts/render_report.py` loads that currency info and renders
   **both the AWS side and the GCP side in the bill's native currency** (AWS × `aws_fx_rate`,
   GCP × `fx_rate`). The downloadable HTML report is therefore already correct.

**What is BROKEN is only the React frontend.** It contains a second, parallel, FAKE currency
system that ignores all of the above:

- `frontend/src/lib/preferencesContext.tsx` defines `CURRENCIES` with **hardcoded, stale FX rates**
  (e.g. `INR: 83.5` — the real committed rate in `data/fx_rates.json` is ~94.9) and a
  `formatCurrency(amountUSD)` that multiplies by those fake rates.
- It is driven by a **user-chosen global toggle** (page `frontend/src/pages/CurrencySettings.tsx`,
  stored in `localStorage`), which has nothing to do with what currency any bill was actually in.
- It is applied on only TWO screens: `frontend/src/pages/JobStatus.tsx` and
  `frontend/src/pages/ReleaseDashboard.tsx`. The values it converts (`aws_spend`, `gcp_od`) are
  **already USD** coming from the Go backend, so the fake toggle produces numbers that are both
  wrong (stale rate) and meaningless (a EUR bill shown in JPY).
- The same file also defines a `language` / `LANGUAGES` selector that **nothing reads** — it is
  completely dead.

**Goal of this work order:** make the frontend tell the truth. Each job must display its amounts
in the currency the bill was actually in — using the backend's real detected rate — and the fake
global currency toggle and the dead language selector must be removed.

---

## PHASE A — Delete the dead language selector (safe, do this first)

**A1.** In `frontend/src/lib/preferencesContext.tsx`:
- Remove the `LANGUAGES` export, the `language` state, `setLanguage`, and `language`/`setLanguage`
  from the context type and provider value. Keep `currency`, `setCurrency`, `formatCurrency` for
  now (Phase C changes them).

**A2.** Find every reference and remove the language UI:
```
cd frontend/src && grep -rn "LANGUAGES\|setLanguage\|\blanguage\b" --include=*.tsx --include=*.ts .
```
For each hit that is a language **selector / dropdown / setting UI**, delete that UI block. Do NOT
touch anything unrelated that merely contains the substring "language". If a component only read
`language` for display, delete that usage.

**A3. Acceptance:** `npm run build` (in `frontend/`) succeeds, and the grep from A2 returns no
`LANGUAGES` / `setLanguage` references. No language picker is reachable in the UI.

---

## PHASE B — Surface the real per-job currency from the backend

The frontend needs, per job, the detected currency so it can display native amounts. The data
already exists in each job's `projection-audit/projection.duckdb` in the `bill_currency` table
(columns: `currency, symbol, fx_rate, source, label, aws_cost_currency, aws_fx_rate`).

**B1. Add a Go query.** In `internal/jobs/duckdb.go`, add a function modeled on the existing
`QueryRunResults` / `runDuckDB` pattern already in that file:

```go
// BillCurrency mirrors the bill_currency table written by currency_utils.py.
type BillCurrency struct {
    Currency       string  `json:"currency"`         // e.g. "EUR"; "USD" default
    Symbol         string  `json:"symbol"`           // e.g. "€"
    FXRate         float64 `json:"fx_rate"`           // USD -> display currency (for GCP side)
    AWSFXRate      float64 `json:"aws_fx_rate"`       // USD -> display currency (for AWS side)
    AWSCostCurrency string `json:"aws_cost_currency"` // currency the stored USD came from
    Label          string  `json:"label"`
    Source         string  `json:"source"`
}

// QueryBillCurrency reads the single bill_currency row. Returns a USD default
// (fx 1.0) when the table or row is absent so callers never special-case nil.
func QueryBillCurrency(dbPath string) (BillCurrency, error) {
    def := BillCurrency{Currency: "USD", Symbol: "$", FXRate: 1.0, AWSFXRate: 1.0, AWSCostCurrency: "USD", Label: "USD", Source: "default"}
    out, err := runDuckDB(dbPath, "SELECT currency, symbol, fx_rate, aws_fx_rate, aws_cost_currency, label, source FROM bill_currency LIMIT 1")
    if err != nil {
        if errors.Is(err, os.ErrNotExist) { return def, nil }
        return def, err
    }
    var rows []BillCurrency
    if err := json.Unmarshal(out, &rows); err != nil || len(rows) == 0 {
        return def, nil // table missing / empty -> USD default
    }
    r := rows[0]
    if r.FXRate == 0 { r.FXRate = 1.0 }
    if r.AWSFXRate == 0 { r.AWSFXRate = 1.0 }
    if r.Symbol == "" { r.Symbol = "$" }
    if r.Currency == "" { r.Currency = "USD" }
    return r, nil
}
```
NOTE: the `bill_currency` table may not have `aws_fx_rate` on very old jobs. The `COALESCE`/default
handling above covers a missing value, but if the SELECT itself errors because the **column**
doesn't exist, fall back to `SELECT currency, symbol, fx_rate, label, source` and set the rate
fields to the defaults. Keep it defensive — a missing column must never 500 the endpoint.

**B2. Expose it on the runs endpoint.** In `internal/jobs/handler.go`, the `Runs` handler
(function `func (h *Handler) Runs`, ~line 1107) currently does `QueryRunResults(dbPath)` then
`json.NewEncoder(w).Encode(runs)`. Change it to return an object that includes the currency:

```go
runs, err := QueryRunResults(dbPath)
// ... existing error handling unchanged ...
cur, _ := QueryBillCurrency(dbPath) // best-effort; defaults to USD on error
json.NewEncoder(w).Encode(map[string]any{
    "runs":     runs,
    "currency": cur,
})
```
**IMPORTANT:** this changes the response shape from a bare array to `{runs, currency}`. You MUST
update the frontend caller in the SAME change (Phase C), or the runs list breaks. Find the caller:
```
cd frontend/src && grep -rn "api/jobs.*runs\|/runs\|QueryRunResults\|listRuns\|getRuns" .
```
Look in `frontend/src/api/*.ts` for the function that fetches `/api/jobs/{id}/runs` and update its
return type + parsing to read `.runs` and `.currency`.

**B3. Acceptance:** `go build ./...` succeeds. `go test ./internal/jobs/...` passes (fix/adjust any
test that asserts the old bare-array runs shape). Hitting the runs endpoint for a USD job returns
`currency.currency == "USD"` and `fx_rate == 1.0`.

---

## PHASE C — Make the frontend display native per-job currency (not a fake global toggle)

**C1. Replace `formatCurrency` semantics.** In `frontend/src/lib/preferencesContext.tsx`:
- DELETE the hardcoded `CURRENCIES` rate table and the global `currency`/`setCurrency` state.
- Replace `formatCurrency` with a pure formatter that takes an explicit symbol + already-converted
  amount — NO hardcoded rates, NO global currency:

```ts
export function formatMoney(amount: number, symbol = "$", maxFractionDigits = 2): string {
  const digits = symbol === "¥" || symbol === "₩" ? 0 : maxFractionDigits
  return `${symbol}${amount.toLocaleString(undefined, {
    minimumFractionDigits: digits > 0 ? 2 : 0,
    maximumFractionDigits: digits,
  })}`
}
```
This is a plain exported function, not a hook. Remove `PreferencesProvider`/`usePreferences` if
nothing else uses them after this phase (check with grep); if other code still imports the provider
for unrelated reasons, leave a minimal provider but strip currency/language from it.

**C2. JobStatus.tsx** (`frontend/src/pages/JobStatus.tsx`): the run data now arrives with a
`currency` object (from Phase B). Convert each displayed amount to native currency at the point of
display:
- AWS-side amounts (`aws_spend`, `aws_total`): `formatMoney(value * currency.aws_fx_rate, currency.symbol)`
- GCP-side amounts (`gcp_od`, `gcp_1yr_cud`, `gcp_3yr_cud`): `formatMoney(value * currency.fx_rate, currency.symbol)`
- Replace every `formatDollars(...)` / `usePreferences()` / `.toFixed(2)` money render on this page
  accordingly. Remove the `usePreferences` import.
- Add a small caption near the totals showing `currency.label` (e.g. the "Converted at 1 USD = …"
  string) so the viewer knows the basis, but ONLY when `currency.currency !== "USD"`.

**C3. ReleaseDashboard.tsx** (`frontend/src/pages/ReleaseDashboard.tsx`): this page lists MANY jobs
from `listJobs()`, and the job-list rows do NOT currently carry per-job currency (only the runs
endpoint does). Two acceptable options — pick ONE:
  - **C3-a (preferred, minimal):** show list amounts in plain USD with a `$` prefix using
    `formatMoney(value, "$")`, since `aws_spend`/`gcp_od` from the list API are USD. Remove the
    `usePreferences`/fake-conversion usage. (The per-bill native currency is shown on the job's
    own detail page via C2 — that's the honest place for it.)
  - **C3-b (only if the user explicitly wants native currency in the list):** extend the Go job
    list (`internal/db/jobs.go` `Job` struct + the list query) to carry `currency`/`symbol`/
    `aws_fx_rate`/`fx_rate`, populated when the job completes. This needs a schema migration and
    is OUT OF SCOPE unless asked — do NOT do it on your own initiative.

Default to **C3-a**.

**C4. Delete the fake global currency page + route.**
- Delete `frontend/src/pages/CurrencySettings.tsx`.
- In `frontend/src/App.tsx`, delete the `CurrencySettings` import and the
  `/preferences/currency` route.
- In `frontend/src/components/Nav.tsx` (and `Sidebar.tsx` if present), delete the link to
  `/preferences/currency`.
- Grep to confirm nothing still imports `CurrencySettings`, `usePreferences`, `CURRENCIES`, or
  `setCurrency`:
```
cd frontend/src && grep -rn "CurrencySettings\|usePreferences\|CURRENCIES\|setCurrency\|preferences/currency" .
```
All hits must be gone (or reduced to a trimmed provider per C1).

**C5. Acceptance:**
- `npm run build` in `frontend/` succeeds with no type errors.
- Open a completed USD job's status page: amounts show with `$`, no behavioral change vs before.
- For a non-USD bill (see test note below), the job status page shows amounts with the bill's real
  symbol and the `currency.label` caption, and the numbers match the downloadable HTML report's
  totals (same currency basis).
- There is no currency selector and no language selector anywhere in the UI.

---

## What you MUST NOT touch

- Any Python under `scripts/` — the ingest/FX/report currency logic is correct and already shipped.
  Do not change `currency_utils.py`, `render_report.py`, `ingest.py`, or `data/fx_rates.json`.
- The stored `aws_spend` / `gcp_od` values (they are USD by design — convert only at display).
- The report HTML generation.

## How to test a non-USD bill end to end

1. Find or create a small CUR with `lineItem/CurrencyCode = EUR` (or run an existing EUR test
   fixture if one exists under the tests directory — grep `tests/` for "currency" or "EUR").
2. Run the pipeline for that bill so a `projection.duckdb` with a `bill_currency` row exists.
3. Call the runs endpoint and confirm `currency.currency == "EUR"`, `fx_rate`/`aws_fx_rate` > 0.
4. Open the job status page and confirm euro symbols + a conversion-basis caption, and that totals
   equal the generated HTML report's totals.

## Definition of done

- [ ] Language selector fully removed (Phase A).
- [ ] `QueryBillCurrency` added; runs endpoint returns `{runs, currency}` (Phase B).
- [ ] `formatMoney` replaces the hardcoded-rate `formatCurrency`; JobStatus renders native
      per-job currency using the backend's real rate (Phase C1–C2).
- [ ] ReleaseDashboard shows honest USD (C3-a).
- [ ] `CurrencySettings` page, its route, and its nav link deleted; no dangling imports (C4).
- [ ] `go build ./...`, `go test ./internal/jobs/...`, and `npm run build` all pass.
