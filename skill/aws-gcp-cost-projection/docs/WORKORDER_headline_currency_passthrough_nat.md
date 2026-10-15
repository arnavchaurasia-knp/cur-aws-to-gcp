# WORK ORDER — Headline correctness: currency, passthrough/unpriced rows, region fallback, NAT

**For an implementing AI / engineer with NO prior context. Read fully. This fixes the
numbers the customer actually sees — the headline AWS-vs-GCP comparison, the gates that
validate it, and two mis-priced services. Four DIRECTIVES (decided by the product owner,
implement exactly as written) + medium/low hardening. Do the directives in order; each has
its own tests. Stop and report after the four directives before starting the medium items.**

## System context (what you're touching)
AWS Cost & Usage Report → GCP cost projection. Per-job DuckDB. Pipeline:
ingest → classify → map (per-row AWS→GCP) → merge → projection_view (computes
`gcp_projected_cost`) → validate_fix (gates) → render_report / export_report.
Key tables: `aws_li_catalog` (AWS rows), `aws_li_to_gcp_li` (mappings),
`gcp_projection` (view joining them with rates), `run_results`.
Row strategies: `map` (real GCP SKU+rate), `passthrough` (carry AWS cost 1:1, no GCP
equivalent), `ignore` ($0 — commitment/discount/non-workload), `outlier_triage` (flagged).

## Project rules (non-negotiable; a violation is a bug)
- Pricing basis = **AWS On-Demand LIST vs GCP On-Demand LIST** (never discounted/amortized AWS vs GCP list).
- **Never fabricate a GCP number or a ratio with no public basis.** Unpriced/unmappable → loud passthrough at AWS cost, never a silent $0.
- Both sides of every displayed total must **foot** (grand total = sum of shown rows) and use the **same inclusion rule**.

## Test command (from `skill/aws-gcp-cost-projection/`)
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
All existing tests must stay green. Add the new tests each section specifies. Line numbers
below are approximate (post-diff working tree) — anchor on function names and verify.

---

# DIRECTIVE 1 — One currency model: normalize to USD internally, render in the bill's currency

**Decision:** Do **NOT** fetch GCP rates in the bill's currency. GCP's catalog is USD and
that is how clouds actually bill (price in USD, convert the invoice at a stated FX rate).
Use a **single FX normalization point** so every internal computation is in USD and only the
final display converts. This is the robust, global choice.

**Problem today:** H-6 kept native (non-USD) amounts in the DB but only `render_report` /
`export_report` are currency-aware. `projection_view.py` and `validate_fix.py` compute on raw
`aws_amortized_cost` with no conversion → for an INR/EUR bill the GCP-cheaper/costlier verdict
and the ratio/outlier/passthrough-budget gates are off by the FX rate (~83× for INR). Also
`ingest._detect_foreign_currency` now treats a bill mixing USD + one foreign currency as fully
foreign (`len(codes) > 1` guard was dropped).

**Implement:**
1. **Ingest (`ingest.py`):** at the single point where rows are finalized, store THREE things
   per bill: the native currency code, the FX rate to USD (prefer the bill's own stated rate;
   else a live FX source; else fail loud — do not assume 1.0 for a non-USD bill), and keep
   `aws_amortized_cost` **in USD** (convert native→USD once, here). Keep the native amount in a
   separate column (e.g. `aws_amortized_cost_native`) for display/audit. Everything downstream
   reads the USD column and needs no FX knowledge.
2. **Restore the mixed-currency guard:** if the bill contains USD rows AND a foreign currency,
   do NOT blanket-label it foreign. Convert each row by its own line currency if present;
   if the bill is genuinely single-foreign-currency, convert the whole bill at its rate. Never
   relabel USD rows as foreign.
3. **projection_view.py / validate_fix.py:** no change needed once the stored cost is USD —
   but ADD an assertion/comment that these operate in USD, so the invariant is explicit.
4. **render_report.py / export_report.py:** convert BOTH AWS and GCP USD figures into the
   bill's currency at the stored FX rate, label the rate used ("converted at 1 USD = 83.2 INR").
   Remove any per-layer ad-hoc FX so there is exactly one conversion, at render.

**Tests:** (a) an INR bill: internal `gcp_projection`/gate math is in USD and the verdict sign
is correct; the report renders both sides in INR with the rate shown. (b) a bill mixing USD +
EUR rows: USD rows stay USD, EUR rows convert; no blanket relabel. (c) a non-USD bill with no
stated rate and no FX source → loud failure, never a silent 1.0.

