---
name: aws-gcp-cost-projection
description: "Project an AWS bill to GCP cost. Loads a CUR or flat AWS Cost Explorer Detail Report into DuckDB, maps each unique line item to its GCP equivalent, and produces a per-row cost comparison report. Use when user says CUR, AWS-to-GCP cost, GCP migration projection, AWS bill projection, GCP cost estimate, AWS CUR analysis, projection audit, AWS to GCP TCO, AWS to GCP migration cost."
---

# AWS CUR → GCP Cost Projection

Project the cost of an AWS workload running on GCP, line item by line
item. Output: per-LI cost-comparison report (MD + HTML) with three GCP
totals — On-Demand, 1-year CUD, 3-year CUD.

This skill is a guide, not a recipe. You run the queries, fetch the
data, decide the mappings, and render the report.

**Scope: project the entire dataset provided.** Don't ask the user to
pick a time range, month, or service subset. Whatever rows are in the
input file, all of them are in scope — the report covers the full
period the file represents. Only filter if the user explicitly asks.

## Pipeline (six phases)

```
                ┌─────────────────────────────────────────────────┐
input bill ────►│ Phase 1 — Ingestion             [sub-agent]     │
                │   load → dedup → classify is_workload           │
                │   → reconcile sum                               │
                └────────────────────┬────────────────────────────┘
                                     │ aws_li_catalog
                ┌────────────────────▼────────────────────────────┐
                │ Phase 2 — Mapping             [4–5 parallel]    │
                │   compute / managed-db / networking /           │
                │   storage-analytics / misc                      │
                │   → aws_li_to_gcp_li + mapping-notes.md         │
                └────────────────────┬────────────────────────────┘
                                     │
                ┌────────────────────▼────────────────────────────┐
                │ Phase 3 — Review            [fresh sub-agent]   │
                │   challenge picks, resolve open notes,          │
                │   write corrections                             │
                └────────────────────┬────────────────────────────┘
                                     │ corrected mappings
                ┌────────────────────▼────────────────────────────┐
                │ Phase 4 — Rate-card fill        [main agent]    │
                │   lazy-load gcp_sku_rates for matched sku_ids   │
                │   only; alias / synthesize CUD rates            │
                └────────────────────┬────────────────────────────┘
                                     │ gcp_sku_rates
                ┌────────────────────▼────────────────────────────┐
                │ Phase 5 — Outlier triage         [sub-agent]    │
                │   build gcp_projection; A1/A2/B/C/D/E/F →       │
                │   fix or accept-with-cited-mechanism            │
                └────────────────────┬────────────────────────────┘
                                     │
                ┌────────────────────▼────────────────────────────┐
                │ Phase 6 — Report                [main agent]    │
                │   MD + HTML, layout per phase 6 spec            │
                └─────────────────────────────────────────────────┘
```

| Phase | Run by | Reads | Writes | Detail |
|---|---|---|---|---|
| 1 — Ingestion | one sub-agent | input bill | `aws_raw`, `aws_li_catalog` | [phases/01-ingestion.md](phases/01-ingestion.md) |
| 2 — Mapping | 4–5 parallel sub-agents | catalog slice, `data/skus/`, `find-sku.sh` | `aws_li_to_gcp_li`, `mapping-notes.md` | [phases/02-mapping.md](phases/02-mapping.md) |
| 3 — Review | one fresh sub-agent | `mapping-notes.md`, `aws_li_to_gcp_li` | corrections to `aws_li_to_gcp_li` | [phases/03-review.md](phases/03-review.md) |
| 4 — Rate-card fill | main agent | `aws_li_to_gcp_li`, `data/skus/` | `gcp_sku_rates` | [phases/04-rate-fill.md](phases/04-rate-fill.md) |
| 5 — Outlier triage | one sub-agent (split to ≤4 only if >50 rows) | `gcp_projection` view | corrections to `aws_li_to_gcp_li` | [phases/05-outlier-triage.md](phases/05-outlier-triage.md) |
| 6 — Report | main agent | `aws_li_catalog`, `gcp_projection` | `report.md`, `report.html` | [phases/06-report.md](phases/06-report.md) |

The DuckDB schemas (`aws_raw`, `aws_li_catalog`, `aws_li_to_gcp_li`,
`gcp_sku_rates`) are referenced from every phase. Definitions in
[reference/schemas.md](reference/schemas.md).

## Setup (do this once at the start)

1. **Run preflight.**

   ```bash
   bash "$SKILL_DIR/preflight.sh" "<bill-path>"
   ```

   Verifies `duckdb`, `jq`, `gzip` are on PATH, the bundled catalog
   under `data/` is intact, the input bill exists. Output is JSON.
   - `verdict: PASS` → continue.
   - `verdict: FAIL` → surface the failed checks to the user and stop.
     Don't try to work around tool-missing failures.
   - `catalog_age` is `warn`-only (>90 days). When you see it, surface
     a one-line note to the user:

     > "GCP rate card was last refreshed on YYYY-MM-DD (~N days ago).
     > GCP list prices change rarely — usually only at quarterly
     > product events — so the projection is still directionally
     > accurate. Ask the skill maintainer to run
     > `scripts/refresh-catalog.sh` if you need fresh rates before
     > signing off."

     Then proceed. **Do not run `refresh-catalog.sh` from inside the
     skill** — refresh is a maintainer action, not a per-run action.

2. Create `projection-audit/projection.duckdb`. The four tables in
   [reference/schemas.md](reference/schemas.md) live there. Initialize
   them empty; each phase populates its own.

## What you never do

- Render the report while any outlier query has unreviewed results.
- Ask the user for a date range, month, or service filter. The
  projection covers the whole input as-is unless they explicitly
  scope it down.
- Web-search for GCP rates. The rate card lives in `gcp_sku_rates`,
  loaded from the bundled `data/` catalog.
- Run `scripts/refresh-catalog.sh` from inside the skill. If the
  catalog is stale, note it to the user and proceed — refresh is a
  maintainer task.
- Bulk-load the entire SKU catalog into `gcp_sku_rates`. Populate it
  lazily — only for `gcp_sku_id` values that ended up in
  `aws_li_to_gcp_li`. Use `scripts/find-sku.sh` for discovery, then
  read the specific service's `data/skus/<id>.json.gz` for the rows
  you need.
- Sum `aws_amortized_cost` from `gcp_projection` for the report total
  (it double-counts `break_down` mappings). Always sum from
  `aws_li_catalog`.
- Mutate AWS data or write back to GCP. Read-only audit.
