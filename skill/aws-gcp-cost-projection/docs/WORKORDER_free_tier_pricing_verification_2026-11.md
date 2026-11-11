# WORK ORDER — Free-tier / `$0` (ignore) decisions: live-pricing verification + fixes (2026-11)

**For an implementing AI / engineer with NO prior context. Read fully. Each item is
self-contained with a concrete test. This work order comes from a live spot-check of
every `$0`/`ignore` decision against the provider's OFFICIAL pricing pages (per
`AGENTS.md`: the official page is the tiebreaker and wins over catalog/code/notes).**

## How this was verified (method + evidence)

Verified **2026-11-11** by rendering the official Google Cloud pricing pages in a
browser (WebFetch returns only the page title — these pages are JS-rendered — so a
plain fetch is NOT sufficient; render them). Pages read:

- Cloud Monitoring + Logging: `https://cloud.google.com/stackdriver/pricing`
  (redirects to `.../products/observability/pricing`)
- Cloud SQL: `https://cloud.google.com/sql/pricing`
- Filestore: `https://cloud.google.com/filestore/pricing`
- Network Connectivity (incl. NCC hub/spoke): `https://cloud.google.com/network-connectivity/pricing`

## ✅ CONFIRMED correct — DO NOT touch (recorded so a future audit need not re-check)

| Decision (code) | Live page (2026-11-11) |
|---|---|
| Cloud Logging ingestion: 50 GiB/project/mo free, then $0.50/GiB | ✓ exact |
| Cloud Logging retention: $0.01/GiB-mo beyond 30-day default | ✓ exact |
| Log Router / routing / export → $0 | ✓ "No additional charge" |
| Vended network logs storage: $0.25/GiB | ✓ exact |
| Monitoring metrics free tier + tiers: 150 MiB free, then $0.2580 / $0.1510 / $0.0610 per MiB | ✓ exact |
| Monitoring scalar datapoint = 8 bytes (custom-metrics conversion) | ✓ exact |
| Dashboards → $0 | ✓ not a billable line item |
| Aurora/RDS per-I/O → $0 (Cloud SQL has no per-I/O-operation charge; I/O is in storage price) | ✓ confirmed |
| EFS throughput → $0 (Filestore **Basic** bundles IOPS/throughput into per-GiB capacity; "Per IOPS $0") | ✓ confirmed |

---

## ISSUE 1 — CloudWatch **Alarms → $0** is a time-bomb (alerting becomes chargeable 2027-09-01) 🔴
**Lives in:** `scripts/apply_static_mappings.py`, `map_cloudwatch()`, `is_alarm` branch (~line 6118).
**Current:** `strategy='ignore'`, note "Cloud Monitoring alerting policies included free".
**Live page:** alerting is free **today**, but Google will begin charging for alerting
**no sooner than 2027-09-01**: **$0.35/month per metric reference in an alerting policy**
+ **$0.50 per 1,000,000 points** returned by a metric alerting-policy condition.
(Existing commit contracts as of 2026-05-31 are exempt until they expire/renew.)
**Verdict:** `$0` is CORRECT for bills dated before 2027-09-01; WRONG after.
**Immediate fix (DONE — keep `strategy='ignore'`):** reword the note to a **dated caveat**
that names the 2027-09-01 change, the rates, and that it must be re-verified / re-modeled
before then; cite the URL.
**Deferred (future work):** when the provider date passes (or a bill's usage month is
≥ 2027-09, which this pipeline can detect from the billing period), switch alerting from
`ignore` to a priced `map`: `$0.35 × (metric references)` — note the AWS CUR exposes alarm
*count*, not "metric references per policy", so this needs a disclosed 1:1 assumption.
**Test:** an alarm row still maps `strategy='ignore'`; its `projection_note` contains
"2027" and "$0.35". (Existing `tests/test_static_mappers.py` alarm assertion stays green.)

