# WORK ORDER — Pipeline hardening (watch-list items #2–#6)

**For an implementing AI / engineer. Each item is independent; do in any order, stop/report
per item. These span classify / pricing / report / ingest — each item says where it lives.**

Project pricing basis (applies throughout): **compare AWS On-Demand LIST vs GCP On-Demand
LIST** (do not use commitment-discounted amounts for the headline comparison).

---

## H-2 — Match instance types against the authoritative list, not hardcoded regex
**Lives in:** classify / family path.
**Problem:** rules detect instance families with hardcoded regex (`m5`, `c6g`, …). A brand-new
family or renamed scheme the regex never saw → falls to misc.
**Fix:** we already have `data/ec2-instance-types.json` (629 real types from the AWS Price
List, refreshable). Use it as the ORACLE: to decide if a token is an instance type (and to
read its family / arch / vcpus / accelerator), look it up in that table first; only fall back
to the regex for types not yet in the file. Keep `refresh_ec2_specs.py` (or equivalent) as
the refresh that keeps the list current.
**Test:** a known type (`m5.xlarge`) and a newly-added one both resolve via the table; an
unknown token does NOT false-match. Both classifier suites stay green.

---

## H-3 — Discounts summary at the bottom (comparison stays list-vs-list)
**Lives in:** report layer (`render_report.py` / `export_report.py`).
**Decision (confirmed):**
- The HEADLINE per-service comparison is **AWS on-demand LIST vs GCP on-demand LIST** (fair).
- Do NOT show discounts per line. Add a **"Discounts by service"** block at the BOTTOM that
  sums each service's actual discounts (RI/SP/EDP/private/credits).
- Show the customer's **net AWS total = list total − discounts** below the comparison, as
  context ("what you actually pay AWS today"), clearly labeled so it isn't confused with the
  list-vs-list comparison.
**Source of discount amounts:** from the bill — commitment/credit/EDP line items already
classified as `commitment_discount` / `negative_cost` / `non_workload`, grouped by service.
**Test:** a bill with SP/EDP discounts renders: (a) list-vs-list per service, (b) a
discounts-by-service block, (c) a net-AWS-total line = list − discounts.

---

## H-4 — Global / multi-region rate fallback
**Lives in:** pricing (`projection_view.py` / `apply_rates.py`).
**Problem:** global/multi-region rows (CloudFront, Route 53, Shield, cross-region) have no
single region, so GCP rate lookup falls back arbitrarily.
**Fix — fallback chain for the GCP rate:**
1. GCP `global` rate for that SKU if it exists;
2. else the **region that appears most across the bill** (the customer's dominant region);
3. (nice-to-have, later) the region where the dependent services run.
Compute the bill's dominant region once and pass it to the view. Note the chosen basis in
the row's `projection_note` ("global charge; priced at <region> rate").
**Test:** a global row with no global SKU prices at the bill's most-common region, with a
note saying so.

---

## H-5 — Loud failure for garbage / detail-less rows
**Lives in:** classify + report.
**Problem:** a row with real cost but every other field blank (bad PDF/export) → misc with
nothing to map; risk of a fabricated GCP number.
**Fix:** when a row has `aws_amortized_cost > 0` but no usable detail (no usage_type /
operation / unit / instance info), force `strategy='passthrough'` at cost AND record it in
`data_gaps.json` + surface it LOUDLY in the report ("$X could not be classified — source bill
lacked line-item detail"). Never let such a row become a non-passthrough GCP estimate.
**Test:** a cost-only blank row → passthrough + appears in the data-gaps/loud-failure output;
never a mapped non-zero GCP cost.

---

## H-6 — Multi-currency: detect, and render GCP in the bill's currency
**Lives in:** ingest + report.
**Problem:** a non-USD bill (EUR, etc.) is assumed USD; GCP is priced in USD only.
**Fix:**
1. **Ingest:** detect the bill's currency (column/symbol; prefer the bill's own stated FX
   rate if present). Store the currency + rate.
2. **Report:** convert GCP USD costs into the bill's currency using that rate, so BOTH sides
   render in the customer's currency. Label the rate used.
Need a live FX source as a fallback when the bill doesn't state a rate.
**Test:** a EUR bill renders AWS and GCP both in EUR; the FX rate used is shown.

---

## Report back (per item)
Files changed, the test added, and anything that needed a decision you couldn't make.
