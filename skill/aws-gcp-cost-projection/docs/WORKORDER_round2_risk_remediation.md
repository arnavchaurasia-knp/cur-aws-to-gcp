# WORKORDER — Round-2 Risk Remediation (security, numerics, LLM, concurrency, staleness, ingestion, tests)

**Created:** 2026-11-03
**Source:** 8 independent read-only audit agents covering issue classes the product owner did not explicitly name (security/SQLi, numerical-correctness, LLM reliability/injection, concurrency/data-integrity, price/FX/region freshness, scale/performance, ingestion edge-cases, test-suite quality). Five CRITICALs were manually re-verified against live code by the coordinator (noted inline). This is the untouched baseline — nothing here is implemented yet.
**Repo root:** `C:\Users\arnav\Downloads\cur-web-AGY`  ·  **Skill root:** `skill/aws-gcp-cost-projection/`

---

> ## 🤖 READ FIRST — instructions for the implementing AI
> **Every item below is a confirmed or suspected defect.** There is no "already fine" filler.
> 1. **Work top-to-bottom by STAGE.** Stage 1 (security) must land before this app is exposed to any untrusted user or bill — treat it as P0.
> 2. **For each item: apply the Fix, then make its Acceptance criteria pass before moving on.** If you can't meet one, STOP and report.
> 3. **Industry-grade bar:** fail loud, never silent; add a regression test that would have caught the bug; no magic numbers; keep public signatures/DB schema unless the item says otherwise; update docs + the Verification table in the same commit.
> 4. **Security items get a test that proves the exploit is closed** (the attack returns 401/403/400, not success).
> 5. **Verify with the suites:** `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q` (use `python`, not `python3`, on this Windows box) and `go build ./... && go test ./...` from repo root.
> 6. **Decisions:** several items require a call (SP/RI headline basis, SUD disclosure). Make it, justify it in the commit, encode it — don't leave it ambiguous.
> 7. **Git:** branch off `main`, commit per item, push to personal remotes only (`origin2/AGY`, `github-personal/AGY`) — never company origin.
> **If code contradicts this file, trust the code and flag it** — file:line refs were accurate at handoff (2026-11-03).

---

## Verification status — pre-implementation baseline

