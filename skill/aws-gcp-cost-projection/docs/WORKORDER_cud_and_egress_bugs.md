# WORKORDER — CUD & Egress Projection Bugs (3 confirmed)

Status: **READY TO IMPLEMENT.** All three found and reproduced in the pricing-review pass. Mechanical for a lower-tier model; each has an exact site, fix, and verification. Do them in order (independent, but BUG-2 and BUG-3 touch the same files).

Rates/behaviors verified against official pages during the review (cite these in code comments per AGENTS.md):
`cloud.google.com/vpc/network-pricing`, `cloud.google.com/memorystore/docs/redis/cuds`.

---

## BUG-1 (P0) — Inter-region egress mislabeled inter-zone → 100% passthrough

### Symptom
`APS3-USW2-AWS-Out-Bytes` (Mumbai→Oregon = **inter-region**) is shown as GCP target **"Network Inter Zone Data Transfer Out"** and priced at **100% of AWS** (GCP = AWS to the cent = passthrough, no rate applied). Correct result: inter-region egress ≈ $0.08/GiB (~93% of AWS Mumbai out $0.086/GiB), priced via a real SKU — not a passthrough, and not labeled inter-zone.

### Root cause (reproduced)
`transfer_direction()` rule 4 returns `interzone` when `from_region == to_region` (`scripts/usage_type_parser.py:430-432`). For `AWS-Out-Bytes` egress rows, AWS often sets `product/toRegionCode = fromRegionCode` (the billing region), so `ingest.py` (`scripts/ingest.py:2946-2951`) sets `to_region = from_region = asia-south1`. Rule 4 then fires → inter-zone → the interzone SKU's rate fails to resolve for the region → passthrough fallback carries AWS cost (100%). The true destination (`USW2`) is in the usage_type token and is ignored.

Reproduced locally:
```
transfer_direction(from=asia-south1, to=asia-south1)      -> ('map','interzone',...)   # the bug
decode_region_pair('APS3-USW2-AWS-Out-Bytes')             -> ('asia-south1','us-west1') # true: different
```

### Fix (primary — single site, pure function)
In `scripts/usage_type_parser.py`, **before** the rule-4 `from_reg == to_reg` block (currently ~line 427 `# CUR from_region and to_region ... win`), insert an authoritative override: if the usage_type encodes **two different real regions**, it is inter-region regardless of the (possibly-wrong) from/to columns.

```python
# Authoritative override: a usage_type encoding two DIFFERENT real regions
# (e.g. "APS3-USW2-AWS-Out-Bytes") is unambiguously inter-REGION. AWS often sets
# product/toRegionCode == fromRegionCode on egress rows, which would otherwise
# trip the from==to -> interzone branch below and mislabel a cross-region lane.
# Verified: cloud.google.com/vpc/network-pricing (inter-region != inter-zone).
from region_prefix import UT_PAIR_RE, UT_PREFIX_TO_GCP   # hoist import to module top
_pm = UT_PAIR_RE.search(ut)
if _pm:
    _a = UT_PREFIX_TO_GCP.get(_pm.group(1).lower())
    _b = UT_PREFIX_TO_GCP.get(_pm.group(2).lower())
    if _a and _b and _a != _b:
        return ("map", "interregion", "inter-region egress (distinct region pair in usage_type)")
```

Move the `from region_prefix import ...` to the module's top-level imports (don't import inside the function body on every call).

### Fix (optional root — benefits all consumers)
In `scripts/ingest.py:2946-2951`, when `product/toRegionCode` decodes to the SAME region as `from` BUT the usage_type pair (`pair_to`) decodes to a DIFFERENT real region, prefer `pair_to`. This fixes the `to_region` column at the source so every downstream reader is correct, not just `transfer_direction`.

### Validation (predicate already confirmed)
```
APS3-USW2-AWS-Out-Bytes   -> interregion  ✓ (fixed)
APS3-APS3-AWS-Out-Bytes   -> unchanged (interzone)  ✓ (same region stays inter-zone)
USW2-AWS-Out-Bytes        -> unchanged (single token falls through)  ✓
<non-region text>         -> unchanged  ✓
```

### Tests
Add to `tests/test_transfer_direction.py` (or nearest): assert `transfer_direction({'usage_type':'APS3-USW2-AWS-Out-Bytes','from_region':'asia-south1','to_region':'asia-south1'})[1] == 'interregion'`, and that `APS3-APS3-...` stays `interzone`.

---

## BUG-2 (P1) — CUD fabricated for Memorystore M1 (CUD-ineligible tier)

### Symptom
ElastiCache `cache.t4g.small` → "Redis Capacity Basic M1" shows committed columns **$1.95 (1yr) / $1.25 (3yr)** against OD **$2.59**. Both should equal OD ($2.59): per `cloud.google.com/memorystore/docs/redis/cuds`, **Memorystore for Redis M1 capacity tier (< 5 GB) is excluded from committed-use discounts.**

### Root cause (reproduced)
`apply_rates.py:834-852` synthesizes `Commit1Yr/Commit3Yr = OnDemand × multiplier` for **every** SKU of a CUD-eligible service, and `Cloud Memorystore for Redis` is in `cud_eligible` (`data/cud_pct.json:16`) with multipliers 0.75 / 0.48. Arithmetic confirms: `2.59×0.75=1.94≈1.95`, `2.59×0.48=1.24≈1.25`. No M1 carve-out exists.

