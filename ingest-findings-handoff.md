# Ingest-phase evaluation — findings handoff

You are picking up work on an AWS→GCP cost-projection pipeline. A prior session audited **Phase 1 (ingestion)** by running the real loader against 6 real bills and inspecting the resulting DuckDB table. Below are the findings with reproducible evidence. Your job: confirm each finding yourself (don't trust this blindly — re-run), then fix them in the priority order given. Do NOT start coding Finding B until the human makes the policy call it requires.

## Project location & how ingest works
- Repo: `C:\Users\arnav\Downloads\cur-web-AGY`
- Ingest script: `skill/aws-gcp-cost-projection/scripts/ingest.py` (~1860 lines, deterministic, no LLM)
- It loads a bill into DuckDB table `aws_li_catalog` (one row per unique line-item type), with 5 parser paths behind a format detector: raw CUR (slash or underscore/parquet columns), Category/Meter/Cost spreadsheet, `usage_(with_units)` semicolon CSV, flat 6-col Cost-Explorer CSV, and PDF.
- Run it manually like this (PowerShell/bash): make a job dir containing `input.<ext>`, create `projection-audit/`, set `SKILL_DIR` to the skill dir, `cd` into the job dir, then `python <skill>/scripts/ingest.py`. Inspect `projection-audit/projection.duckdb` → table `aws_li_catalog`. Env has Python 3.14 + duckdb 1.5.5; `pdftotext` is available.

## Test corpus used (real bills on this machine)
- `C:\Users\arnav\Downloads\easebuzz-cur-report.csv` — 303MB raw CUR, underscore columns
- `C:\Users\arnav\Downloads\Facets\aws_cur_style_report_enriched.csv` — raw CUR, slash columns
- `C:\Users\arnav\Downloads\Facets\test-bill (1).csv` — flat 6-col CSV
- `C:\Users\arnav\Downloads\Facets\New bills\June_prod_filtered.csv` — Category/Meter/Cost spreadsheet
- `C:\Users\arnav\Downloads\Facets\usage-june-2026.csv` — Azure usage export (should be rejected)
- `C:\Users\arnav\Downloads\Facets\BuyHatke AWS Bill.pdf` — PDF bill

## Results table (what the prior run produced)
| Bill | Format | Rows | Total | Workload base | instance_type populated |
|---|---|---|---|---|---|
| test-bill | flat_csv | 82 | $19,359 | $28,211 (!) | 54/82 |
| enriched CUR | raw_cur | 549 | $419,119 | $416,380 | 0 |
| June_prod spreadsheet | flat_csv | 121 | $10,042 | $9,513 | 0 (expected) |
| usage-june (Azure) | — | 0 | rejected | — | — |
| BuyHatke PDF | flat_csv | 47 | $12,397 | $14,859 (!) | 17/47 |
| easebuzz CUR | raw_cur | 2,589 | $235,929 | $186,250 | 0 |

---

## FINDING A (🔴 fix first — mechanical, no policy needed): raw-CUR instance-spec enrichment never fires
**Symptom:** both raw CURs produced **0 rows** with instance specs, despite EC2+RDS dominating spend (easebuzz: $53K EC2 + $71K RDS). Flat CSV / PDF populate specs fine.

**Root cause:** the instance-type extraction regex at `ingest.py:~1704-1709` searches only the `operation` field. In real CUR, `operation = "RunInstances"` and the instance type lives in `usage_type` (e.g. `EU-WEST-1-BoxUsage:g4ad.8xlarge`) and in the authoritative `product_instance_type` / `product_vcpu` / `product_memory` CUR columns — none of which ingest consults. Verified: 0 rows match the type pattern in `operation`; 509 rows carry `BoxUsage` in `usage_type`.

**Consequence:** `instance_type, instance_vcpus, instance_ram_gb, workload_class, instance_count, aws_effective_unit_rate` are all NULL for every compute row on a CUR. `family_mapper.py` has a fallback (`_resolve_instance_type` / `_backfill_specs`, whose comment literally says "this ingest simply never populated them") that silently re-derives type from usage_type — so it's not fatal, but it's duplicated logic that can drift, and the authoritative `product_instance_type` columns are wasted.

**Fix:** in ingest's enrichment, (1) also search `usage_type` for the instance-type pattern, and (2) prefer the `product_instance_type`/`product_vcpu`/`product_memory` columns when present (load them into `aws_li_catalog` from the raw-CUR SQL first). Then re-run easebuzz + enriched and show instance-spec coverage go from 0 → near-complete.

## FINDING B (🔴 highest impact — POLICY DECIDED, implement to this spec): comparison basis must be GROSS, format-independent, with discounts shown separately
**Symptom:** the AWS comparison basis is inconsistent across formats. flat_csv/PDF use GROSS on-demand usage (test-bill workload $28,211 vs net bill $19,359; BuyHatke $14,859 vs $12,397). Raw CUR uses net-ish (`DiscountedUsage`, easebuzz $186K vs $236K). Same economics → different basis purely by upload format. Root cause: `is_workload` + cost logic differs per parser — CUR path `ingest.py:~772`, spreadsheet `~851`, usage-units `~1003`.

**DECISION MADE BY THE HUMAN (implement exactly this — do not re-litigate):**
- **Comparison basis = GROSS vs GROSS.** The AWS side must be full on-demand / list cost (pre Savings-Plan/RI/EDP discount), compared against GCP On-Demand (and GCP CUD columns as already produced). This is "Option 2".
- **Discounts shown SEPARATELY.** Savings Plans / RI / EDP / credits must NOT be netted into the workload cost. They are surfaced as their own explicit line(s)/section so the report can show: AWS gross, the discounts, and GCP — side by side. Do not fold them back into workload spend.
- **Format-independent.** All parser paths (raw CUR, flat CSV, PDF, spreadsheet, usage-units) must produce the SAME basis: gross workload usage priced at on-demand/list, with discounts as separate non-workload lines. Today raw CUR is the odd one out (it uses discounted `DiscountedUsage` as the workload basis) — make CUR match the gross behavior the flat/PDF paths already have, and keep discounts/SP fees as separate rows.

**What to implement:** unify the three `is_workload`/cost code paths so the workload basis is always gross on-demand and SP/RI/EDP/credits are always separated out as their own rows (never merged into workload cost), regardless of format. Keep usage *quantity* intact for GCP sizing in every path.

## FINDING C (🟠): hard-fail path writes no failure.txt
The Azure file was correctly rejected with a clear message and exit 1, but **no `failure.txt`** was written to the job dir. The Go watcher's `readFailureReason` depends on that file; without it the job can die with no clean user-facing reason. Also `_reject_non_aws` (`ingest.py:~101`) didn't catch this Azure *usage* export (its column names don't match the hardcoded Azure signatures) — it fell through to the generic failure. Minor: a mojibake `—` char in that generic message (encoding). Fix: ensure every hard-fail writes failure.txt; broaden Azure detection.

## FINDING D (🟡): the `usage_(with_units)` parser fabricates data
Not triggered by the 6 test bills, but live at `ingest.py:~913-1165`: it splits one blended cost across usage units using hardcoded guess-weights (EC2 hr=8.5, gb=1.5, etc.) and stamps EC2 hours as a literal `t3.medium` (`~L1072`). Any bill of that shape gets confident-looking fabricated specs. Fix: gate behind low confidence / passthrough, or remove.

## FINDING E (🟢): `canonical_service` column never populated (Layer-1 integrity)
The `aws_li_catalog.canonical_service` column was ABSENT on all 6 runs. Ingest uses the `canonical_service()` *function* only in the PDF path; the persisted column that the "Layer 1" design (and the deferred "classify_mechanics consumes canonical_service" item) assumes exists is never written (`aws_normalizer.py`'s `main()` that would stamp it is not wired into any phase). Decide whether to wire it in or drop the column from the design.

---
---
# SECOND-PASS FINDINGS (deeper audit: column utilization, formats, scenarios)

Confirmed first: **data IS extracted and persisted** — DuckDB table `aws_li_catalog` in `projection-audit/projection.duckdb`, one row per unique line item. Storage is fine. (Also: the earlier worry about `total_usage` being sparse on CUR was a FALSE ALARM — all workload rows have usage; the empty ones are non-workload SP/tax/support rows with no quantity. $0 of workload spend lacks usage.)

## FINDING F (🔴): Excel (.xlsx) AWS bills silently fail to load
**Evidence:** `New bills\June.xlsx` is a legitimate bill (Category/Meter/Cost columns, single sheet "Export") — openpyxl reads its header fine (`Cloud Account, Category, Subcategory, Meter, Consumed, Cost, ...`). But ingest saw only one column `OGC_FID` and died with "Unrecognized bill format."
**Root cause:** `_load_excel` (`ingest.py:~231`) tries DuckDB's excel extension, then falls back to the **spatial `st_read`** reader. For this workbook the excel read fails, and `st_read` (a GEO reader) "succeeds" returning a junk single-column `OGC_FID` table — which `_load_excel` accepts as success and returns, so the correct **openpyxl fallback below it never runs**. Result: a real Excel bill is lost.
**Fix:** drop `st_read` for spreadsheets (it is for geospatial files, not billing xlsx), OR validate the returned columns and reject the `OGC_FID`/empty result so control falls through to the openpyxl path. xlsx is a common delivery format — this matters.

## FINDING G (🔴): ingest ignores authoritative CUR columns and re-derives worse data from strings
**Evidence:** easebuzz CUR header literally contains `pricing_public_on_demand_cost`, `pricing_public_on_demand_rate`, `product_instance_type`, `product_instance_family`, `reservation_effective_cost`, `savings_plan_savings_plan_effective_cost` — ingest reads **none** of them. It uses `line_item_unblended_cost` (net) and regex-derives instance type from strings.
**Why it matters (ties to A + B):**
- `pricing_public_on_demand_cost` IS the GROSS cost column the Option-2 (gross-vs-gross) policy needs — Finding B should source the AWS basis from here instead of reconstructing it.
- `product_instance_type` / `product_instance_family` are the authoritative specs Finding A should prefer (before any regex).
- `reservation_effective_cost` / `savings_plan_*_effective_cost` are exactly the discount figures Finding B wants to "show separately."
**Fix:** load these columns into `aws_li_catalog` and prefer them over string re-derivation. This is the core "are all columns used?" answer: no — the richest CUR columns are discarded.

## FINDING H (🟠): managed-DB `database_engine` is almost never extracted
**Evidence:** easebuzz RDS ≈ $72K (top spend), yet `database_engine` populated on 0.5% of rows (enriched CUR: 0%). It's inferred only from operation/usage_type keywords; the `product_database_engine` CUR column is ignored.
**Consequence:** Cloud SQL engine choice (Postgres/MySQL/SQL Server) becomes guesswork for the biggest DB spend. Fix: read `product_database_engine`/`product_operating_system` columns when present.

## FINDING I (🟠): 4-column flat CSV silently buckets unknown services to "Other"
**Evidence/root cause:** the 4-col flat path (no `Service` column) reconstructs product identity from the Description via a ~40-entry hardcoded `if/elif` keyword chain (`ingest.py:~1283-1434`); anything unmatched falls to `product="Other", usage_type="Other"` (`~L1433`) with no error. The LLM fallback is **file-level** (only fires when the whole file produces zero rows), NOT row-level — so unknown services on this format silently become misc→passthrough.
**Scope note:** the 6-column flat format (HAS a `Service` column) is FINE — I tested a synthetic bill with brand-new services (Amazon Bedrock, AWS App Runner, Amazon Q) and all three kept their correct product names, because that path uses the `Service` column directly. The silent-"Other" risk is specific to the 4-col Description-only format.
**Fix:** when a 4-col row falls to "Other", flag it (count + spend) and/or route it to the row-level LLM layer rather than silently passing it through.

## FINDING J (🟡): the keyword classifier is brittle to new formats/services
The same `if/elif` chain is order-sensitive and substring-based, with documented past near-misses (`s3`⊂`APS3`, `ses`⊂`licenses`, EMR-must-precede-EC2). Any new CUR layout or newly-named AWS service that isn't in the chain lands in "Other" (see I). Consider a data-driven alias table (like `aws-aliases.json`) instead of inline Python branches.

## FINDING K (🟡): `aws_region` column under-filled on underscore/parquet CUR
**Evidence:** easebuzz `aws_region` populated 14.7%, but `gcp_region` 100% (recovered via `usage_type` prefix decode). Root cause: `product_region_code` (underscore CUR) isn't mapped into the `aws_region` SELECT. Net effect is mostly masked, but 17% of rows still end up `gcp_region='global'` → risks a wrong-region SKU/rate. Fix: map `product_region_code`→`aws_region`.

## FINDING L (🟡 — VERIFY): unzipped CUR *directory* format may not be handled
`ingest.main()` globs `input.*` (a single file). A real AWS CUR export is often a directory (`data/` parquet part-files + `metadata/` manifest) — seen on disk at `Facets\aws_bill\{data,metadata}`. Via the web app the upload is always a single file/zip, so this is lower priority, but confirm the zip-of-CUR and multi-part-parquet scenarios actually work end to end.

## Note on FINDING C (refined): which failure paths write failure.txt
Verified: the explicit non-AWS rejections (`_reject_non_aws`→`_fail_direct`) DO write `failure.txt` (VMware test wrote it correctly). The GENERIC "unrecognized format" `raise SystemExit(...)` at `~L1200` and the Azure-usage file do NOT. So the gap is specifically the generic unrecognized-format and Excel-fail paths — make those write `failure.txt` too.

## Your task
1. Reproduce each finding with your own ingest run (at minimum: easebuzz + test-bill + the Azure reject).
2. Fix A, B (to the decided spec: gross-vs-gross, discounts separate, format-independent), C, D, E.
3. After A, re-run easebuzz + enriched and report the new instance-spec coverage.
4. After B, re-run test-bill + BuyHatke + easebuzz and show the workload basis is now consistently gross with discounts as separate lines across all formats.
5. Run `python -m pytest tests/ -v` from `skill/aws-gcp-cost-projection/` — existing tests must still pass.
