# Phase 6 — Report

**Run by:** main agent. The narrative quality and customer-facing
language matter — this phase stays with the agent that has the
holistic view.
**Reads:** `aws_li_catalog`, `gcp_projection`, `aws_li_to_gcp_li`,
`run_results` (own history).
**Writes:**
- `projection-audit/report-<run_id>.md`
- `projection-audit/report-<run_id>.html`
- `projection-audit/summary-<run_id>.md` (new scannable narrative)
- one new row in `run_results` inside `projection-audit/projection.duckdb`

Reports are customer-shareable. **Do not invent new sections in the
main report, do not add narrative commentary inline, do not add an
appendix.** The Description column carries per-row rationale; bulk
narrative belongs in `summary-<run_id>.md`, not the main report
table.

## Run identity — compute this first

Every Phase 6 invocation is a single "run" — initial render, or a
refinement re-render after the user pushed back on something. Each
run gets a fresh `run_id` and writes a fresh set of versioned
artifacts; **nothing on disk is overwritten**. Older renders stay
around as history.

Compute the run identifier once at the top of the phase:

```bash
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
```

Compact ISO-8601 in UTC, no separators — filesystem-safe and
lexicographically sortable so a plain `ls projection-audit/` orders
runs chronologically. Reuse the same `RUN_ID` for all four
artifacts (HTML, MD, summary, and the `run_results` row).

### Versioned artifact filenames

| Artifact | Path |
|---|---|
| Customer report (HTML) | `projection-audit/report-<run_id>.html` |
| Customer report (MD)   | `projection-audit/report-<run_id>.md` |
| Narrative summary (MD) | `projection-audit/summary-<run_id>.md` |

Do **not** write `report.md` or `report.html` without the suffix. Do
**not** overwrite a prior run's files — if a refinement supersedes
an earlier render, the earlier render still stays on disk. The
caller picks the latest by sorting filenames or by querying
`run_results` ORDER BY `ts_utc` DESC.

### Detect initial vs refinement

Open `projection-audit/projection.duckdb` and check whether
`run_results` already has rows:

```sql
SELECT COUNT(*) FROM run_results;
```

- `0` rows → this is the **initial** run for this projection.
  `run_type = 'initial'`, `instruction = NULL`.
- `>= 1` row → this is a **refinement** run. The agent received the
  refinement instruction from the user in its current prompt
  (e.g. "switch all c5a to T2D for ARM affinity",
  "recompute with Premium Tier egress", "drop Mumbai region rows
  and re-total"). Capture that instruction verbatim, or a faithful
  paraphrase capped at 500 characters, and store it in
  `instruction`. `run_type = 'refinement'`.

If `run_results` doesn't exist yet, create it (see schema below).
The `CREATE TABLE IF NOT EXISTS` is idempotent and safe to run
every time.

## `run_results` — history table

This table is the durable record of every Phase 6 render — one row
per run. Phases 1–5 do not write to it; only Phase 6 inserts. Phase
3 and Phase 5 corrections that change totals are observed
automatically because Phase 6's `SUM(...)` reads the current state of
`aws_li_catalog` and `gcp_projection` after their writes have
landed.

### Schema (create idempotently at the top of Phase 6)

```sql
CREATE TABLE IF NOT EXISTS run_results (
  run_id        TEXT PRIMARY KEY,
  ts_utc        TIMESTAMP,
  run_type      TEXT,                    -- 'initial' | 'refinement'
  instruction   TEXT,                    -- for refinements: the user's instruction. NULL for initial.
  aws_total     DOUBLE,                  -- SUM(aws_amortized_cost) FROM aws_li_catalog (ALL rows — matches the bill grand total post-discount)
  gcp_od        DOUBLE,                  -- SUM(gcp_projected_cost) FROM gcp_projection WHERE is_workload
  gcp_1yr_cud   DOUBLE,
  gcp_3yr_cud   DOUBLE,
  report_html   TEXT,                    -- relative path, e.g. 'projection-audit/report-20260511T124200Z.html'
  report_md     TEXT,
  summary_md    TEXT,                    -- new artifact you're producing this run
  mapped_rows   INTEGER,                 -- COUNT(*) FROM aws_li_to_gcp_li
  passthroughs  INTEGER,                 -- COUNT(*) WHERE strategy='passthrough'
  confidence    TEXT                     -- worst-of: 'provisional' < 'low' < 'medium' < 'high'
);
```

### INSERT pattern (after the three artifact files are written)

**Always use the named-column `INSERT (...) SELECT ... AS alias` form.**
Do NOT use positional `INSERT INTO run_results VALUES (...)`. The
named form binds each computed value to its semantic column by name,
so it's structurally impossible to swap `gcp_od` ↔ `gcp_3yr_cud` (or
any other pair) even if you later reorder the SELECT clause to match
how the report displays the four numbers.

