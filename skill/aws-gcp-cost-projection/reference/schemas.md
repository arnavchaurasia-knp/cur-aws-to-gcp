# DuckDB schemas

Four tables live in `projection-audit/projection.duckdb`. Initialize
them empty at the start of the run; each phase populates its own.

```sql
-- 1. Raw rows, as-is from the input file. No transformation, no dedup.
--    Columns vary by input format — use SELECT * so DuckDB infers them.
--    Don't materialize a fixed schema; the next table is where shape
--    settles. (Populated by Phase 1.)
CREATE TABLE aws_raw AS
  SELECT * FROM read_csv_auto('<input_path>', ALL_VARCHAR=TRUE);
  -- (use read_parquet / read_json_auto / glob if appropriate; you decide
  --  by inspecting the file)


-- 2. Unique AWS line-item types (deduped). One row per
--    (product × usage_type × operation × region × pricing_model
--     × line_item_type × is_workload).
--    (Populated by Phase 1.)
CREATE TABLE aws_li_catalog (
    aws_li_key         VARCHAR PRIMARY KEY,   -- md5 of the dedup tuple
    product            VARCHAR,               -- AWS Service (e.g. "Elastic Compute Cloud")
    aws_region         VARCHAR,               -- raw AWS region string from input
    gcp_region         VARCHAR,               -- mapped equivalent (you derive)
    usage_type         VARCHAR,               -- AWS usage_type / "Custom Usage Type" — Cost-Explorer rollup string, not row-distinguishing
    operation          VARCHAR,               -- AWS operation / verbatim Description (carries instance type, disk class, transfer sub-type, net rate); Phase 2's structural mapping key
    line_item_type     VARCHAR,               -- 'Usage' | 'DiscountedUsage' | 'SavingsPlanCoveredUsage' | 'RIFee' | …
    pricing_model      VARCHAR,               -- 'OnDemand' | 'Spot' | 'Committed'
    is_workload        BOOLEAN,               -- TRUE = projects to GCP; FALSE = AWS-side commercial mechanism
    total_usage        DOUBLE,
    aws_amortized_cost DOUBLE
);


-- 3. Mapping table: one AWS LI → 1+ GCP rows.
--    (Populated by Phase 2; corrected by Phase 3 and Phase 5.)
CREATE TABLE aws_li_to_gcp_li (
    aws_li_key      VARCHAR,
    strategy        VARCHAR CHECK (strategy IN ('map','break_down','passthrough','ignore')),
    gcp_service     VARCHAR,                  -- 'Compute Engine' | 'Cloud SQL' | 'Networking' | NULL for ignore
    gcp_sku_id      VARCHAR,                  -- exact SKU ID from Cloud Billing Catalog (NULL for ignore/passthrough)
    component       VARCHAR,                  -- 'core' | 'ram' | 'storage' | 'accelerator' | 'os_premium' | NULL
    unit_from       VARCHAR,                  -- AWS unit ('Hrs', 'GB-Mo', 'Requests-1000', …)
    unit_to         VARCHAR,                  -- GCP unit ('h', 'GiBy.mo', 'Count-10000', …)
    unit_multiplier DOUBLE,                   -- so total_usage × unit_multiplier × rate = GCP cost
    projection_note VARCHAR                   -- one-line rationale
);


-- 4. GCP rate card — one row per (sku × pricing_type × region).
--    (Populated by Phase 4, lazily — only for gcp_sku_ids referenced
--     in aws_li_to_gcp_li.)
CREATE TABLE gcp_sku_rates (
    gcp_sku_id      VARCHAR,
    gcp_service     VARCHAR,
    gcp_sku_name    VARCHAR,
    resource_family VARCHAR,                  -- 'Compute' | 'Storage' | 'Network' | 'License'
    resource_group  VARCHAR,                  -- 'CPU' | 'RAM' | 'SSD' | 'InterregionEgress' | …
    pricing_type    VARCHAR,                  -- 'OnDemand' | 'Commit1Yr' | 'Commit3Yr' | 'Preemptible'
    region          VARCHAR,
    unit            VARCHAR,
    rate_usd        DOUBLE,
    source          VARCHAR,                  -- 'catalog-bundled' | 'doc-percentage'
    audit_url       VARCHAR,
    PRIMARY KEY (gcp_sku_id, pricing_type, region)
);
```

Phase 5 builds a `gcp_projection` view on top of these — see
[../phases/05-outlier-triage.md](../phases/05-outlier-triage.md).
