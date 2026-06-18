# Phase 5 — Outlier triage

**Run by:** one sub-agent. Like the Phase 3 review agent, this one
comes in fresh — without the mapping agents' or main agent's
investment in the picks — and is more willing to overturn them.
**Reads:** `gcp_projection` view (built below), `aws_li_to_gcp_li`,
`aws_li_catalog`, `mapping-notes.md`, `data/skus/`.
**Writes:** corrections directly to `aws_li_to_gcp_li`.
**Returns to main:** one paragraph — what was flagged, fixed, and
accepted (with the documented mechanism cited for each acceptance).

If the combined outlier output is **>50 rows**, you may split across
**up to 4 sub-agents** by query family — A1+A2 (cost deviation), B+C
(phantom + zero-rate), D+E (cross-service + missing CUD), F (unit
sanity). Don't split below 30 rows total — context priming costs more
than the parallelism saves.

## Build the projection view

This view is the substrate for every outlier query. It applies a
region-fallback rule (regional rate first, then `'global'`) so SKUs
keyed at `region='global'` (GCS ops, IP, DNS, Pub/Sub, inter-region
egress destinations) aren't silently zero-priced.

```sql
CREATE OR REPLACE VIEW gcp_projection AS
WITH od_pick AS (  -- pick regional rate first, fall back to global
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'OnDemand'
  GROUP BY m.aws_li_key, m.gcp_sku_id
),
c1_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'Commit1Yr'
  GROUP BY m.aws_li_key, m.gcp_sku_id
),
c3_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'Commit3Yr'
  GROUP BY m.aws_li_key, m.gcp_sku_id
)
SELECT  c.aws_li_key, c.product, c.aws_region, c.gcp_region,
        c.line_item_type, c.pricing_model, c.is_workload,
        c.total_usage, c.aws_amortized_cost,
        m.strategy, m.gcp_service, m.gcp_sku_id, m.component,
        m.unit_multiplier, m.projection_note,
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          ELSE c.total_usage * m.unit_multiplier * od.rate_usd
        END AS gcp_projected_cost,
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          ELSE c.total_usage * m.unit_multiplier * COALESCE(c1.rate_usd, od.rate_usd)
        END AS gcp_cost_1yr_cud,
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          ELSE c.total_usage * m.unit_multiplier * COALESCE(c3.rate_usd, od.rate_usd)
        END AS gcp_cost_3yr_cud
FROM    aws_li_catalog c
LEFT JOIN aws_li_to_gcp_li m ON m.aws_li_key = c.aws_li_key
LEFT JOIN od_pick od ON od.aws_li_key = m.aws_li_key AND od.gcp_sku_id = m.gcp_sku_id
LEFT JOIN c1_pick c1 ON c1.aws_li_key = m.aws_li_key AND c1.gcp_sku_id = m.gcp_sku_id
LEFT JOIN c3_pick c3 ON c3.aws_li_key = m.aws_li_key AND c3.gcp_sku_id = m.gcp_sku_id;
```

> **AWS double-count warning.** A `break_down` mapping produces N rows
> in `aws_li_to_gcp_li` per AWS LI. The view above carries
> `aws_amortized_cost` on every output row, so a naive
> `SUM(aws_amortized_cost) FROM gcp_projection` inflates AWS by N. For
> the report's "AWS Total" row in Phase 6, **always sum from
> `aws_li_catalog`** (one row per LI). GCP totals are safe to sum from
> the view because the per-row GCP cost already represents the
> component's slice.

## Triage order — never skip steps

When a row appears in any outlier query, walk through these checks
in order. Don't jump to step 4 without doing 1–3.

1. **Mapping check.** Does the GCP SKU's HA tier, instance family,
   region, and engine match the AWS description text *literally*?
   Re-read the description; don't infer. Most common bug class.
2. **Unit check.** Re-run the implied-rate back-check from mapping
   rule #9 (`aws_unit_rate` vs `gcp_unit_rate`). 100×/1000× off →
   unit interpretation is inverted, fix the multiplier.
3. **Spec check.** Does `unit_multiplier` match the AWS instance
   type's actual vCPU / RAM / IOPS? (16-vCPU instance × 1h on the
   bill should multiply by 16, not 1.)
4. **Bill is ground truth** (Phase 2 principle, repeated here because
   this is where it bites). The only commercial mechanisms you may
   invoke as the explanation are those visible as line items in the
   input: `reserved instance applied`, SP-coverage offsets, EDP /
   Private Pricing / CK Discount lines. If a documented line item
   explains the row, the like-for-like comparison is GCP `1yr_cud` /
   `3yr_cud` against the AWS effective rate, not GCP OD — note in
   `projection_note` and move on.

   If **no** documented mechanism explains the row, the row is wrong.
   Speculative rationales — "free tier we don't see", "reseller
   absorbs this", "promo credit applied silently", "probably bundled
   with parent account" — are not valid resolutions. They're signals
   you skipped step 1 or 2. Go back and find the bug.

   The textbook violation: phantom-zero AWS row with $5K GCP
   projection, "explained" by an invented vendor absorption. Almost
   always the multiplier is wrong.

Steps 1–3 are 30 seconds each. Skipping straight to step 4 is the
classic way real bugs survive review.

## The seven queries