```sql
INSERT INTO run_results
  (run_id, ts_utc, run_type, instruction,
   aws_total, gcp_od, gcp_1yr_cud, gcp_3yr_cud,
   report_html, report_md, summary_md,
   mapped_rows, passthroughs, confidence)
SELECT
  '<run_id>'                                                              AS run_id,
  TIMESTAMP '<run_id_as_iso>'                                             AS ts_utc,
  '<initial|refinement>'                                                  AS run_type,
  <NULL or 'the refinement instruction text'>                             AS instruction,
  (SELECT SUM(aws_amortized_cost) FROM aws_li_catalog)                    AS aws_total,
  (SELECT SUM(gcp_projected_cost) FROM gcp_projection WHERE is_workload)  AS gcp_od,
  (SELECT SUM(gcp_cost_1yr_cud)   FROM gcp_projection WHERE is_workload)  AS gcp_1yr_cud,
  (SELECT SUM(gcp_cost_3yr_cud)   FROM gcp_projection WHERE is_workload)  AS gcp_3yr_cud,
  'projection-audit/report-<run_id>.html'                                 AS report_html,
  'projection-audit/report-<run_id>.md'                                   AS report_md,
  'projection-audit/summary-<run_id>.md'                                  AS summary_md,
  (SELECT COUNT(*) FROM aws_li_to_gcp_li)                                 AS mapped_rows,
  (SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE strategy = 'passthrough')  AS passthroughs,
  '<confidence>'                                                          AS confidence
;
```

**Schema column meaning is semantic and fixed.** `gcp_od` is always
`SUM(gcp_projected_cost)` — the GCP On-Demand math — regardless of
which row the report Cost Summary table features as the headline
("★ primary"). `gcp_1yr_cud` is always the 1-year Committed-Use
projection; `gcp_3yr_cud` is always the 3-year. Refinement
instructions that ask to "use 3yr CUD as primary" or similar are
**Phase 6 report-layout changes only** — they re-order the rows /
re-highlight the table / change the ★ badge / adjust the diff
percentages. They never permute the meaning of `run_results`
schema columns. Seen in the wild (Rooter PDF Test 2 refinement1,
2026-05-18): an agent took *"3yr CUD as primary"* to mean *"put the
3yr value into `gcp_od` because it's the first GCP column slot"* and
emitted a row with OD and 3yr values swapped. The named-column
INSERT pattern above prevents that physically.

The `aws_total` / `gcp_od` / `gcp_1yr_cud` / `gcp_3yr_cud` columns
must match the four numbers shown in the report's Cost Summary
table exactly — that's the whole point of the persisted history.

**Asymmetry on the `is_workload` filter — intentional.** `aws_total`
sums every row (including the negative EDP / PRC / SP-coverage /
credit / refund rows) so it matches the bill's grand-total
post-discount net the customer's CFO sees. The GCP totals filter
to `is_workload=TRUE` because non-workload rows are AWS-side
commercial mechanisms with no GCP analog — the projection view
already maps them to `strategy='ignore'` and zeroes them, but the
filter is defensive against any misclassification that would
otherwise push a negative AWS amortized cost through a `passthrough`
into the GCP sum.