| ID | Severity | Claim | State |
|---|---|---|---|
| SEC-1 | 🔴 CRITICAL | TOFU password login auto-provisions **admin** for any unclaimed facets email; email path skips domain allowlist | ✅ DONE (users.go, oauth.go, security_test.go) |
| SEC-2 | 🔴 CRITICAL | SQL injection via uploaded CUR **column names** into DuckDB (local file read/write) | ✅ DONE (quote_ident in ingest.py, test_sec2_sqli.py) |
| ING-1 | 🔴 CRITICAL | Flat-CSV path indentation bug ingests **1 row per 5000-row chunk** | ✅ DONE (ingest.py lines indented, test_ing1_flat_csv.py) |
| ING-2 | 🔴 CRITICAL | Faithfulness check reconciles against catalog's own post-ingest sum, not raw-file total → can't detect extraction loss | ✅ DONE (raw file reconciliation in ingest.py, test_ingestion_edge_cases.py) |
| LLM-1 | 🔴 CRITICAL | Bill text interpolated **raw/undelimited** into prompts for an agent run with `--dangerously-skip-permissions` + `run_command` | ✅ reported (orchestrate.go:976-979; auto_review.py:345-352) |
| PRICE-1 | 🔴 CRITICAL | Bill priced against **today's** catalog (no as-of); max-age blocks only at Docker build; Windows refresh never advances `fetched_at` | ✅ reported (CATALOG_META.json; check_catalog_age.py; Dockerfile:71-72) |
| NUM-1 | 🔴 CRITICAL | Headline basis mismatch: render_report AWS **amortized** vs GCP **OD list**; can flip the verdict | ✅ reported (render_report.py:790-792 vs 818; accuracy_report.py:155) |
| NUM-2 | 🔴 CRITICAL | Passthrough rows grossed up to OD while AWS baseline is amortized → up to 1/(1-disc) overstatement (3.3x Spot) | ✅ reported (projection_view.py:381-400) |
| SEC-3 | 🟠 HIGH | OAuth `state` (CSRF) check skipped when `state` param omitted | ✅ DONE (fail closed in oauth.go Callback, security_test.go) |
| SEC-4 | 🟠 HIGH | Passwords hashed with salted single-round SHA-256 (not bcrypt/argon2) | ✅ DONE (bcrypt in users.go, upgrade on login, users_test.go) |
| PRICE-2 | 🟠 HIGH | resolve_sku lets description word-overlap override correct region; `re` used but never imported | ✅ reported (resolve_sku.py:297-331, 314) |
| PRICE-3 | 🟠 HIGH | region_prefix map missing af-south-1/ap-east-1/me-south/il-central-1/eu-south/ca-west-1 → silent global fallback | ✅ reported (region_prefix.py:26-70) |
| NUM-3 | 🟠 HIGH | Unpriced mapped rows (NULL rate) silently drop from GCP total in `--check-only` | ✅ reported (projection_view.py:383; validate_fix.py:1602) |
| NUM-4 | 🟠 HIGH | Credits/negatives net the AWS side down but have no coherent GCP counterpart | ✅ reported (ingest.py:479-481) |
| NUM-6 | 🟠 HIGH | No detection/guidance when the uploaded CUR lacks On-Demand cost columns (the basis for OD-vs-OD) | 🆕 DECIDED (owner): add CUR-config preflight + banner |
| LLM-2 | 🟠 HIGH | Zero determinism: no temperature/seed/max_tokens on any agy call | ✅ reported (orchestrate.go:976-979) |
| CONC-1 | 🟠 HIGH | Catalog swap renames `.duckdb` + `.wal` in two syscalls vs unlocked readers → torn read during weekly refresh | ✅ reported (build_catalog_index.py:128-136) |
| TEST-1 | 🟠 HIGH | No regression guard on total projected $; MOCK_SKU stubs the rate path; golden check `sys.exit(0)` | ✅ reported (build_pinned_corpus.py; test_pricer_correctness.py:36; verify_golden_mappings.py:99) |
| ING-3 | 🟠 HIGH | PDF path lossy (unanchored gross), + CUR/spreadsheet schemas silently misparse when the detecting column is renamed | ✅ DONE (broadened CUR signature in ingest.py) |
| CONC-2 | 🟡 MED | Phase-2 coverage retry self-defeating (merge wipes direct inserts); no zombie reaper between restarts; shared-cache lost updates | ✅ reported (merge_mappings.py:451; main.go:131; refresh_store.py:150) |
| ING-4 | 🟡 MED | EU decimal-comma / locale silently rescales; Python float paths mangle accounting parens → 0.0; no multi-account payer/linked dedup | ✅ DONE (parse_money in ingest.py, test_ingestion_edge_cases.py) |
| NUM-5 | 🟡 MED | Over/under gate computed per-component, misses break_down rows whose components SUM to >2x | ✅ reported (validate_fix.py:1818-1824) |
| LLM-3 | 🟡 MED | No output-token/batch cap on Phase-2; non-critical phases silently skip on LLM death without disclosure | ✅ reported (orchestrate.go:190,1687) |
| PRICE-4 | 🟡 MED | No SUD modeling (overstates always-on compute); flat $0.08 inter-region egress; GCP minimums ignored | ✅ reported (projection_view.py:268; egress-rates.json) |
| SCALE-1 | 🔴 CRITICAL | No global job-concurrency cap/worker pool (admins exempt) → host OOM under a few concurrent big bills | ✅ reported (handler.go:185-211,314) |
| SCALE-2 | 🔴 CRITICAL | No timeouts anywhere (per-script, per-LLM, whole-job); heartbeat defeats the 45-min stale-kill → can hang for hours/forever | ✅ reported (orchestrate.go:928,972-977; watcher.go:38) |
| SCALE-3 | 🔴 CRITICAL | LLM Phase-2 manifest uncapped/unchunked, no dedup by (canonical,charge_type) → token/cost explosion on diverse orgs | ✅ reported (classify_mechanics.py:1876-1996) |
| SCALE-4 | 🟠 HIGH | Downloaded HTML report emits one `<tr>` per distinct row, no pagination → browser hangs ~20k–50k rows | ✅ reported (render_report.py:994-1026,1653) |
| SCALE-5 | 🟠 HIGH | http.Server has no Read/Write/Idle timeouts; sync endpoints (Recalculate/Regenerate/Export) run Python in-request | ✅ reported (main.go:460; handler.go:1054) |
| SCALE-6 | 🟡 MED | Semicolon spreadsheet-export ingest path does full-raw `fetchall` + per-row Python loop → OOM (that format only) | ✅ reported (ingest.py:2064-2110) |

Flip each row to ✅ DONE + commit SHA as it lands.

---

# STAGE 1 — Security (P0, before any untrusted exposure)

### SEC-1 [CRITICAL] Trust-on-first-use login provisions admin
**Problem:** `internal/db/users.go:64-75` — on an unknown email `AuthenticateUser` INSERTs a new user with the supplied password and sets `role="admin"` when `isFacetsEmail(email)`. The email/password `Login` path (`internal/auth/oauth.go:111-234`, public route `cmd/server/main.go:210-211`) runs **no `allowedDomains` check**. So `POST /api/auth/login {email:"anything@facets.cloud", password:"x"}` for any unclaimed facets address creates a working **admin** account; any other email self-registers as viewer, bypassing the Google domain allowlist. Also: `oauth.go:165` admin-password compare is non-constant-time; `oauth.go:168` `devBypass` authenticates any email if ever enabled in prod.
**Fix:** Do not auto-provision on password login. Split authentication from provisioning: `AuthenticateUser` returns false for unknown emails (no INSERT). Provision users only through the Google-OAuth callback (which already enforces `allowedDomains`) or an explicit admin invite. Never derive `admin` from the email string at login time — seed admins explicitly. Remove/guard the devBypass branch so it is impossible in a non-dev build; make the admin-password compare constant-time (`subtle.ConstantTimeCompare`).
**Acceptance:** a Go test: `POST /api/auth/login` with an unknown facets email returns 401 and creates **no** row; unknown non-facets email returns 401; role is never set from email at login. devBypass cannot be true under the prod config.
**Risk:** changes onboarding — confirm the intended provisioning path (OAuth) still works; seed the first admin via migration.