---

# DIRECTIVE 2 — Restructure the report into clean sections so passthrough/unpriced rows never fake a saving

**Decision (product owner's structure):** the report is three stacked sections, each footing
on its own, so no row category distorts another:

```
SECTION A — MAPPED (real GCP equivalent)     Σ AWS(mapped)      vs  Σ GCP(mapped)   ← HEADLINE savings %
SECTION B — PASSTHROUGH (no GCP equivalent)  Σ AWS(passthrough)  =  Σ GCP(passthrough)  (carried 1:1, cost-neutral)
SECTION C — DISCOUNTS (bottom)               net AWS = Σ AWS(list) − Σ discounts
```

**The bugs this fixes:**
- Headline AWS switched to `aws_gross` (positive rows only) while GCP grand total still summed
  ALL rows incl. negative passthrough → GCP understated, totals don't foot.
- `outlier_triage` rows and map-rows with an unresolved rate return NULL → summed as $0 on the
  GCP side while their AWS cost stays in the baseline → **fabricated savings** (a $20k service
  that fails SKU resolution shows as a $20k "saving").
- The "GCP On-Demand" card compares workload-only GCP against all-positive AWS (incl.
  non-workload passthrough) → overstated savings; card disagrees with grand total.

**Implement (rules for EVERY total — headline card, grand total, export):**
1. **Section A headline = `strategy='map'` rows only, positive cost, `is_workload=true`, with a
   resolved GCP rate — BOTH sides filtered identically.** This is the savings comparison.
2. **Unpriced rows are NOT allowed in Section A.** Any `map` row whose rate did not resolve, and
   any `outlier_triage` row, is **reclassified to passthrough** (carry AWS cost 1:1) and shown in
   Section B — never summed as $0 GCP in the headline. (This is the honesty rule; coordinate with
   projection_view's CASE arms so an unresolved-rate `map` row yields passthrough cost, not NULL.)
3. **Section B (passthrough) = AWS cost carried 1:1 to GCP**, so AWS==GCP for these rows → they
   contribute **zero** to the savings delta and cannot fake a gain or loss. Show them in their own
   block, excluded from the Section-A headline %. Label: "No direct GCP equivalent — carried at AWS
   cost." Non-workload passthrough (Marketplace/Support/Tax) belongs here too, out of the headline.
4. **Section C (discounts)** stays the existing bottom block; `net AWS = list − discounts`.
   Keep the existing try/except fallback to `aws_discounts` for variant schemas.
5. **Footing invariant:** assert in a test that every grand total equals the sum of its section's
   shown rows, on BOTH sides. No category silently in one sum but not the other.

**Tests:** (a) bill with a $20k unresolved-rate row → it appears in Section B (passthrough,
AWS==GCP), NOT as a $20k headline saving. (b) bill with −$8k passthrough credits → AWS and GCP
headline both exclude them; grand total foots to displayed rows. (c) non-workload Marketplace
spend → Section B, excluded from the savings %. (d) the GCP card and the grand-total row agree.

---

# DIRECTIVE 3 — Region rate fallback: never pick the cheapest region

**Decision:** confirmed. **Problem:** `projection_view.py` (~L67/L132/L146) region-rate fallback
was changed `MAX(rate_usd)` → `MIN(rate_usd)`, so a multi-region SKU with no row-region/global
rate now prices at the **cheapest** region — a bias in GCP's favor.

**Implement — neutral fallback chain (no min/max cherry-pick):**
1. the SKU's rate in the row's own region; else
2. the SKU's `global` rate; else
3. the rate in the **bill's dominant region** (most common across the bill — this value is
   already computed for the global-fallback path; reuse it); else
4. if still none, treat as **unpriced → passthrough** (Directive 2 rule), not an averaged or
   min/max guess.
Record the chosen basis in `projection_note` ("priced at <region> rate — no <row-region> rate").

**Test:** a multi-region SKU with no row-region/global rate prices at the bill's dominant region
(not the global minimum), and the note says so; a SKU with no rate anywhere → passthrough.

---

# DIRECTIVE 4 — NAT: use GCP's real per-gateway cap, drop the fleet-VM multiplier

**Decision (grounded in GCP's published Cloud NAT pricing, verified 2026-10-14):** GCP Cloud NAT
gateway fee = $0.0014/VM/hr **up to 32 VMs, then capped at $0.044/hr per gateway**; data
processing = $0.045/GiB (same as AWS $0.045/GB); AWS NAT gateway = $0.045/hr. So at the cap GCP
is within ~2% of AWS — **parity falls out of the real ceiling, no tuned ratio needed.**

**Problem today (`apply_static_mappings.py`, map_flat_hourly public-NAT branch ~L2408-2424 /
~L2605-2647):** gateway-hours priced as `$0.0014 × (ALL compute VMs in bill ÷ estimated
gateways)` with **no 32-VM cap** → e.g. 500 VMs behind 1 gateway = ~15× over-projection. The VM
count is the whole fleet, not VMs behind that gateway. This is a fabricated multiplier (the work
order had deferred NAT pending this decision).

**Implement:**
1. **Gateway-hours →** price at the **$0.044/hr cap** (GCP's ≥32-VM ceiling). Resolve the real
   catalog SKU for the per-gateway uptime at/with that rate (the `Networking … Nat Gateway
   Uptime` desc patterns match the catalog — verify). This never over-projects GCP (for <32 VMs
   real GCP is cheaper; using the cap is the safe, slightly-conservative direction) and lands at
   AWS parity. Drop the fleet-VM multiplier entirely.
2. **Data processing (per-GB) →** keep the existing 1:1 mapping at $0.045/GiB (already correct).
3. **(Optional, later — do NOT build now)** if actual VMs-behind-gateway becomes available, use
   `min(vms_behind_gateway × $0.0014, $0.044)` for precision. For now the cap is the global rule.
4. Label `projection_note`: "Cloud NAT gateway priced at the 32-VM cap ($0.044/hr) — AWS parity;
   per-VM detail not modeled." Confidence can stay moderate; it is now a real basis, not a guess.

**Test:** a NAT row with 500 fleet VMs behind 1 gateway → gateway cost ≈ $0.044/hr × hours (NOT
500× that); within ~5% of the AWS gateway cost; data-processing row still 1:1. No dependence on
fleet VM count.

---

# MEDIUM (do after the four directives; separate report-back)

**M-1 — Shield Advanced de-duped across the whole bill** (`apply_static_mappings.py` map_shield
~L4318/L4392). `enterprise_sub_priced` prices only the FIRST monthly subscription row at
$3,000/mo and sets the rest to `ignore` → a 3-month bill projects $3,000 instead of $9,000. Fix:
price each distinct billing-month's subscription row; don't dedupe across months. Tighten the
`"fee"`/`"monthly"` substring detector. Test: 3-month Shield bill → $9,000.

**M-2 — `billing_month` in the row md5 key + non-ISO date parsing** (`ingest.py`). (a) The md5
`aws_li_key` now includes `billing_month` for all formats, so multi-month bills split each
service into one row per month and resume artifacts keyed on the old hash won't match — verify
merge temp-files/resume regenerate against the same DB, and correct the two now-false
"byte-identical to before" comments. (b) `LEFT(CAST(date AS VARCHAR),7)` assumes ISO
`YYYY-MM`; a `MM/DD/YYYY` column yields `07/15/2` as the month bucket, fragmenting keys and the
dedup GROUP BY. Fix: parse the date to a real date before taking the month; handle `MM/DD/YYYY`.
Test: a `MM/DD/YYYY` CSV → correct `YYYY-MM` buckets; a multi-month bill resumes without key
mismatch.

**M-3 — "GCP On-Demand" summary card uses an inconsistent baseline** (`render_report.py`
~L474-479). `gcp_od`/`gcp_1yr`/`gcp_3yr` are `WHERE is_workload` (workload-only GCP) but the
card's AWS baseline is `aws_gross` (all positive rows, incl. non-workload Marketplace/Support,
which the audit showed can be $1.8M). So the card overstates GCP savings by the non-workload
amount, and it disagrees with the GRAND TOTAL row (which correctly uses `gcp_grand_od` =
workload+non-workload). Fix under Directive 2's rule: the card compares **like with like** —
either workload-only on both sides, or all-positive on both sides — never mixed. Pick the
Section-A basis (mapped, workload, priced) so the card and the headline agree. Test: the card's
AWS baseline and GCP number use the same row filter; card % equals the Section-A headline %.

---

# LOW (hardening; batch into the medium report-back)

- **L-1 `connect`/`mq` substring misroute** (`llm_guidance.py` ~L189-194, `_MISC_CONTEXT` loop):
  raw substring + first-match means an "AWS Direct Connect" row that falls to `misc` maps to
  "Contact Center AI Platform". Require word-boundary/exact-canonical match for keys ≤4 chars
  (same fix class as the `aws_normalizer` alias bug). Test: "AWS Direct Connect" in misc → not
  Contact Center AI.
- **L-2 `map_managed_db` returns a bare list, not `(out, llm_rows)`** like map_compute_breakdown
  (`apply_static_mappings.py` ~L5038-5052; manifest prune ~L8617 removes all managed_db keys).
  Non-ACU rows that fail to parse get no mapping AND are pruned from the LLM pass → they fall to
  the coverage backstop (passthrough) instead of a real Cloud SQL projection. Cost isn't lost,
  fidelity is. Fix: return the tuple and re-inject unmapped managed_db rows into the LLM manifest
  group, mirroring the compute path. Test: an unparseable RDS row stays in the LLM manifest.
- **L-3 Memory GB→GiB `×0.931`** (`apply_static_mappings.py` L9 `GB_TO_GIB`, applied to
  Fargate/Lambda/Autopilot memory ~L2869/L3053). Storage conversion is correct; for MEMORY,
  confirm whether AWS bills Fargate/Lambda "GB-Hours" as decimal GB or binary GiB — if binary,
  memory should use factor 1.0, and `×0.931` under-projects memory ~7%. Decide + comment the basis.
- **L-4 `datetime.UTC`** (`render_report.py` ~L129) requires Python ≥3.11 — confirm the runtime
  floor or guard it.
- **L-5 per-row CUD cells don't foot to the CUD grand total** (render_report ~L910/L1142):
  per-row `SUM(gcp_cost_1yr_cud)` omits NULL components while the grand total COALESCEs to OD.
  Make the per-row cell COALESCE the same way.
- **L-6 detail-less autofix bare `except: pass`** (`validate_fix.py` ~L889-915): a silent partial
  UPDATE failure leaves rows un-downgraded and flips the run to exit 1 with no diagnostic. Add a
  stderr log (match the echo/no-rate autofixes which log).
- **L-7 `_RHEL_VCPU_BANDS` `next(...)` has no default** (`apply_static_mappings.py` ~L1136-1143):
  add a default so `vcpus<=0` can't raise.
- **L-8 `ensure_full_coverage.py`** — the full-body try/except (good) now also swallows the
  `executemany`, so on a schema-drift DB the backstop silently no-ops, hiding real gaps its
  "loud, not silent" contract promises. Coordinate with `WORKORDER_ensure_full_coverage.md`
  FIX-1/FIX-3: keep selection + the data_gaps/stderr reporting OUTSIDE the swallow so gaps are
  still surfaced even when the INSERT fails.
- **L-9 discount "crumbs" dropped from the net-AWS math** (`render_report.py` /
  `export_report.py` discount block, `HAVING discount_amount > 0.005` per product). Tiny
  per-product credits below the threshold are dropped, so `total_service_discounts` ≠
  `aws_discounts` and the "Net = List − Discounts" box is off by the dropped crumbs. Fix: either
  remove the per-product `HAVING` floor for the SUM used in the net total, or compute the net
  total from the authoritative `aws_discounts` figure (not the rounded per-product sum), and keep
  the floor only for which rows are *displayed*. Test: net box == list − full discount total,
  exactly, even with sub-cent per-product credits.

---

# POLICY CONFIRMATIONS (not bugs — confirm intent, then encode)

**P-1 — Neptune / QLDB / Timestream now fall to passthrough instead of GCE sizing.**
`classify_mechanics.py` (~L395-398) adds Neptune/QLDB/Timestream to the `compute_breakdown`
EXCLUSION clause but does NOT re-home them in the `managed_db` rule (~L476-509). Net effect:
their instance-hour rows (e.g. Neptune `db.r5.large`), previously priced as a GCE VM via
`_has_instance_type`, now fall through to `misc` → passthrough at AWS cost. This is plausibly
correct (no managed GCP graph/ledger/timeseries equivalent — matches the DocumentDB/Neptune→PASS
policy). **CONFIRM** passthrough (not GCE sizing) is the intended basis; if yes, add a short
comment at the exclusion site stating the policy and why, and a test asserting a Neptune
instance-hour row → passthrough (so this can't silently regress to GCE sizing later). If GCE
sizing was actually wanted, re-home them in `managed_db` instead.

---

# Explicitly OUT OF SCOPE (do not do)
- Do NOT fetch/pre-convert GCP catalog rates per currency (Directive 1 decided against it).
- Do NOT build the per-VM NAT model now (cap is the rule; per-VM is a labeled "later").
- Do NOT wire `service_specs.json` to set `mechanic_group` (that's the separate cutover order).
- Do NOT change the OD-vs-OD pricing basis or add commitment math into the headline.

# CONFIRMED CORRECT by review — DO NOT "fix" or revert these
These were inspected during the review and are genuine improvements or safe-by-construction.
Do not touch them; do not re-report them as bugs.
- **Atomic merge** (`merge_mappings.py`): DELETE+INSERT wrapped in one BEGIN/COMMIT with ROLLBACK
  on failure → re-runnable, no double-insert. Orphan-key referential check present.
- **`apply_rates.py`** transaction wrapping + PRAGMA schema guards (is_workload / pricing_unit /
  mechanic_group) for older-DB variants are well-formed.
- **Tiered-rate blending** changed `MAX(single-row usage)` → `SUM(usage)/months` — the correct
  fix for cumulative GCP volume tiers.
- **Storage GB→GiB `×0.931`** (EBS/EFS/FSx/DynamoDB capacity) is correct (decimal→binary). Only
  the *memory* application is in question — see L-3.
- **gp3 Hyperdisk-Extreme ceiling** branches flipped `gp3_like` → `(not gp3_like)` is a genuine
  FIX (the ceiling must never fire for gp3).
- **`map_commitment_discount` / `map_compute_breakdown`** correctly return `(out, llm_rows)` and
  the dispatch loop handles tuple-vs-list — the pattern L-2 asks `map_managed_db` to match.
- **`canonical_service` fallback lambda** widened to `*_a` is a real fix (prior `lambda _p` would
  crash the 2-arg calls).
- **Global-rate fallback** now prefers the dominant-region rate over a blind AVG, and
  global-fallback rows are excluded from the "reachable rate" check — improvement (distinct from
  the Directive-3 region MIN bug).
- **`family_mapper.py` → shim**: logic consolidated into `apply_static_mappings.py`; orchestrate.go
  dropping `?scripts/family_mapper.py` from Phase-2 PreLLMScripts is correct (no lost mapping).
- No inline-flag `(?i)` regexes introduced (Python 3.14 safe). No NameError/KeyError in the
  rewritten `build_guidance`. No row falls through with no group.

# WATCH (no action now — know these exist)
- **`service_specs.json` is dormant for routing.** `spec_dispatch.dispatch()` runs only inside
  `llm_guidance.build_guidance()` for rows RULES already put in `misc`, and only the charge_type's
  `llm_guidance` block is read — the spec `mechanic` field routes nothing. The big registry
  additions can only change guidance text/target for misc rows, not route a row. Wiring specs to
  own `mechanic_group` is the **separate cutover work order**, not this one.
- **`check_manifest_integrity()`** (`classify_mechanics.py`) is effectively a no-op
  (`llm_groups ∪ DETERMINISTIC_GROUPS` always covers every manifest group) — harmless, but it is
  NOT a real backstop; don't rely on it.
- **`_has_instance_type` oracle** strips `db.`/`cache.`/`kafka.` prefixes, so a DB token like
  `db.r5.large` now matches the 1315-key type table — currently shielded by the compute_breakdown
  DB-exclusion, but keep in mind if a non-excluded service ever carries such a token.
- **`marketplace_thirdparty` prefix regex** loosened `^(amazon|aws)\b` → `^(amazon|aws)\s*`
  (tightens the AWS-native exclusion). No regression found, but it's a classification-boundary
  change, not a no-op.

# Report back (per directive, then per medium/low, then P-1)
Files changed, the test added, pytest result (all existing green), and any item where you hit a
basis you couldn't confirm (especially L-3 memory GiB, and whether the NAT uptime SKU rate in the
catalog equals $0.044/hr — if the catalog rate differs, price at the catalog rate and note it).