`confidence` is **worst-of** across the run, ordered
`provisional` < `low` < `medium` < `high`. If a single mapping row
in `aws_li_to_gcp_li` is `low`, the whole run is `low`. If anything
is still `provisional` (e.g. a rate fell through to a placeholder)
the run is `provisional`. This is intentionally pessimistic — one
shaky row means the customer should re-check before signing off.

## Layout (in order)

1. **Title block.**
   - `# AWS to GCP Cloud Cost Analysis` — large, brand-blue (#1A73E8),
     with a horizontal blue rule under it.
   - One-line subhead:
     `**Customer:** <name>  **Analysis Date:** <Month YYYY>  **Line Items:** <N> services analyzed`
2. **Cost Summary** — 4-row table.
3. **Cost Comparison by Service** — one row per `aws_li_catalog` row
   (every row, including ignores and SP offsets), plus a TOTAL row at
   the bottom.
4. **Diff legend** — single italic line under the table:
   *"Diff = AWS − GCP On-Demand. Negative values (green) indicate GCP
   is lower cost."*
5. **Methodology** — short bulleted list (compute family choices,
   region mapping policy, CUD discount tiers, egress tier). No more.

## Cost Summary — exact rows (no extras)

|  |  |
|---|---:|
| AWS Total | $X,XXX |
| GCP On-Demand | $X,XXX |
| GCP with 1-Year Commitment | $X,XXX |
| GCP with 3-Year Commitment | $X,XXX |

`AWS Total` is `SUM(aws_amortized_cost) FROM aws_li_catalog`
(post-tax, post-classification — **never** from `gcp_projection`,
because `break_down` mappings double-count there).

**HTML output:** Immediately after the Cost Summary table (or
anywhere in the `<body>`), include a hidden machine-readable marker
with the raw AWS total — no `$`, no commas, two decimals max:

```html
<div id="aws-total-spend" hidden>48320.50</div>
```

Downstream automation greps this element to extract the headline
spend number. Render it once. The visible table is unchanged.

## Cost Comparison by Service — column spec

| # | Service (AWS → GCP) | Description | AWS | GCP OD | GCP CUD | GCP 3yr | Diff |
|---|---|---|---:|---:|---:|---:|---:|

- **#** — 1-indexed. Sort the body **by `aws_amortized_cost`
  descending** (positive rows largest-first), then **negative
  SP-offset / discount rows at the bottom** sorted by absolute value
  descending. The TOTAL row is always last and not numbered.
- **Service (AWS → GCP)** — *category pill* + service text.
  - Pill values (one per row, exact spelling — render as a colored
    badge in HTML, plain `[Compute]` prefix in MD): `Compute`,
    `Storage`, `Database`, `Network`, `Messaging`, `Monitoring`,
    `Container`, `Other`. Map from the AWS product family —
    Compute = EC2 / Lambda / Fargate; Storage = S3 / EBS / EFS /
    Snapshots; Database = RDS / DynamoDB / ElastiCache / DocumentDB;
    Network = ELB / Route 53 / DataTransfer / VPC / NAT;
    Messaging = SQS / SNS / Kinesis / MSK / EventBridge;
    Monitoring = CloudWatch / X-Ray; Container = ECR / EKS;
    Other = Savings Plan offsets, KMS, Secrets, GuardDuty, SCC,
    Shield/WAF (where appropriate), and anything else.
  - Service text: AWS source description (verbatim or lightly edited)
    + ` → ` + chosen GCP target. Example:
    "On Demand Linux c5a.4xlarge Instance Hour → Compute Engine N2D".
    For ignored rows: "→ N/A - <reason>" (e.g.
    "N/A - Savings Plans Payment", "N/A - SP Offset",
    "N/A - Zero Cost Data Transfer").
  - **Passthrough rows** (`strategy = 'passthrough'`) get a different
    treatment:
    - **AWS side:** use the bill's specific `usage_type` (not the
      broad `product`) so the reader sees what sub-feature is being
      carried — e.g. *"S3 Glacier Transition (Mumbai)"*, not just
      *"Simple Storage Service"*. Append the region in parens when
      the `usage_type` doesn't already encode it.
    - **GCP target:** the literal word `passthrough` — not the
      broad GCP service name. The row carries AWS cost forward 1:1;
      naming a GCP service implies a mapping we don't have.
    - Combined example:
      `S3 Glacier Transition (Mumbai) → passthrough`.
