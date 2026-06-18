# Phase 1 — Ingestion

**Run by:** one sub-agent. Main agent dispatches and waits.
**Reads:** the input bill file path supplied by the user.
**Writes:** `aws_raw`, `aws_li_catalog` (see [../reference/schemas.md](../reference/schemas.md)).
**Returns to main:** `{aws_raw_rows, aws_li_catalog_rows, reconcile_delta, anomalies[]}`.

This phase is noisy (head/DESCRIBE iteration, classifier tweaks). The
main agent doesn't need to see the noise — it only needs the resulting
table and the reconcile delta.

## What to do

### 1. Inspect the input

`head -3 <file>`, or `DESCRIBE` after a tentative `read_csv_auto`. Three
known shapes:

- **Raw CUR** — hundreds of columns, has `lineItem/LineItemType`. CSV
  or parquet, possibly gzipped, sometimes a directory of part files.
- **Flat AWS Cost Explorer Detail Report** — 6 columns:
  `Service, Region, Custom Usage Type, Description, Usage Quantity,
  Cost ($)`. Comma thousand-separators; "Total ($)" synthetic last row.
- **AWS console PDF bill** — `file` reports `PDF document`. Convert
  to the flat Cost Explorer shape FIRST (a custom pre-processing step
  per PDF flavor), then ingest the resulting CSV. See
  [../reference/pdf-ingestion.md](../reference/pdf-ingestion.md) for
  the recipe (`pdftotext -layout` + indent-based row classification +
  per-service reconciliation against the PDF's own "Taxes by service"
  totals). PDFs lack `LineItemType` and pricing-model columns, so
  classification is coarser, but the projection still works for the
  visible cost.

If none matches, return to main with `anomalies: ["unknown input
shape: <first row>"]` and stop. Don't guess. The main agent should
then write a one-paragraph explanation to `./failure.txt` in the
working directory (e.g. "This looks like an Azure cost export, not
an AWS bill — the skill only projects AWS → GCP.") so downstream
automation can surface the reason without retrying.

### 2. Load `aws_raw` as-is

Don't materialize a fixed schema; the next step is where shape settles.

```sql
CREATE TABLE aws_raw AS
  SELECT * FROM read_csv_auto('<input_path>', ALL_VARCHAR=TRUE);
```

Use `read_parquet` / `read_json_auto` / glob if appropriate.

### 3. Build `aws_li_catalog`

`GROUP BY` the dedup tuple (product × usage_type × operation × region ×
pricing_model × line_item_type × is_workload), `SUM` `total_usage` and
`aws_amortized_cost`. Compute `aws_li_key` as `md5` of the tuple.

**`operation` carries the AWS Description verbatim — keep it intact.**
On AWS Cost Explorer Detail Reports (flat-CSV and PDF-derived),
`usage_type` (the "Custom Usage Type" column) is a *rollup* string —
the same value (`"Amazon Elastic Compute Cloud running Linux/UNIX"`,
`"EBS"`, `"Bandwidth"`) covers many distinct workloads in the same
region. The instance type (t3.micro / m5.4xlarge / c5.large), disk
class (gp3 / gp2 / Snapshot), data-transfer sub-type (regional /
internet / inter-region), discount note (`[EC2 PRC] EC2 Discount @
35.00%`), and net unit rate (`$0.014560 per On Demand Linux t3.small
Instance Hour`) all live in `operation`. Phase 2 keys its mapping on
`operation`, not `usage_type`, so do not trim, normalize, or pre-parse
this column — store the AWS Description string as-is. Including
`operation` in the dedup tuple is what keeps each distinct workload
as its own catalog row instead of collapsing under the rollup.

### 4. Classify `is_workload`

The signals depend on which input shape you have.

**Raw CUR shape** (has `lineItem/LineItemType`):

- `is_workload = FALSE` for `LineItemType` in
  `('Tax','RIFee','SavingsPlanUpfrontFee','SavingsPlanRecurringFee','SavingsPlanNegation','SavingsPlanCoveredUsage','Refund','Credit','EdpDiscount','PrivateRateDiscount','BundledDiscount')`.
- `is_workload = TRUE` for `LineItemType` in `('Usage','DiscountedUsage')`
  — these carry the actual workload running on AWS.

**Flat Cost Explorer Detail Report shape** (no LineItemType column):

- `is_workload = FALSE` for rows whose `Description` matches any of:
  `'covered by Compute Savings Plans'`,
  `'covered by EC2 Instance Savings Plans'`,
  `'covered by Reserved Instances'`,
  `'committed.*upfront'`, `'No upfront fee'`,
  `'Recurring monthly fee'`, `'EDP Discount'`,
  `'Private Pricing Discount'`, `'Refund'`, `'Credit'`.
  SP-coverage offsets show up here as **negative-cost rows** with
  description containing "covered by … Savings Plans" — drop them as
  not-workload (the matching positive Usage row is what projects).
- `is_workload = TRUE`, `pricing_model = 'Committed'`,
  `line_item_type = 'DiscountedUsage'` for rows whose `Description`
  contains `'reserved instance applied'`. This is the RI-amortized
  effective row — real workload, post-RI rate. Set
  `projection_note = 'AWS rate is RI-amortized; compare GCP CUD, not OD'`
  so outlier triage doesn't waste time on it.
- `is_workload = TRUE` for everything else that survives the tax
  filter.

**Tax: drop entirely** (don't even write to `aws_li_catalog`) — match
on `LineItemType='Tax'` (raw CUR) or
`Service='Tax'` / `Description ILIKE '%tax%'` (flat).

### 5. Map `gcp_region` from `aws_region`

Straight regional equivalents where they exist:
- `ap-southeast-1` → `asia-southeast1`
- `ap-south-1` → `asia-south1`
- `us-east-1` → `us-east4`
- `us-east-2` → `us-east4`
- `us-west-2` → `us-west1`
- `eu-west-1` → `europe-west1`
- `eu-central-1` → `europe-west3`
- (etc. — pick the closest geographic match)

Region-agnostic AWS rows ("Global") → `gcp_region = NULL`. The mapping
phase decides per-LI which GCP service handles them.

### 6. Reconcile

```sql
SELECT (SELECT SUM(CAST("Cost ($)" AS DOUBLE)) FROM aws_raw
        WHERE Service != 'Tax')
     - (SELECT SUM(aws_amortized_cost) FROM aws_li_catalog) AS delta;
```

(Adapt column names per input shape.) **Off-by-anything is a classifier
bug** — debug before returning to main. The reconcile delta in the
return payload should be exactly 0.

## Anomalies to surface in the return payload

Return these to the main agent as `anomalies[]` so the mapping phase
knows what to expect:

- Rows with no `aws_region` and no obvious global classification
  (the mapping phase needs to decide per-LI).
- Negative-cost rows whose description doesn't match any of the
  is_workload=FALSE patterns above (potential new discount line type
  the classifier doesn't know about).
- Distinct count of rows whose description contains `Multi-AZ` (the
  mapping phase needs to map these to Cloud SQL Regional / HA tiers).
- Distinct count of rows containing `'reserved instance applied'`
  (these will need GCP-CUD vs AWS-effective comparisons in outlier
  triage).