```sql
-- A1. Big-dollar deviations: meaningful rows where GCP cost has
--     shifted noticeably from AWS. The $50 floor keeps the report
--     focused on rows that materially affect the total.
SELECT aws_li_key, product, gcp_service, gcp_sku_id,
       ROUND(aws_amortized_cost,2) AS aws,
       ROUND(gcp_projected_cost,2) AS gcp,
       ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
FROM   gcp_projection
WHERE  strategy NOT IN ('ignore','passthrough')
  AND  aws_amortized_cost > 50
  AND  ( gcp_projected_cost > aws_amortized_cost * 1.5
      OR gcp_projected_cost < aws_amortized_cost * 0.667 );

-- A2. Wildly implausible ratios at any dollar size. Ratio of 100×
--     or 0.01× is almost always a bug — small AWS rows ($1–50) hide
--     unit-multiplier mistakes that A1 misses (e.g. WAF $16 row that
--     projected to $0.02 due to a 1/720 multiplier bug). No $ floor.
SELECT aws_li_key, product, gcp_service, gcp_sku_id,
       ROUND(aws_amortized_cost,2) AS aws,
       ROUND(gcp_projected_cost,2) AS gcp,
       ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
FROM   gcp_projection
WHERE  strategy NOT IN ('ignore','passthrough')
  AND  aws_amortized_cost > 1   -- skip cents-rows
  AND  ( gcp_projected_cost > aws_amortized_cost * 5
      OR gcp_projected_cost < aws_amortized_cost * 0.2 );

-- B. Phantom GCP cost: AWS row had ~0 cost but GCP projection is large.
--    HARD RULE — every row here is a unit_multiplier bug until proven
--    otherwise by a *visible* line item in the bill (free-tier
--    pass-through, credit, explicit discount line). No visible
--    mechanism = no exception. The agent's narrative — "free tier we
--    don't see", "absorbed by reseller", "AWS rounded", "promotional
--    credit applied silently" — is NOT a valid resolution. This is
--    rule #9 Branch B applied at triage time; treat it as mechanical.
SELECT aws_li_key, product, gcp_service, gcp_sku_id,
       total_usage, ROUND(gcp_projected_cost,2) AS gcp
FROM   gcp_projection
WHERE  aws_amortized_cost <= 1
  AND  gcp_projected_cost > 10;

-- C. Zero rate on a billable row: SKU resolved but rate_usd=0 and the
--    AWS row carries non-trivial cost. Usually a free-tier SKU was
--    picked over the per-unit one.
SELECT aws_li_key, gcp_sku_id, gcp_sku_name, aws_amortized_cost
FROM   aws_li_to_gcp_li m
JOIN   aws_li_catalog c USING (aws_li_key)
JOIN   gcp_sku_rates  r ON r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'OnDemand'
WHERE  m.strategy IN ('map','break_down')
  AND  r.rate_usd = 0
  AND  c.aws_amortized_cost > 50;

-- D. Cross-service mismatch: mapping declares service X but the
--    resolved SKU is registered under service Y in the Catalog.
SELECT m.aws_li_key, m.gcp_service AS mapping_says, r.gcp_service AS sku_actually,
       m.gcp_sku_id
FROM   aws_li_to_gcp_li m
JOIN   gcp_sku_rates    r ON r.gcp_sku_id = m.gcp_sku_id
WHERE  m.strategy IN ('map','break_down')
  AND  m.gcp_service IS NOT NULL AND r.gcp_service IS NOT NULL
  AND  m.gcp_service != r.gcp_service;

-- E. CUD missing where it should exist. Compute Engine, Cloud SQL,
--    and Memorystore should all have Commit1Yr (and ideally Commit3Yr)
--    rows aliased onto every OD compute / database core+ram sku.
SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_id
FROM   aws_li_to_gcp_li m
WHERE  m.strategy IN ('map','break_down')
  AND  m.gcp_service IN ('Compute Engine','Cloud SQL',
                         'Cloud Memorystore','Cloud Memorystore for Redis',
                         'Cloud Memorystore for Memcached')
  AND  m.component IN ('core','ram')
  AND  NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                   WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'Commit1Yr');

-- F. Unit-multiplier sanity check: for non-trivial AWS rows, the
--    projected OD cost should be within 0.5×–2× of AWS cost. Way
--    outside that range usually means the unit_multiplier is wrong
--    (classic case: AWS qty is "raw requests" but the mapper assumed
--    "per 10K"). This catches phantom-cost bugs before outlier A.
--    Filter to like-for-like comparisons (OD AWS rows only, no SP/RI
--    discount distortion).
SELECT aws_li_key, product, gcp_service, gcp_sku_id,
       ROUND(aws_amortized_cost,2) AS aws,
       ROUND(gcp_projected_cost,2) AS gcp_od,
       unit_multiplier,
       ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
FROM   gcp_projection
WHERE  strategy = 'map'  -- skip break_down (split rows make ratios noisy)
  AND  pricing_model = 'OnDemand'
  AND  line_item_type IN ('Usage')
  AND  aws_amortized_cost > 20
  AND  ( gcp_projected_cost > aws_amortized_cost * 2
      OR gcp_projected_cost < aws_amortized_cost * 0.5 );
```

## Done condition

All seven queries return zero rows after triage, **or** every
remaining row has been documented in `mapping-notes.md` under
`## Outlier acceptance` with a cited mechanism (RI-applied, SP
offset, etc.) per step 4 of the triage order.

Don't return to main until one or the other holds.