- **Description** — math + rationale, **3–6 lines** of compact text.
  Always include:
  - Spec resolution: `c5a.4xlarge (16 vCPU, 32 GB AMD)`
  - Hours/qty arithmetic: `2232 hrs = 3 instances × 744h`
  - Effective rate: `Rate = 16×$0.033929 + 32×$0.004547`
  - 1yr / 3yr CUD math when applicable
  - Cross-region or HA-tier note when applicable

  Don't truncate; the cell wraps. Don't abbreviate to a single
  sentence — the customer reads this column to validate the
  projection.

  **Passthrough rows** are the exception: render the
  `aws_li_to_gcp_li.projection_note` **verbatim** as the entire
  Description. Phase 3's passthrough audit rewrote those notes to
  be specific and definitive ("S3 Glacier transition requests
  Mumbai; ratio would be <0.33 vs Class A ops — passthrough"); the
  reader sees that reasoning, not a generic template. **Never
  emit the templated string** *"No direct GCP SKU mapping available;
  carrying AWS cost forward"* — if you see it on a row, Phase 3 left
  a hole; fall back to the verbatim `projection_note` anyway and
  flag the row to the user.

  ### Worked example — passthrough row before / after

  Wrong (what the report used to emit):

  ```
  [Storage] Simple Storage Service → Cloud Storage |
  No direct GCP SKU mapping available; carrying AWS cost forward |
  $109.22
  ```

  Right (what Phase 6 now emits):

  ```
  [Storage] S3 Glacier Transition (Mumbai) → passthrough |
  S3 Glacier transition requests Mumbai; ratio would be <0.33 vs
  Class A ops — passthrough |
  $109.22
  ```
- **AWS** — `aws_amortized_cost` rounded to 2dp, `$X,XXX.XX`.
  SP-offset rows render as negative.
- **GCP OD / GCP CUD / GCP 3yr** — `gcp_projected_cost`,
  `gcp_cost_1yr_cud`, `gcp_cost_3yr_cud` rounded to 2dp.
  Right-aligned.
- **Diff** — `aws_amortized_cost - gcp_projected_cost` rounded to
  2dp, rendered with explicit sign:
  - Negative → `-$X.XX` in **green** (#0F9D58) — GCP is cheaper.
  - Positive → `+$X.XX` in **red** (#D93025) — GCP is more
    expensive.
  - Zero or near-zero → `-$0.00` in default text.

## TOTAL row

Last row of the table. No `#`. Label "TOTAL" in the Service column,
empty Description. Sums of AWS / GCP OD / GCP CUD / GCP 3yr / Diff
columns. The Diff total is colored using the same rule.

## Methodology — keep it short

Four bullets, no more:

- **Compute:** AWS EC2 t4g/t3 → GCP N2D or E2 instances (or whichever
  families you actually used)
- **Region:** Per-service region mapping applied based on source data
- **CUD Discounts:** 1-year and 3-year committed use discounts applied
  where available
- **Network:** GCP Standard Tier (or Premium Tier — say which) egress
  pricing used

That's the whole report. No "Key findings", no "Caveats", no
"Recommended next steps". Those belong in `summary-<run_id>.md`, the
companion narrative — see below.

## `summary-<run_id>.md` — scannable narrative

A short, customer-facing companion to the main report. **Target
30–60 lines of markdown.** This is where the agent does its thinking
out loud — the qualitative read that doesn't fit in a row-by-row
table.

### Required sections (in this exact order)

```markdown
# Projection summary — <prospect name or "this run">

**Bottom line:** <one sentence about the overall verdict, e.g. "GCP 3-year CUD
is ~13% cheaper than the customer's current AWS spend">

## Where GCP wins
- **<service / category>** — saves $X/mo. <one-sentence reason>.
- ...

## Where AWS wins
- **<service / category>** — costs $Y more on GCP. <one-sentence reason>.
- ...

## Caveats
- <one-sentence each. Examples: AWS PRC discount unmirrored on GCP at list;
  ARM workloads mapped to C4A but CUD rates not yet in the catalog;
  N services passed through with no GCP equivalent.>
- ...

## Confidence
<one paragraph about how confident the projection is — how many rows are
high-confidence mappings, where the soft spots are>
```

### How to populate each section

- **Bottom line** — one sentence, lead with the headline number.
  "GCP 3-year CUD is ~13% cheaper" / "GCP On-Demand is roughly
  flat vs AWS, but 3-year CUD saves 22%" / "GCP costs ~8% more at
  list; the gap closes to 4% with 3-year CUD". State the comparison
  basis (OD vs OD, or CUD vs effective AWS rate after RI/SP).
- **Where GCP wins** — query `gcp_projection` for the rows where
  `gcp_projected_cost < aws_amortized_cost` materially (think
  `aws - gcp > $50/mo` or top-10 by absolute savings). Group by
  natural category — Compute, Storage, Database, Network — using
  the same pills as the main report. Order **largest dollar
  savings first**, cap at **5–7 bullets**. The one-sentence reason
  is the agent's call: "N2D family lists cheaper than c5a per
  vCPU-hour", "Cloud Storage Standard is ~15% below S3 in
  ap-south-1", etc. Don't fabricate — pull from the mapping
  decisions in `aws_li_to_gcp_li.projection_note` and the actual
  rate gap.
- **Where AWS wins** — same shape, opposite direction. Rows where
  GCP costs materially more. Order **largest dollar penalty
  first**, cap at **5–7 bullets**. Common honest causes: AWS PRC
  / EDP discount that GCP list price doesn't mirror; ElastiCache
  Redis Basic priced below Memorystore Basic; a region with no
  GCP presence (mapped to nearest-region with cross-region egress
  surcharge).
- **Caveats** — anything that would change the read if it were
  different. One sentence each. Mandatory inclusions when they
  apply:
  - **AWS PRC / EDP / private pricing** observed in the bill but
    not mirrored on GCP at list — quantify the implied list-vs-list
    gap.
  - **ARM workloads** mapped to C4A when C4A CUD rates aren't in
    the catalog yet — note that the 1yr/3yr columns reflect OD
    pricing for those rows.
  - **Passthroughs** — name how many rows passed through and the
    cumulative dollar amount. ("3 rows passed through totaling
    $142.18 — AWS Support, AWS Config item recording, and one
    S3-INT monitoring fee with no GCP analog.")
  - **Region mapping** assumptions when a non-trivial region had
    no GCP equivalent (e.g. AWS Bahrain → GCP Doha).
- **Confidence** — one paragraph, prose. Quantify:
  `mapped_rows` from `run_results`, the count at each confidence
  band, and where the soft spots are. Example shape: *"217 mapping
  rows: 198 high-confidence (direct SKU + verified rate), 16
  medium (rate aliased from a sibling region), 3 low (Glue and
  Athena have no direct GCP analog; mapped to BigQuery + Dataform
  with broad assumptions). Three rows passed through (Support,
  Config, S3-INT monitoring). Re-check the low-confidence rows
  before signing off."*

### Style rules

- The narrative is the agent's call — these aren't hardcoded
  categories. Pull from the actual mapping decisions and the data
  in `gcp_projection`.
- **Order each list by dollar impact, largest first.** Don't
  alphabetize.
- **Cap each bullet list at 5–7 items.** If the customer needs
  the long tail they read the main report.
- No tables in the summary. Tables go in the main report; the
  summary is prose-and-bullets.
- Refinement runs: this file gets rewritten under a new
  `summary-<run_id>.md` filename. The Bottom line should
  acknowledge the refinement when applicable — *"With Premium
  Tier egress applied per refinement, GCP 3-year CUD is now ~9%
  cheaper (was ~13%)"*.
