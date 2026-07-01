# Phase 4 — Rate-card fill

**Run by:** main agent. Mechanical script phase, no judgment calls.
**Reads:** `aws_li_to_gcp_li` (corrected by Phase 3).
**Writes:** rows in `gcp_sku_rates` (via deterministic script).

## FIRST LINE OF THIS PHASE — write progress marker

Before any other work, write `progress.json` in the job working directory:

```python
import json
with open("progress.json", "w") as f:
    json.dump({"phase": 4, "phase_name": "Rate-Card Fill", "last_activity": "Fetching GCP rates via script"}, f)
```

This is required — the UI reads it every 5 s and will show a blank screen for phases before the last if you skip it.

## Execution

This phase used to require you to manually look up SKUs, handle region fallbacks, and compute tiered and CUD rates using complex SQL logic.

**That is no longer required.** A deterministic Python script now does all of this perfectly without hallucination.

Run the script:

```bash
python3 scripts/apply_rates.py
```

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

Unreachable → either the allow-list is short a service, or the
mapping uses a different `displayName` than the catalog, or the SKU was completely removed by Google.

Don't proceed to Phase 5 until this returns 0 rows (or you log the unmappable rows in your phase summary).