### Fix (data-driven, matches "cud_pct.json is single source of truth")
1. In `data/cud_pct.json`, add an exclusion regex to each Memorystore-Redis entry (`"Memorystore"`, `"Cloud Memorystore"`, `"Cloud Memorystore for Redis"`):
```json
"Cloud Memorystore for Redis": {
  "1yr_multiplier": 0.75,
  "3yr_multiplier": 0.48,
  "cud_exclude_sku_re": "Redis Capacity (Basic|Standard) M1\\b",
  "note": "M1 capacity tier (<5 GB) is CUD-ineligible: cloud.google.com/memorystore/docs/redis/cuds"
}
```
2. Extend `load_cud_pct()` (`apply_rates.py:69`) to also return the per-service `cud_exclude_sku_re` (today it returns only the `(1yr,3yr)` tuple — widen it, or add a sibling `load_cud_exclusions()`).
3. In the synthesis loop (`apply_rates.py:841-851`), inject the exclusion into the INSERT's WHERE:
```python
excl = exclusions.get(svc)   # regex str or None
excl_clause = "AND NOT regexp_matches(gcp_sku_name, ?)" if excl else ""
params = [mult, svc] + ([excl] if excl else [])
# ... WHERE gcp_service = ? {rg_clause} {excl_clause}
```
With no synthesized commit row, `projection_view.py:393/404` `COALESCE(c1.rate_usd, od.rate_usd)` falls back to OD automatically → committed columns = $2.59. Correct.

### CRITICAL gotcha
Scope the regex to **`Redis Capacity … M1`** only. Memorystore **for Memcached** SKUs are named "Custom Core **M1**" / "Custom RAM **M1**" where `M1` is a generation label, NOT the excluded capacity tier — a blanket `%M1%` would wrongly strip Memcached's legitimate CUDs. Also must not catch eligible M2–M5. (Valkey has the same < 5 GB exclusion — add the equivalent entry when the Valkey mapping lands, see WORKORDER_best_fit_target_selector §5.1.)

### Tests
`tests/test_cud_m1_exclusion.py`: after synthesis, assert zero `Commit1Yr/3Yr` rows for a `Redis Capacity Basic M1` SKU; assert Memcached `Custom Core M1` still gets commit rows; assert `Redis Capacity Basic M5` still gets commit rows.

---

## BUG-3 (P1) — CUD silently dropped for eligible services on name mismatch

### Symptom
Committed (1yr/3yr) columns equal OD for rows that SHOULD get a discount — an eligible compute/db/cache instance row shows no CUD. (Distinct from the many *correct* OD-in-CUD rows: ineligible services, passthrough, spot — those are intended via the `DEFAULT 1.0/1.0` multiplier.)

### Root cause (by code inspection)
CUD synthesis keys on an **exact `gcp_service` name match**: the in-bill gate (`apply_rates.py:838`, `svc not in mapped_services`) and the INSERT (`apply_rates.py:848`, `WHERE gcp_service = ?`). `mapped_services` comes from `aws_li_to_gcp_li.gcp_service`; the rate rows are keyed by `gcp_sku_rates.gcp_service`. If a candidate's label in the **mapping** table differs from its label in the **rate** table (e.g. `"Cloud Memorystore for Redis"` vs `"Memorystore"`, or Cloud SQL label variants), synthesis produces **zero** commit rows and `projection_view.py:393/404` COALESCEs the committed column to OD — a discount silently not applied where it's owed.

### Fix
1. **Diagnostic gate** (do this first — it also measures the blast radius): add to `validate_fix.py` a check that lists rows where `strategy IN ('map','break_down')` AND `gcp_service ∈ cud_eligible` AND no `Commit1Yr` rate exists for the SKU. Run it against a real job DB to enumerate exactly the eligible rows missing their discount. Report as a warning with service + count (not fatal — some may be legitimately M1-excluded per BUG-2).
2. **Normalize the key:** ensure the `gcp_service` string the mappers write to `aws_li_to_gcp_li` is the SAME canonical value the rate loader writes to `gcp_sku_rates`. Prefer a single source (the `GCP_*` constants in `apply_static_mappings.py`) used by both the mapper and the rate-tagging path. Where a service legitimately has aliases, make synthesis match on a normalized/aliased set rather than one exact string.

### Guard test
`tests/test_candidate_service_names_match_rates.py`: for every CUD-eligible service, assert the `gcp_service` value(s) produced by the mappers are a subset of the `gcp_service` values the rate loader can produce — so a future rename can't silently reintroduce the drop-out.

### Note
Needs a **real job DB** to quantify (the bundled `tests/corpus/*` DBs are synthetic fixtures with empty `gcp_sku_rates`, so they can't measure this). The diagnostic in step 1 is how you confirm scope before/after the fix.

---

## Verification (all three)
1. `python3 -m pytest tests/ -v` from `skill/aws-gcp-cost-projection/` — existing + new tests pass.
2. Re-run the reference bill: the `APS3-USW2` row prices as inter-region (not 100% passthrough, not inter-zone label); the `cache.t4g.small` M1 row shows committed = OD; the BUG-3 diagnostic lists any remaining eligible-but-undiscounted rows.
3. Any rate pinned from a page cites its official URL in a comment (AGENTS.md).