### SEC-2 [CRITICAL] SQL injection via uploaded CUR column names
**Problem:** `ingest.py:672` `cl = {c.lower(): c for c in cols}` from the uploaded bill header (attacker-controlled); `ingest.py:691` (and 681, 751, 1164, 1202-1204, 1532-1558) interpolate `"{cl[col]}"`/paths into DuckDB SQL with no escaping. DuckDB exposes `read_text`/`read_blob`/`COPY … TO` → **server-side local-file read/write** (e.g. exfiltrate `.env` into the attacker's own report). Same pattern in `service_classifier.py:148`, `apply_commitment_ignores.py:58-64`, `schema_mapper.py`.
**Fix:** Introduce one `quote_ident(name)` helper that validates against `^[A-Za-z0-9 _/:.\-]+$` and doubles embedded `"`→`""`; route every bill-derived identifier through it. Pass file paths and values as **bound parameters**, never f-strings. Audit all `execute(f"…")` sites in scripts/ for bill-derived interpolation and convert them. Consider disabling DuckDB external-file functions for the ingest connection if not needed.
**Acceptance:** a test uploads a CUR whose header contains `x" FROM aws_raw; COPY(SELECT 1)TO 'pwn.csv'; --` and a `'`-bearing filename → ingest rejects or safely quotes, no file written, no error-based breakout. Add the malicious-header fixture to the suite.
**Risk:** identifier validation may reject legitimately odd column names — map them to safe internal names rather than failing the bill.

### SEC-3 [HIGH] OAuth state check skippable
**Problem:** `oauth.go:322-328` only compares `state` when both cookie and query param are non-empty, so omitting `state` bypasses CSRF protection (login-CSRF / fixation).
**Fix:** Fail closed — when an auth `code` is present, a matching `state` cookie+param is **required**; missing/mismatched → reject.
**Acceptance:** callback with code and no state → 400; test added.

### SEC-4 [HIGH] Weak password hashing
**Problem:** `users.go:27-32` salted single-round SHA-256 — fast, brute-forceable if the table leaks.
**Fix:** Migrate to `bcrypt` (or argon2id) with a sane cost; verify-and-upgrade legacy hashes on next successful login. (Lower priority if SEC-1 removes password self-registration, but still fix for any seeded accounts.)
**Acceptance:** new hashes are bcrypt; a test asserts the hash prefix and that verification works; legacy hash upgrades on login.

---

# STAGE 2 — Silent data loss in ingestion

### ING-1 [CRITICAL] Flat-CSV path ingests one row per chunk (indentation bug)
**Problem:** `ingest.py:2213-2221` — lines 2214-2218 are inside `for row in chunk:` but line 2220 (`if not desc …`) onward is dedented to the `for`'s own level, so the parse/aggregate/`continue` block runs **once per 5000-row chunk** using only the last row's values; the `continue` at 2221 iterates `while True`. A ≤5000-row Cost-Explorer-style CSV (no `Service` column, reached at `ingest.py:2163`) ingests exactly ONE row and the run reports success. **Coordinator-verified.**
**Fix:** Indent the entire body (2220 onward) into the `for row in chunk:` loop. Add a test that ingests a 100-row flat CSV totalling a known sum and asserts the catalog has 100 rows and the correct total.
**Acceptance:** 100-row / 12,000-row flat CSV ingests all rows; total matches; regression test added.
**Risk:** low — this is a clear bug fix; verify aggregation keys still behave.

### ING-2 [CRITICAL] Faithfulness reconciliation can't detect extraction loss
**Problem:** `ingest.py:2853` sets `_bill_reference_total = SUM(aws_amortized_cost) FROM aws_li_catalog` (the already-ingested catalog), then `check_faithfulness` (822-857) compares against that. So any rows dropped **during extraction** (e.g. ING-1) pass "Faithfulness OK." The only true external anchor is the PDF `stated_pretax` path.
**Fix:** Compute the reference total from the **raw file** (`SUM(cost_col) FROM aws_raw` before dedup/materiality), and reconcile the final catalog against that. Fail loud (write `failure.txt`) if they diverge beyond tolerance. This single change would have made ING-1 fail instead of ship.
**Acceptance:** a fixture that deliberately drops rows mid-parse now fails the faithfulness gate; a clean bill still passes.
**Risk:** may surface other latent loss paths — that's the point; triage them.

### ING-3 [HIGH] PDF lossiness + schema-detection fragility
**Problem:** PDF path (`ingest.py:1235,1517`) keeps gross (pre-SP/RI) and only reconciles down when a "Total pre-tax" line is found; missing anchor → inflated total unflagged; unmatched charge lines silently skipped. CUR detection hinges on one column (`1646-1649`); a renamed column falls through to flat-CSV/spreadsheet misparse; the spreadsheet branch (`1966`) accepts any Category/Meter/Cost sheet with no provider check.
**Fix:** When the PDF pre-tax anchor is absent, flag the bill as unreconciled (loud WARN + report banner), don't ship a silent gross. Broaden CUR detection to a set of signature columns and, when a bill matches multiple/none, fail to the LLM-normalization path rather than guessing. Add a provider sanity check to the spreadsheet branch.
**Acceptance:** anchor-less PDF is flagged; a CUR with a renamed line-item-type column is recognized or sent to normalization, not misparsed. Tests added.

### ING-4 [MED] Locale/number parsing + multi-account
**Problem:** Python aggregation paths (`ingest.py:2086/2098/2239`) parse with bare `float(... .replace(",",""))` — accounting parens `(150.00)` → ValueError → silently 0.0; EU decimal comma `1.234,56` → `1.23456` (100x wrong). No payer-vs-linked dedup (account id not a dedup key) → payer+linked combined exports can double-count.
**Fix:** Route all numeric parsing through the robust `_sql_num` (548-562) or a shared `parse_money()` that handles parens, symbols, and locale decimal separators; detect decimal-comma locale from the file. Add a guard that detects simultaneous payer-rollup + linked-detail rows and de-duplicates or flags.
**Acceptance:** parens/EU-locale/scientific values parse correctly; a payer+linked fixture doesn't double-count. Tests added.

---

# STAGE 3 — Comparison-basis correctness (the dollars)

> **DECISION (product owner, 2026-11-03):** The headline comparison is **On-Demand vs On-Demand**. AWS On-Demand cost is sourced per line item through the reconstruction cascade in NUM-1 below. Where the bill does not publish On-Demand data, reconstruct it from AWS-published RI/SP discount information; detect and warn when the CUR lacks the needed columns (NUM-6). SCALE items are to follow standard industry practice (bounded worker pool, timeouts, pagination).

### NUM-1 [CRITICAL] Headline must be OD-vs-OD, with a per-line AWS On-Demand reconstruction cascade
**Problem:** `render_report.py:790-792` shows the AWS headline as `SUM(aws_amortized_cost)` (post-SP/RI, discounted) while `:818` shows GCP `SUM(gcp_projected_cost)` at OD list. `accuracy_report.py:155` already uses `aws_od_cost` (OD-vs-OD). The two reports reach **opposite verdicts** on the same bill. The decided policy is OD-vs-OD, but render_report still uses amortized, and `aws_od_cost` is not always reconstructed faithfully when the bill omits On-Demand data.

**Fix — resolve `aws_od_cost` per line item via this exact priority cascade, and tag which tier produced it (`od_basis` column):**
1. **`published_od`** — use `pricing/publicOnDemandCost` from the CUR when present and > 0. This is AWS's own authoritative On-Demand list cost; always prefer it. (Also accept a published OD **rate** × usage when the cost column is absent but the rate column is present: `pricing/publicOnDemandRate` × `lineItem/UsageAmount`.)
2. **`reconstructed_from_published_discount`** — when (1) is unavailable but the line carries AWS-published commitment pricing, derive the **effective discount from the line's own rate fields** and gross the amortized/effective cost back up to On-Demand:
   - For RI/SP-covered rows: `effective_discount = 1 − (effective_rate / public_on_demand_rate)` using the row's own `publicOnDemandRate` vs its reservation/SP effective rate (`reservation/EffectiveCost`-implied rate, or `savingsPlan/SavingsPlanEffectiveCost`-implied rate), then `od = amortized_cost / (1 − effective_discount)`.
   - Prefer this **per-line published ratio** over any global assumption — it reflects the actual commitment the customer holds, which is exactly the "AWS publishes RI/SP % → compute OD" path the owner specified.
3. **`reconstructed_from_assumptions`** — when neither published OD cost nor per-line rates exist, fall back to the configured admin discount divisors (`spot_div`, `ri1_div`, `ri3_div`, `sp_div`; current `ingest.apply_od_basis` behavior): `od = amortized / (1 − assumed_discount)`. These are the editable assumptions (see assumptions subsystem) and are clearly an estimate.
4. **`amortized_fallback`** — last resort when no OD signal and no derivable rate: carry `aws_amortized_cost` as `aws_od_cost` **but stamp a visible `[od-unresolved]` note** on the row and count it in the report's data-quality summary. Never silently present amortized as On-Demand.

Then: make `render_report.py` use this `aws_od_cost` for the headline AWS total; show amortized ("what you pay today, after SP/RI") as a clearly labeled secondary column. Put the dominant `od_basis` tier and the share of spend in each tier in the report header, so the reader knows how much of the AWS baseline is authoritative vs reconstructed vs estimated.

**Acceptance:**
- `render_report` and `accuracy_report` agree in direction and basis on a heavy-3yr-RI fixture.
- A fixture CUR **with** `publicOnDemandCost` → `od_basis='published_od'` and the headline equals the published OD sum.
- A fixture CUR **without** OD cost but **with** `publicOnDemandRate` + reservation effective rate → `od_basis='reconstructed_from_published_discount'`, and the reconstructed OD matches the published-rate gross-up within tolerance.
- A fixture with neither → `reconstructed_from_assumptions`, and editing the SP/RI assumption changes the OD total (ties to NUM/assumptions).
- Report header shows the per-tier spend breakdown.
**Risk:** this changes the headline number customers see — it is the decided OD-vs-OD story; make the basis explicit in the report so it is defensible.

### NUM-2 [CRITICAL] Passthrough rows must use the same basis as their own AWS side
**Problem:** `projection_view.py:381-400` sets passthrough/needs_reprice `gcp_projected_cost = COALESCE(aws_od_cost, reconstructed_od)`, grossing amortized up by `1/(1−discount)` (Spot `/0.30`). A Spot row billed $30 contributes **$100** to the GCP total while contributing $30 to the AWS total beside it.
**Fix:** With the OD-vs-OD headline (NUM-1), the AWS side of a passthrough row must also be its reconstructed `aws_od_cost`, so an OD-passthrough compares against OD-AWS = ratio ≈ 1.0 (honest, since a passthrough IS "same cost on GCP"). A passthrough row must never inflate the GCP total relative to its own AWS basis. Reuse the exact `aws_od_cost` resolved in NUM-1 for both sides.
**Acceptance:** a Spot passthrough row contributes equal amounts to both the AWS and GCP totals (ratio ≈ 1.0, not 3.3x); test added.

### NUM-6 [HIGH] Detect missing On-Demand columns in the uploaded CUR and tell the user how to enable them
**Problem:** The whole OD-vs-OD headline and the NUM-1 reconstruction tiers depend on the CUR containing AWS's On-Demand cost/rate columns. AWS only populates these when the Cost & Usage Report is **configured** to include them; many exported CURs (and all PDF/Cost-Explorer exports) omit `pricing/publicOnDemandCost` / `publicOnDemandRate`, silently forcing the pipeline down to the assumption-based or amortized tiers with no signal to the user. The owner asked for a check "at the AWS site of the CUR report to tell this."
**Fix:** Add an ingest **preflight data-quality check** that inspects the uploaded file's columns (after schema detection, before projection) and classifies OD-data availability into the same tiers as NUM-1:
  - `pricing/publicOnDemandCost` present & populated → **"On-Demand: authoritative (from CUR)."**
  - only rate / reservation / SP columns present → **"On-Demand: reconstructed from AWS commitment rates."**
  - none present → **"On-Demand: estimated from discount assumptions"** AND surface an actionable, non-dismissable banner in the report + an API field:
    > *"Your AWS Cost & Usage Report does not include On-Demand cost columns, so the AWS baseline is estimated. For an accurate AWS↔GCP comparison, recreate/edit the report in the AWS Billing & Cost Management console → **Cost & Usage Reports (Data Exports)** → your report → enable the pricing/public On-Demand columns (and 'Include resource IDs' is not required), then re-export and re-upload. CUR 2.0 / Data Exports: include `pricing/public_on_demand_cost` and `pricing/public_on_demand_rate`."*
  Persist the resolved OD-quality tier per bill (DB column + diagnostics) and show it in the report header next to the NUM-1 per-tier spend breakdown. This makes the data-quality limitation visible to the user and tells them exactly how to fix it at the source.
**Acceptance:** three fixtures (OD-cost present / rates-only / neither) produce the three classifications; the "neither" case renders the banner and sets the API field; the tier is persisted and shown in the header. Test added.

### NUM-3 [HIGH] Unpriced mapped rows drop from GCP total in check-only
**Problem:** `projection_view.py:383` mapped cost = `usage * mult * rate`; NULL `rate` → NULL → excluded from `SUM`. The downgrade that rescues this (`validate_fix.py:1601-1631`) runs only `if not check_only`, and even then masks the unknown as cost-parity.
**Fix:** Treat a NULL-rate mapped row as a loud failure in all modes (including check-only) — either resolve a rate or mark `needs_reprice` with a visible note, never silently drop from the total. Don't let the autofix fake parity; carry the AWS cost as labeled passthrough with a `needs_reprice` flag surfaced in the report.
**Acceptance:** a bill with an unpriceable mapped row shows that spend in the total (flagged), in both normal and check-only runs; test added.

### NUM-4 [HIGH] Credits have no coherent GCP counterpart
**Problem:** `ingest.py:479-481` forces `is_workload=FALSE` for `cost<0`; these net the AWS total down but their GCP projection is incoherent (0 / negative / wrong-sign depending on strategy), reinforcing NUM-1 bias.
**Fix:** Define explicit handling for credit/refund/EDP rows: exclude them symmetrically from both sides, or carry them as an explicit "AWS-only credits" line that the report discloses, so the comparison isn't silently skewed. Document the choice.
**Acceptance:** a bill with EDP credits shows a consistent, disclosed treatment; test added.

### NUM-5 [MED] Over/under gate misses summed break_down components
**Problem:** `validate_fix.py:1818-1824` computes the ratio per gcp_projection row; each core/ram/license component carries the full `aws_amortized_cost` but only its partial GCP cost, so a row whose components sum to >2x can slip the gate.
**Fix:** Compute the gate ratio on the **per-aws_li_key aggregate** of components vs the row's AWS cost, not per component.
**Acceptance:** a synthetic break_down row summing to 2.5x trips the gate; test added.

---

# STAGE 4 — Reference-data freshness

### PRICE-1 [CRITICAL] No bill-period pricing; staleness only warns at run
**Problem:** Every bill is priced against one present-day catalog snapshot (`CATALOG_META.json fetched_at`), with no as-of dimension in rate selection (`projection_view.py:213-269`, `apply_rates.py:650-662`). `check_catalog_age.py --assert-fresh` blocks only in `Dockerfile:71-72`; at job-run `apply_rates._record_catalog_vintage` only WARNs. On **Windows**, `check_catalog_age.py:133-146` "refresh" just rebuilds the index from bundled SKUs and never advances `fetched_at`.
**Fix:** (a) Promote the in-job `catalog_stale` warning to a **blocking gate** (configurable max age) so an old catalog can't silently ship projections; (b) fix the Windows refresh to actually re-fetch and advance `fetched_at`, or fail loudly that it can't; (c) longer-term, add a bill-period vs catalog-date reconciliation and surface the vintage gap in the report header. At minimum, disclose the catalog date and the bill period side-by-side.
**Acceptance:** a stale catalog blocks (or is prominently flagged and gated by config); Windows refresh advances `fetched_at` or errors; report shows catalog date vs bill period. Tests added.

### PRICE-2 [HIGH] Region correctness overridden by description score
**Problem:** `resolve_sku.py:327` `best_match = best_exact_res if best_exact_score >= best_any_score else best_any_res`; because `best_any` also sees the exact candidate, a different-region SKU with higher word-overlap wins → wrong-region price (Delhi row priced at Iowa rate). Also `re.split` at `:314` with no `import re` (swallowed by broad except → silent passthrough).
**Fix:** Prefer `best_exact_res` whenever an in-region/global SKU matched; use `best_any_res` only when none did. Add `import re`. Log when a cross-region SKU is used.
**Acceptance:** a row needing asia-south2 resolves to the asia-south2 SKU even when a us-central1 SKU scores higher on words; test added; no NameError path.

### PRICE-3 [HIGH] region_prefix gaps → silent global fallback
**Problem:** `region_prefix.py:26-60` omits af-south-1, ap-east-1, me-south variants, il-central-1, eu-south, ca-west-1; undecodable prefix → global → whatever scores best (compounds PRICE-2).
**Fix:** Fill the missing prefixes with sensible GCP equivalents; make an undecodable region a **flagged review**, not a silent global fallback.
**Acceptance:** listed regions decode; an unknown region is flagged in the report; test added.

### PRICE-4 [MED] Disclosed modeling biases
**Problem:** No SUD modeling (`projection_view.py:268` OD list → overstates always-on compute); flat $0.08 inter-region egress (`egress-rates.json`) over/understates by continent; GCP minimums (Autopilot pod floor, Cloud SQL, 1-min VM) ignored → understates tiny rows.
**Fix:** These are defensible conservative choices — but **disclose** them in the report methodology section, and replace the flat inter-region egress with a continent-aware table. Decide whether to model SUD as an optional "realistic GCP" column.
**Acceptance:** report discloses the no-SUD / minimums stance; inter-region egress uses a tiered table; decision recorded.

---

# STAGE 5 — LLM hardening

### LLM-1 [CRITICAL] Prompt injection into an unrestricted agent
**Problem:** Bill-derived fields are written raw/undelimited into files the LLM reads (`auto_review.py:345-352` f-strings into review_flags.md; phase2_manifest; triage_suggestions; targeted-retry prompts `orchestrate.go:1308-1431`), and the agent runs `agy --dangerously-skip-permissions` (`orchestrate.go:976-979`) with `run_command` and direct-SQL capability. Output is well-validated (closed catalog allow-list — good), so data-corruption injection is contained, but an injection that induces a **shell/DB action** is not.
**Fix:** (a) Fence all bill-derived text as explicit untrusted data (delimit + a "treat as data, not instructions" wrapper), length-cap it, and strip control/directive-shaped content; (b) remove `--dangerously-skip-permissions` for the LLM phases and grant a **scoped allow-list** of only the tools/dirs each phase needs (it writes JSON mapping files — it does not need arbitrary `run_command`); (c) keep the merge-time allow-list as defense in depth.
**Acceptance:** a bill with an injection payload in `usage_type` cannot cause a tool/shell/DB action; a red-team fixture asserts the mapping output is unchanged and no out-of-scope file is touched.
**Risk:** scoping tools may require adjusting the agy invocation; verify phases still complete.

### LLM-2 [HIGH] No determinism controls
**Problem:** `orchestrate.go:976-979` passes no temperature/top_p/seed/max_tokens; same bill can yield different mappings on re-run; no seed recorded for audit.
**Fix:** Set a fixed low temperature and a recorded seed for the mapping/review phases; persist them with the job for auditability.
**Acceptance:** two runs of the same fixture produce identical mappings (or documented-bounded variance); seed is recorded.

### LLM-3 [MED] No output cap; silent skip on LLM death
**Problem:** Phase-2 maps all rows in one unbounded invocation (`orchestrate.go:190-198`), only bounded by a 45m timeout; non-critical phases (3/5/6) silently skip on quota/auth failure (`orchestrate.go:1687-1689, 1815-1818`) and ship an un-reviewed report without telling the user.
**Fix:** Add a row-batch / max_tokens cap on Phase-2 (dedup identical (canonical,charge_type) rows before sending); when a non-critical phase is skipped, **disclose** it in the report ("projection not LLM-reviewed — mappings are deterministic best-effort").
**Acceptance:** large-bill Phase-2 stays within token bounds; a skipped review phase is disclosed in the report; tests added.

---

# STAGE 6 — Concurrency / operational

### CONC-1 [HIGH] Catalog two-file swap torn read
**Problem:** `build_catalog_index.py:128-136` renames `catalog.duckdb` then `.wal` in separate syscalls against readers that open read-only with no lock; during the weekly refresh a job can pair a new DB with a stale WAL → read error / inconsistent rates. Stale dst `.wal` is never deleted; on Windows an open handle makes `os.replace`/`shutil.move` fail mid-swap.
**Fix:** Implement the versioned-catalog + single pointer swap (the B5 item already tracked): write the new catalog into a versioned dir and atomically repoint a `current` symlink/pointer; readers resolve the pointer once per job. At minimum, delete any stale dst `.wal` before the swap and have readers retry-open on WAL mismatch.
**Acceptance:** a refresh concurrent with N reader jobs never yields a torn/inconsistent read (stress test); no orphan `.wal`.

### CONC-2 [MED] Self-defeating retry, no reaper, lost cache updates
**Problem:** (a) Phase-2 targeted retry tells the LLM to INSERT directly into `aws_li_to_gcp_li`, but the re-run of `merge_mappings.py` (`:451`) DELETEs and rebuilds from JSON, discarding those inserts — coverage is salvaged only by `ensure_full_coverage.py`. (b) Zombie reaping runs only at backend startup (`main.go:131-170`); a watcher-goroutine panic isn't caught by `middleware.Recoverer`, and PID recycling can wedge a job "running". (c) Shared self-healing caches (`refresh_store.py:150`, `promote_learned_codes.py:233`) do read-modify-write with no lock → lost updates under concurrency (degrades future learning only).
**Fix:** (a) Make the retry write to the JSON files merge reads from (or have merge preserve direct inserts); (b) add a periodic heartbeat/timeout sweeper that fails jobs stuck "running" past a max duration, and recover watcher panics; (c) add a file lock around read-modify-write on the shared caches.
**Acceptance:** retry inserts survive the merge; a stuck job is reaped without a server restart; concurrent cache writers don't lose updates. Tests added where feasible.

---

# STAGE 7 — Test-suite hardening (so green means correct)

### TEST-1 [HIGH] Add real dollar regression coverage; de-fang vacuous tests
**Problem:** No regression guard on total projected $ for any realistic bill. `build_pinned_corpus.py` hard-stamps `gcp = aws*0.94` fiction; `test_pricer_correctness.py:36`, `test_static_mappers.py`, `test_family_mapper_compute.py` stub the rate path with priceless `MOCK_SKU`; `verify_golden_mappings.py:99` does `sys.exit(0)` on failure (never blocks). Real end-to-end dollar assertions exist only in `test_assumption_mappings_e2e.py`, covering niche mappers (NAT/DynamoDB/DAX/MemoryDB/Glue) — EC2 family, Cloud SQL, S3, EBS, Fargate, Lambda rate correctness is untested.
**Fix:** (a) Add a golden end-to-end test that drives the core services (EC2/RDS/S3/EBS/Fargate/Lambda) through real `resolve_sku` → committed catalog → asserts expected dollar totals within tolerance, and guards the headline total for a fixed fixture bill; (b) make `verify_golden_mappings.py` fail CI on mismatch; (c) add fixtures with the shapes behind real bugs — PDF empty-`usage_type`, negative/credit rows, consolidated multi-account; (d) add the SEC-2 malicious-header and ING-1 flat-CSV fixtures as regression tests.
**Acceptance:** the new golden test catches a deliberately introduced 2x rate error and a wrong-SKU mapping; `verify_golden_mappings` is wired into pytest and blocks on failure.
**Risk:** golden totals must be computed from trusted reference pricing — document their provenance.

---

# STAGE 8 — Scale / resource governance

> **DECISION (product owner, 2026-11-03): implement standard industry practice for all SCALE items** — a bounded worker pool with a FIFO queue and graceful `503 Retry-After` when saturated (not unbounded goroutines); `context.Context` deadlines on every subprocess/exec/HTTP call that actually kill the whole process tree on timeout; explicit `http.Server` Read/Write/Idle/ReadHeader timeouts; and server-side pagination / top-N rollup for any unbounded render. Concrete per-item guidance below.

**Architectural backbone is sound:** ingest dedups the raw CUR into `aws_li_catalog` via pure-SQL GROUP BY (one row per distinct line-item type), so downstream scripts run on ~10k–100k distinct rows, not the raw millions, using set-based SQL. Standard CSV/parquet/gz ingest uses streaming DuckDB VIEWs and does **not** OOM on a 5M-row bill. The gaps are resource governance and two unbounded artifacts — not the row math.

### SCALE-1 [CRITICAL] No global job-concurrency cap
**Problem:** Every job is a detached process tree via `go h.runJob` (handler.go:314/445/636/794/1145) with **no semaphore/worker pool**. The only throttle is per-user (`checkUserQuota` maxConcurrent=2, handler.go:185-211) and **admins are exempt** (:189). Multiple users or any admin can launch unbounded simultaneous heavy jobs; each tree (DuckDB+agy+Python) uses GB. OOM (exit 137) is already handled (orchestrate.go:1555,1625), i.e. it happens.
**Fix (industry-standard):** Introduce a single process-wide **bounded worker pool** (buffered-channel/semaphore of size `MAX_CONCURRENT_JOBS`, env-configurable, default sized to host RAM ÷ per-job memory budget) that **all** job launches acquire from — admins included. Excess launches enqueue FIFO; if the queue is full, the trigger endpoint returns `503` with `Retry-After` rather than spawning. Track in-flight weight so a big bill can count as more than one slot (optional weighted semaphore). Keep the per-user quota as a separate, additional gate.
**Acceptance:** with `MAX_CONCURRENT_JOBS=2`, launching 5 jobs (incl. as admin) runs 2, queues 3; a saturated queue returns 503; a Go test asserts the cap holds for admins.

### SCALE-2 [CRITICAL] No timeouts anywhere
**Problem:** Python scripts run via bare `subprocess.run` with no `timeout=` (orchestrate.go:928,1507,1593); Go exec uses `cmd.Run()`/`CombinedOutput()` with no `CommandContext` (handler.go:886,1055,1264; watcher.go:524); gate queries unbounded. agy is bounded only by its own soft `--print-timeout 45m` (orchestrate.go:977), and the 60s heartbeat (972-975) keeps the log fresh so the watcher's 45-min stale-kill (watcher.go:38) never fires during an LLM phase. No whole-job wall-clock ceiling.
**Fix (industry-standard):** Use `exec.CommandContext` / `subprocess.run(..., timeout=)` with explicit, env-configurable deadlines at three levels — per-script, per-LLM-phase, and a whole-job wall-clock ceiling — and ensure the kill tears down the **whole process tree** (process group kill, not just the parent), since agy spawns children. On timeout, mark the job failed cleanly with a clear reason, don't hang. Replace the stale-log heuristic (which the 60s heartbeat defeats) with a real max-duration guard. Keep agy's `--print-timeout` but never rely on it as the only bound.
**Acceptance:** a script/LLM call exceeding its budget is killed (process tree gone) and the job fails with a timeout reason; a whole-job ceiling is enforced; tests simulate a hanging child and assert cleanup.

### SCALE-3 [CRITICAL] Unbounded LLM manifest
**Problem:** `classify_mechanics.py:1876-1996` writes `phase2_manifest.json` from ALL non-deterministic catalog rows (no LIMIT/chunk/batch), with dedup only at the catalog GROUP BY — **no further collapse by (canonical, charge_type)**, so near-identical SKUs across regions/months/accounts each become a manifest row, read in one shot. Token cost/size scales linearly with distinct-SKU count and breaks context on large diverse orgs.
**Fix:** Dedup the manifest by (canonical, charge_type) — map one representative, fan the decision back out to all matching rows — and chunk/batch the LLM calls with a cap. (This also directly reduces cost.)
**Acceptance:** a bill with thousands of near-identical SKUs produces a bounded manifest and bounded token use; representative-mapping is fanned out correctly; test added.

### SCALE-4 [HIGH] Unbounded HTML report
**Problem:** `render_report.py:994-1026` fetches all rows (trim defaults to 0.0 = off, :1032), emits one `<tr>` per distinct row (:1525/1614), O(n) string-concat (:1653). A static HTML file can't virtualize → browser hangs ~20k–50k rows. (The in-app React view and admin tables are safe — they paginate.)
**Fix (industry-standard):** Cap the static HTML report to a top-N by cost (default e.g. 1,000 rows) with a "+X more rows ($Y) — see CSV export" rollup line so totals still reconcile; for full detail point to the paginated in-app view or the CSV/XLSX export. Generate via streamed/buffered writes instead of building one giant string. Make N configurable.
**Acceptance:** a 50k-distinct-row bill produces a report that opens responsively and whose displayed total + rollup equals the true total; manual check + a size assertion test.

### SCALE-5 [HIGH] No http.Server timeouts; sync Python endpoints
**Problem:** `main.go:460` sets only Addr+Handler — no Read/Write/ReadHeader/Idle timeouts → slow-loris / slow 512MiB upload holds connections; sync endpoints (`Recalculate` handler.go:1054, `RegenerateReport` :902, `downloadExport` :1264) run Python in-request for minutes with no server cutoff. (The main job trigger is correctly async.)
**Fix (industry-standard):** Set explicit `http.Server` timeouts — `ReadHeaderTimeout` (e.g. 10s), `ReadTimeout` sized for the 512 MiB upload, `WriteTimeout`, `IdleTimeout` — on `main.go:460`. Convert the heavy synchronous endpoints (`Recalculate`, `RegenerateReport`, `downloadExport`) to the same async job model the main trigger uses (return `202` + a job/poll handle) or bound them with a request `context` deadline. Keep streaming uploads behind the worker pool so a slow upload can't pin a job slot.
**Acceptance:** server has explicit timeouts; a slow-loris connection is dropped; the heavy endpoints return promptly (202) or time out cleanly; test added.

### SCALE-6 [MED] Semicolon spreadsheet-export ingest OOM
**Problem:** `ingest.py:2066` `fetchall()` of the full raw table + per-row Python loop (2075-2110), guarded to the semicolon "usage_(with_units)" format (:2064). OOMs on a large file of that format (standard CUR is unaffected).
**Fix:** Convert this branch to the same streaming VIEW + SQL GROUP BY the standard path uses.
**Acceptance:** a large semicolon-format file ingests without full materialization; test added.

---

## Global acceptance gate
1. `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q` — green, with all new regression tests.
2. `go build ./... && go test ./...` — green, with the new security tests.
3. Security: the SEC-1 and SEC-2 exploits return 401/safe-quote, proven by test.
4. A heavy-RI + non-USD + PDF + multi-account fixture runs end-to-end with consistent basis, disclosed vintage, and no silent row loss.
5. Verification table above fully flipped to ✅ DONE with SHAs.