## ISSUE 2 — CloudWatch **API requests → $0** is now stale (read API chargeable since 2025-10-02) 🟡
**Lives in:** `scripts/apply_static_mappings.py`, `map_cloudwatch()`, `is_api_requests` branch (~line 6135).
**Current:** `strategy='ignore'`, note "Cloud Monitoring API calls have no billable SKU".
**Live page:** **write** API calls are free, but **read** API calls are charged
**$0.50/million time series returned**, first **1,000,000 time series/billing-account/month free**,
effective **2025-10-02**. So "no billable SKU" is no longer true.
**Verdict:** magnitude is tiny and the units don't align (AWS bills per-1,000-requests;
GCP bills per-time-series-returned on reads only), so a clean conversion isn't available.
**Immediate fix (DONE — keep `strategy='ignore'` as a disclosed free-tier assumption, mirroring
custom-metrics):** reword the note to state writes are free, reads are chargeable since
2025-10-02 at $0.50/M time series (first 1M/account free), that typical control-plane call
volumes sit within the free allotment, and that the AWS-requests→GCP-time-series unit
mismatch prevents a precise conversion; cite the URL. Lower confidence to ~0.55.
**Deferred (future work):** if read-API volume is material, model it against the real SKU
once a requests→time-series-returned assumption is agreed.
**Test:** API-requests row still `strategy='ignore'`; note contains "read" and "2025-10-02".

## ISSUE 3 — **Transit Gateway data-processing → $0** hides NCC charges (unstated architecture assumption) 🔴
**Lives in:** `scripts/apply_static_mappings.py`, the `transitgateway-bytes`/`tgw-bytes`
branch (~line 4139).
**Current:** `strategy='ignore'`, note "VPC network spoke traffic has no transit fee on GCP;
only standard egress applies".
**Live page (Network Connectivity → Hubs and spokes):** the TGW analog, **NCC**, charges:
- **Hub:** no charge.
- **Spoke hours (VPC / producer VPC spokes):** **$0.10/hour** (≈ $73/mo per VPC spoke).
- **Spoke hours (Cloud Interconnect / Cloud VPN / Router-appliance spokes):** $0.075/hour.
- **Advanced Data Networking (ADN):** a **per-GB data-processing fee** for traffic sent
  from a spoke (applies to traffic originating from VPC spokes).
**Verdict:** `$0` is only right if the GCP target architecture uses plain **VPC Peering**
(which is free — no data-processing fee), NOT NCC. That is an **unstated assumption**, not a
universal truth; it understates cost for any design that actually uses NCC.
**Immediate fix (DONE — keep `strategy='ignore'`):** reword the note to state the explicit
**VPC-Peering assumption** and name the NCC charges that would apply otherwise (spoke-hours
$0.10/hr per VPC spoke + ADN per-GB data processing); cite the URL. Lower confidence to ~0.60.
**Deferred (future work — needs a user decision):** optionally model the NCC path — spoke
hours (per attached VPC spoke) + ADN data processing per GB — gated on an
architecture/assumption input ("peering" vs "NCC"). Flag for review; do not fabricate a VM
or spoke count.
**Test:** TGW-bytes row still `strategy='ignore'`; note contains "VPC Peering" and "NCC".

## ISSUE 4 — Metrics free tier is **per billing account**, not "per project"; per-row zeroing can under-project 🟡
**Lives in:** `scripts/apply_static_mappings.py`, `is_custom_metrics` branch, the low-volume
`ignore` note (~line 6213) and the comments above it (~6027, ~6165).
**Current:** comments/notes say "first 150 MiB/project/month free".
**Live page:** the chargeable-metrics free allotment is **"First 150 MiB per billing account"**
— a single account-wide pool, not per-project.
**Consequence:** the low-volume `$0` decision is made **per row**. With one account-wide
150 MiB pool, many individually-sub-threshold custom-metric rows can **collectively** exceed
the free tier, so blanket per-row `$0` can under-project on metric-heavy bills.
**Immediate fix (DONE):** correct "per project" → "per billing account" in the comments and
the `ignore` note, and add one sentence disclosing the per-row-vs-account-pool limitation;
cite the URL.
**Deferred (future work):** aggregate estimated custom-metric MiB across all metric rows in
the bill and compare the TOTAL against the single 150 MiB account free tier before zeroing
(same idea the log-ingestion path already gets for free via `apply_rates` tiered blending
across the SKU's total usage).

---

## Scope of the immediate fixes (this pass)
All four `strategy` values are **unchanged** (`ignore` stays `ignore`) — the existing
`tests/test_static_mappers.py` assertions (`strategy == "ignore"`) stay green. Only the
`projection_note`/comment text and `mapping_confidence` change: accurate rates, dated
caveats, explicit assumptions, and URL citations (per `AGENTS.md`). The deeper modelling
(priced alerting post-2027, volumetric read-API, NCC data-processing, account-wide metric
free-tier aggregation) is documented above as deferred work, each needing a modelling
decision rather than a mechanical edit.

## Report back
Files changed, which notes/confidences were updated, and confirm the suite is green.
