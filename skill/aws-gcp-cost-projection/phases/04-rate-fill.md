# Phase 4 — Rate-card fill

**Run by:** main agent. Mechanical SQL phase, no judgment calls.
**Reads:** `aws_li_to_gcp_li` (corrected by Phase 3), `data/skus/`,
`data/services.json`.
**Writes:** rows in `gcp_sku_rates` (one row per
`(gcp_sku_id × pricing_type × region)`).

This phase only populates the rate card — it doesn't make mapping
decisions. If a `gcp_sku_id` is missing from `data/skus/`, that's a
mapping bug; surface it and stop, don't paper over it.

## Bundled catalog layout

```
data/
  services.json              -- 110 first-party services (id ↔ displayName)
  skus/<service_id>.json.gz  -- one file per service, full SKU list
  CATALOG_META.json          -- fetched_at, sku_count
```

The staleness check at the top of `SKILL.md` already ran. Don't run
`refresh-catalog.sh` from here — that's a maintainer task.

## Load lazily, not in bulk

Don't bulk-import all 84K SKUs into `gcp_sku_rates` — most of them
you'll never reference. Populate only for the `gcp_sku_id`s that
actually appear in `aws_li_to_gcp_li` (typically 50–500 rows total).

```sql
-- Step 1: enumerate distinct sku_ids used by mapping
WITH used AS (
  SELECT DISTINCT gcp_sku_id, gcp_service
  FROM   aws_li_to_gcp_li
  WHERE  gcp_sku_id IS NOT NULL
)
-- Step 2: for each, find which service file owns it
SELECT u.gcp_sku_id, s.serviceId, s.displayName
FROM   used u
LEFT JOIN read_json_auto('data/services.json') s
  ON s.displayName = u.gcp_service;
```

Then read each relevant `data/skus/<serviceId>.json.gz`, filter to
your sku_ids, and INSERT the rows.

## SKU JSON shape

Each SKU object looks like:

```json
{
  "skuId": "0561-8741-3BC7",
  "description": "N2D AMD Instance Core running in Singapore",
  "category": {
    "serviceDisplayName": "Compute Engine",
    "resourceFamily": "Compute",
    "resourceGroup": "CPU",
    "usageType": "OnDemand"
  },
  "serviceRegions": ["asia-southeast1"],
  "pricingInfo": [{
    "pricingExpression": {
      "tieredRates": [
        { "startUsageAmount": 0, "unitPrice": {"nanos": 33929000, "currencyCode": "USD"} }
      ],
      "usageUnit": "h",
      "baseUnit": "s"
    }
  }]
}
```

INSERT one row per `(sku, pricing_type, region)` into `gcp_sku_rates`.
Set `source = 'catalog-bundled'` and
`audit_url = 'data/skus/<service_id>.json.gz#<skuId>'`.

## Gotchas — bake into the parser

1. **Region emission — be exhaustive, otherwise rows silently project as $0.**
   A SKU's region list comes from `serviceRegions` plus `geoTaxonomy`.
   Emit one rate row per **resolved single-region code**, applying
   all three rules below. The `gcp_projection` view (Phase 5) falls
   back from regional rate → `region='global'`; if neither matches
   a row's `gcp_region`, `gcp_projected_cost` becomes NULL and the
   row renders as $0 in the report.

   - **Rule A — globally-priced SKUs.** When
     `geoTaxonomy.type == "GLOBAL"`, the SKU bills at the same rate
     everywhere; `serviceRegions` may list only a representative
     subset (e.g. `["us-central1","us-east1","us-west1","asia-east1","europe-west1"]`
     for `DE9E-AFBC-A15A` Inter-Zone Egress, which actually applies
     in every region). Emit one row with `region='global'`. This is
     in addition to (or in place of) the serviceRegions entries —
     the projection view's `global` fallback picks it up for any
     `gcp_region`.
   - **Rule B — multi-region container codes.** `serviceRegions`
     entries like `"us"`, `"eu"`, `"asia"`, `"northamerica"`,
     `"europe"` are *region containers*, not single regions. Expand
     each to the constituent single-region codes the catalog uses
     elsewhere (`"us"` → `us-central1, us-east1, us-east4, us-east5,
     us-south1, us-west1, us-west2, us-west3, us-west4`, etc.).
     Emit one row per single-region code.
   - **Rule C — single-region codes.** `serviceRegions` entries
     that already look like `asia-south1`, `europe-west3`, etc.
     pass through as-is — one row per entry.

   Sanity check after rate-fill: for every distinct `gcp_region`
   referenced in `aws_li_to_gcp_li`, the mapped sku_id has at least
   one rate row whose `region` is either that `gcp_region` or
   `'global'`. The Phase 5 sanity check at the end of this file
   verifies this; do not proceed to Phase 5 until it returns 0
   rows.
2. **`tieredRates` lives under `pricingInfo[0].pricingExpression`**,
   not under `pricingInfo[0]` directly. Deep-nest carefully.
3. **Tiered pricing** — `tieredRates[0]` is often the free-tier
   allotment (`unitPrice = 0`). Use the **first non-zero `unitPrice`**.
4. **Price encoding** — `unitPrice.nanos` is integer nanos;
   `unitPrice.units` is integer dollars. Combined =
   `units + nanos / 1e9`.
5. **`pricing_type`** — read from `category.usageType`. Values are
   `OnDemand | Commit1Yr | Commit3Yr | Preemptible`. Cloud SQL /
   Memorystore don't ship CUD SKUs — see synthesis below.
6. **Shared parent service-id** — e.g. Networking and Cloud Armor both
   live under `E505-1604-58F8`. Key `gcp_service` on
   `category.serviceDisplayName`, not the parent service ID.
7. **Allow-list scope** — marketplace and 3rd-party services are
   excluded by `scripts/firstparty-allowlist.txt`. If a needed
   first-party service is missing from `data/services.json`, surface
   that to the user — extending the allow-list and re-running
   `refresh-catalog.sh` is a maintainer action.

## Filling in commitment rates

Three patterns, depending on the service.

### 1. Compute Engine — real Commit SKUs exist; alias them onto the OD sku_id

GCE publishes separate `Commit1Yr` / `Commit3Yr` SKUs in the catalog
(e.g. N2D AMD has OD sku `8B4E-B458-AD51` and 1yr-CUD sku
`C576-D7FE-0B62` — different IDs, both real). The projection JOIN in
Phase 5 expects to find Commit1Yr/3Yr rows under the **same** sku_id
as the OD row, so you have to alias.

For each OD compute sku_id you mapped, look up the matching commit
sku in the same region/resource_group/family, then INSERT a row into
`gcp_sku_rates` keyed on the OD sku_id with
`pricing_type='Commit1Yr'` (or `Commit3Yr`) and the commit rate.

```sql
-- Example pattern (do this for every distinct OD compute sku in mapping):
-- 1) Look up the commit sku via find-sku.sh / direct query.
-- 2) Insert under the OD sku_id, with commit rate:
INSERT INTO gcp_sku_rates VALUES
  ('8B4E-B458-AD51', 'Compute Engine', 'N2D AMD Core (1yr CUD alias)',
   'Compute', 'CPU', 'Commit1Yr', 'asia-southeast1', 'h',
   0.021373,  -- from sku C576-D7FE-0B62
   'catalog-bundled', 'data/skus/6F81-5844-456A.json.gz#C576-D7FE-0B62');
```

The OD sku_id is a foreign key in your mapping; the commit sku_id is
a real catalog SKU you copied the rate from. `audit_url` should name
the real commit sku for traceability.

### 2. Cloud SQL & Memorystore — no Commit SKUs in catalog; multiply OD

These services don't ship `Commit1Yr` / `Commit3Yr` SKUs at all.
Synthesize from OD using Google's published CUD percentages
(× 0.75 for 1yr, × 0.48 for 3yr — verify the exact numbers against
`https://cloud.google.com/sql/cud` and
`https://cloud.google.com/memorystore/docs/redis/committed-use-discounts`).

```sql
-- Cloud SQL CPU/RAM/storage resource_groups (NOT plain 'CPU','RAM'):
INSERT INTO gcp_sku_rates(
  gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
  resource_group, pricing_type, region, unit,
  rate_usd, source, audit_url)
SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
       resource_group, 'Commit1Yr', region, unit,
       rate_usd * 0.75, 'doc-percentage',
       'https://cloud.google.com/sql/cud'
FROM   gcp_sku_rates
WHERE  gcp_service IN ('Cloud SQL','Cloud Memorystore',
                       'Cloud Memorystore for Redis',
                       'Cloud Memorystore for Memcached')
  AND  pricing_type = 'OnDemand'
  AND  resource_group IN (
         'SQLGen2InstancesCPU','SQLInstancesCPU',
         'SQLGen2InstancesRAM','SQLInstancesRAM',
         'SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD',
         'RedisCapacityBasicM1','RedisCapacityBasicM2',
         'MemcacheNode'
       );
-- Same for Commit3Yr at × 0.48.
```

### 3. Other services — no commit pricing

Most managed-service SKUs (Cloud Run, Cloud Storage, Pub/Sub, BigQuery
on-demand, etc.) bill at OD only. Leave Commit1Yr/Commit3Yr out for
these; the projection view in Phase 5 falls back to OD via
`COALESCE(c1.rate_usd, od.rate_usd)`.

## Sanity check before handing off to Phase 5

```sql
-- For every mapped row, the SKU must have a rate row whose region
-- matches the catalog's gcp_region OR is 'global'. A mapping that
-- has *some* rate rows but none reachable for this row's region
-- silently projects $0 — exactly what the Mumbai inter-AZ Egress
-- bug looked like before Gotcha #1 was tightened. Catch it here.
SELECT m.gcp_service, c.gcp_region, m.gcp_sku_id, COUNT(*) AS unreachable_rows
FROM   aws_li_to_gcp_li m
JOIN   aws_li_catalog   c USING (aws_li_key)
WHERE  m.gcp_sku_id IS NOT NULL
  AND  m.strategy IN ('map','break_down')
  AND  NOT EXISTS (
         SELECT 1 FROM gcp_sku_rates r
         WHERE  r.gcp_sku_id = m.gcp_sku_id
           AND  (r.region = c.gcp_region OR r.region = 'global')
       )
GROUP BY m.gcp_service, c.gcp_region, m.gcp_sku_id;
```

Unreachable → either Gotcha #1 wasn't fully applied (missed the
`geoTaxonomy.type='GLOBAL'` case or a multi-region container code
in `serviceRegions`), the allow-list is short a service, or the
mapping uses a different `displayName` than the catalog. Don't
proceed to Phase 5 until this returns 0 rows.
