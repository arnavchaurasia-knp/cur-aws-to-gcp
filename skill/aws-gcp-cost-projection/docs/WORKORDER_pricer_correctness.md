# WORK ORDER — Pricer correctness fixes (apply_static_mappings / family_mapper / projection_view)

**For an implementing AI / engineer with NO prior context. Read fully. Every item below is a
DIRECT fix: a verified bug with an unambiguous correct answer and a small change. Each item is
independent — do in any order, run the test suite after each, stop/report per item.**

Test command (from `skill/aws-gcp-cost-projection/`):
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
Pricing basis for the whole pipeline: **AWS On-Demand LIST vs GCP On-Demand LIST** (do not bake
in commitment discounts). Mappers must NEVER fabricate a GCP number: if a rate can't be
resolved, emit `strategy="passthrough"` (carry AWS cost) with a note — never `strategy="map"`
with a guessed/placeholder SKU.

All line numbers are from the current files and may drift by a few lines after earlier edits —
match on the surrounding code, not the number alone.

---

## P-1 — Unify GB→GiB conversion; two mappers use the wrong constant, one uses none
**Lives in:** `scripts/apply_static_mappings.py` (EFS, Fargate memory, EBS, FSx, DynamoDB).
**Problem:** The same "AWS decimal GB → GCP GiB" conversion is implemented with THREE different
constants. The file's own S3 code (~line 2236) documents the correct one and says `1/1.024` is
wrong: "1 GiB = 1.074 GB ... (1/1.024 was wrong — that is the MiB/MB ratio, not GiB/GB.)"
- S3 (`map_object_storage`) correctly uses `1.0/1.074`.
- **EFS** (`_EFS_STORAGE_MAP` lines ~5968-5974 and `_EFS_DEFAULT_STORAGE` ~5974) uses `1.0/1.024`.
- **Fargate memory** (`map_per_request`, line ~2771: `mult = 1.0/1.024 if is_memory else 1000.0`)
  uses `1.0/1.024`.
- **EBS capacity + snapshots** (`map_block_storage`, the capacity/snapshot entries, `unit_multiplier: 1.0`
  at ~line 3699 and the snapshot desc ~3636) apply NO conversion at all.
- Check **FSx capacity** (~6150) and **DynamoDB storage** (`map_dynamodb_storage` ~4233) for the
  same `1.0` omission and fix them the same way.
**Fix:**
1. Add one module-level constant near the top of the file:
   `GB_TO_GIB = 1.0 / 1.074  # AWS decimal GB-Mo → GCP GiBy.mo (1 GiB = 1.074 GB)`
2. Replace every `1.0/1.024` and every storage `unit_multiplier: 1.0` that maps an AWS **GB-Mo**
   quantity onto a GCP **GiBy.mo**-priced SKU with `GB_TO_GIB`.
   - BEFORE changing a `1.0`, confirm the target GCP SKU is actually priced per **GiBy** (PD /
     Hyperdisk capacity, snapshot storage, Filestore all are). If a SKU is genuinely priced per
     decimal GB, leave it `1.0` and add a one-line comment saying why.
3. Do NOT touch the Fargate CPU branch (`1000.0` mCPU conversion) — that's unrelated.
**Impact if unfixed:** ~5% over-projection on EFS, ~5% on Fargate memory, ~7% on EBS (the single
largest storage line on most bills).
**Test:** a 1000 GB-Mo EBS gp3 row projects ~931 GiBy, not 1000; an EFS Standard 1000 GB-Mo row
uses `1/1.074`. Add one assertion per family. Existing suites stay green.

---

## P-2 — flat_hourly emits `strategy="map"` even when no SKU resolved, plus a fabricated default
**Lives in:** `scripts/apply_static_mappings.py`, `map_flat_hourly` (~line 2615) and the default
constants (`FLAT_HOURLY_DEFAULT_SERVICE` / `FLAT_HOURLY_DEFAULT_DESC`, ~393-394, used ~2545).
**Problem (two parts):**
(a) The emitted entry hardcodes `"strategy": "map"` (line ~2620) regardless of whether
`resolve_sku()` returned a SKU; `gcp_sku_id` is only attached inside `if sku_id:`. Every OTHER
mapper uses `"map" if sku_id else "passthrough"`. So flat_hourly emits confident "map" rows with
no rate (relying on a downstream autofix), and — worse — when `resolve_sku` word-overlap-matches
the WRONG SKU it produces a confident priced row the autofix can't catch.
(b) The catch-all default maps any unmatched `*-Hours` row to Compute Engine / "Other Hourly
Charge". This is the same failure mode the file's own IPAM comment (~2504) calls "the single
largest distortion in the entire report". Example: product "AmazonVPC", usage_type
"APS3-PrivateLink-Hours" → no table match → "Other Hourly Charge" → a coincidental Compute Engine
SKU at `strategy="map"`.
**Fix:**
1. Change line ~2620 to `"strategy": "map" if sku_id else "passthrough"`.
2. For the generic/unmatched default path, do NOT emit a fabricated Compute Engine SKU. Emit
   `strategy="passthrough"` at AWS cost with `projection_note="unrecognized hourly charge — no
   GCP SKU match"` and a low confidence (≤0.3), so it carries the real AWS dollars and is visible
   as unmapped rather than silently priced against a coincidental SKU. Keep the existing targeted
   exceptions (IPAM, Cloud Armor enrollment) exactly as they are.
**Impact if unfixed:** silent mispricing on any new/unrecognized hourly service (the IPAM 37x
class of bug).
**Test:** an unrecognized `*-Hours` row → `strategy="passthrough"`, no fabricated `gcp_sku_id`; a
matched row that resolves a SKU still → `strategy="map"`.

---

## P-3 — Aurora Serverless v2 ACU→RAM multiplier is ~2x too high
**Lives in:** `scripts/apply_static_mappings.py`, `map_managed_db` Aurora Serverless v2 branch
(PostgreSQL ~4751/4763, MySQL ~4796/4808).
**Problem:** The code encodes 1 ACU → `2.0` vCPU + `4.0` GiB RAM. AWS's definition is
**1 ACU ≈ 2 GiB of memory** (plus a corresponding, small CPU slice). So the RAM multiplier is
~2x too high. The docstring is internally inconsistent ("4 GiB" in one line, "7.5 GB" in another),
a sign the figure was never sourced.
**Fix (both multipliers — researched, decided):**
- **RAM** `unit_multiplier`: `4.0` → `2.0` (AWS: 1 ACU ≈ 2 GiB memory). Both PostgreSQL + MySQL branches.
- **vCPU** `unit_multiplier`: `2.0` → `0.25` (AWS does NOT allow custom vCPU for Aurora Serverless
  v2 — ACU is memory-defined and CPU follows; there is no official ratio. The best-sourced data
  point is 16 ACU ≈ 32 GiB + 4 vCPU, which matched a db.r5.xlarge → **0.25 vCPU/ACU**, consistent
  with r5's 1 vCPU : 8 GiB shape). Both branches.
- Update the docstring to one sourced statement: "1 ACU = 2 GiB memory + ~0.25 vCPU (empirical:
  16 ACU ≈ 32 GiB/4 vCPU ≈ db.r5.xlarge; AWS does not expose a custom vCPU for Serverless v2)."
  Add `projection_note` labelling this as an assumption.
**Impact if unfixed:** every Aurora Serverless v2 bill over-projects — RAM ~2x AND vCPU ~8x — at
confidence 0.65 (reads as trustworthy).
**Test:** a 1000 ACU-hr Aurora Serverless v2 row emits a RAM component with multiplier 2.0 and a
core component with multiplier 0.25.

---

## P-4 — DynamoDB PITR detection reads the wrong field → 100% of PITR rows mispriced
**Lives in:** `scripts/apply_static_mappings.py`, `map_dynamodb_storage` (~line 4237).
**Problem:** `is_pitr = "pitr" in (r.get("operation") or "").lower()`. But PITR rows are identified
by **usage_type** `TimedPITRStorage-ByteHrs` (see `classify_mechanics.py:924`); the `operation`
field is usually blank. So `is_pitr` is almost always False and every PITR (continuous-backup)
row is priced at the regular table-storage SKU/rate instead of the Point-in-time-Recovery storage
SKU.
**Fix:**
1. Build a blob `blob = (usage_type + " " + operation).lower()` and test
   `is_pitr = "pitr" in blob or "timedpitrstorage" in blob`.
2. Fix the cosmetic label mismatch: the entry stores `gcp_service="Firestore"` (~4251) but
   resolves the SKU under `"Cloud Firestore"` (~4241) — make the stored `gcp_service` match the
   service the SKU was resolved under.
**Impact if unfixed:** wrong SKU/component on 100% of DynamoDB PITR rows.
**Test:** a row with usage_type `APS3-TimedPITRStorage-ByteHrs`, empty operation → routed to the
PITR storage SKU with component `pitr-storage`.

---

## P-5 — Shield prices EVERY routed row at flat $3,000/mo
**Lives in:** `scripts/apply_static_mappings.py`, `map_shield` (~line 4191).
**Problem:** Every row routed to `map_shield` is emitted as Cloud Armor Enterprise Annual
Subscription, `unit_multiplier=1.0`, `strategy="map"` — with no discrimination between the Shield
Advanced subscription row, Shield Advanced data-transfer-out (DTO) fees, per-protected-resource
rows, or free Shield Standard lines. Any bill with more than one Shield line multiplies or
fabricates the $3k subscription.
**Fix:** only the **subscription** row (usage_type/operation indicating the monthly Advanced
subscription fee, e.g. contains "Shield" + "Monthly"/"Subscription"/"Fee") maps to the $3k Cloud
Armor Enterprise SKU. For all other Shield rows:
- DTO / data-processing rows → route to the data-transfer treatment (passthrough or egress SKU as
  appropriate), NOT the subscription SKU.
- Shield Standard / $0 rows → `strategy="ignore"` ($0, free on both clouds).
Mirror the dedup discipline already used in `map_flat_hourly`'s Cloud Armor enrollment branch
(~2462) so the $3k fee is emitted at most once per bill.
**Impact if unfixed:** $3k fabricated/multiplied on any multi-line Shield bill.
**Test:** a bill with a Shield subscription row + a Shield DTO row → exactly one $3k subscription
line; the DTO row is not priced at $3k.

---

## P-6 — MSK ARM brokers silently mapped to x86 (missing `r6g`, newer Graviton)
**Lives in:** `scripts/apply_static_mappings.py`, `map_msk` (~line 6543).
**Problem:** `is_arm_sustained = itype.split(".")[0] in ("m7g","m6g","r7g","c7g","c6g")` omits
`r6g` (and future `m8g`/`r8g`/`c8g`). MSK supports `r6g` brokers, so an `kafka.r6g.xlarge` broker
falls to the `else` branch and is priced as x86 **N4D** — crossing the ARM→x86 instruction-set
boundary with no disclosure, violating the file's own ARM-safety rule.
**Fix:** derive ARM from the family token instead of a hardcoded set: a family is ARM/Graviton
when its generation suffix ends in `g` (e.g. `m6g`, `r6g`, `c7g`, `m8g`) — e.g.
`fam = itype.split(".")[0]; is_arm_sustained = bool(re.match(r"^[a-z]+\d+g", fam))`. Keep the
ARM-disclosure note that the other ARM branches emit. (If a shared `family_arch()` helper is
introduced later, use it here — see architecture notes.)
**Impact if unfixed:** wrong architecture + silent, undisclosed on real Graviton MSK fleets.
**Test:** `kafka.r6g.large` resolves to an ARM GCP family with the architecture-review note;
`kafka.m7g.large` still works; an x86 `kafka.m5.large` is unchanged.

---

## P-7 — OpenSearch compute-optimized `.search` nodes shop against the general pool
**Lives in:** `scripts/apply_static_mappings.py`, `map_opensearch` (~line 6803).
**Problem:** Only ARM / burstable / else branches exist; a compute-optimized x86 family
(`c5/c6i/c7i.*.search`) lands in the else branch and is swept against GCP **general-purpose**
(N4D, `workloads=("general",)`), never the compute line (C4D). This is the opposite of what
`map_compute_windows` deliberately does (it scopes `c*` sources to `workloads=("compute",)`).
**Fix:** before the general `else`, detect a compute-optimized source family (first token matches
`^c\d`) and call the cheapest-in-scope helper with `workloads=("compute",)` so it shops the C4D
compute line. Leave storage/IOPS/snapshot OpenSearch rows (which correctly fall through to
`llm_rows`) untouched.
**Impact if unfixed:** mis-tiered compute for OpenSearch compute nodes.
**Test:** `c6i.4xlarge.search` is scoped to the compute workload pool, not general.

---

## P-8 — EFS bare `"ia"` tier key is a 2-char substring matched before `"standard"`
**Lives in:** `scripts/apply_static_mappings.py`, `_EFS_STORAGE_MAP` (~5968-5972), matched in
`map_efs` (~6081).
**Problem:** The map iterates in insertion order `standardia`, `standard-ia`, `ia`, `standard`,
testing `if key in blob` where `blob = usage_type + " " + operation`. The bare key `"ia"` is
tested before `"standard"`, so any EFS **Standard** row whose blob contains the substring "ia"
anywhere matches Basic **HDD** (cheaper) → under-projection.
**Fix:** remove the bare `"ia"` key; keep only anchored IA keys (`standardia`, `standard-ia`,
`-ia`, `timedstorage-ia`). Alternatively require a word boundary for the IA match. Ensure
`"standard"` is reachable for plain Standard rows.
**Impact if unfixed:** intermittent HDD-vs-SSD under-projection on EFS Standard.
**Test:** an EFS Standard row whose operation text contains "ia" still maps to Basic SSD.

---

## P-9 — commitment_discount zeros every routed row with no guard
**Lives in:** `scripts/apply_static_mappings.py`, `map_commitment_discount` (~line 4109).
**Problem:** The function sets `strategy="ignore"` ($0) on every row handed to it, with no check
that the row is actually a commitment artifact. If a real usage row is ever misrouted here it is
silently zeroed with no trace → under-projection.
**Fix:** add a defensive guard: only `ignore` rows whose line-item-type / usage_type indicates a
commitment artifact (e.g. `RIFee`, `SavingsPlanRecurringFee`, `SavingsPlanNegation`,
`EdpDiscount`, `Credit`, or a negative cost). Any row that does NOT match → leave it for normal
handling (emit it to `llm_rows` / passthrough) rather than zeroing it, and log a warning naming
the aws_li_key.
**Impact if unfixed:** a classify misroute becomes a silent $0 with no diagnostic.
**Test:** a genuine commitment-fee row → ignored; a non-commitment row accidentally passed in →
NOT zeroed (passthrough/llm), warning logged.

---

## P-10 — Redshift RMS entry omits the `gcp_sku_id` key when unresolved (latent KeyError)
**Lives in:** `scripts/apply_static_mappings.py`, `map_redshift` ManagedStorage branch (~5228-5242).
**Problem:** The RMS entry dict is built WITHOUT a `gcp_sku_id` key and only sets it inside
`if sku_id:`. Every other emitted row always includes `gcp_sku_id` (None or value). A downstream
consumer that indexes `entry["gcp_sku_id"]` instead of `.get()` would KeyError on an
unresolved-rate RMS row.
**Fix:** initialize `"gcp_sku_id": None` in the entry dict and overwrite inside `if sku_id:`
(match the pattern used by the other branches).
**Impact if unfixed:** latent KeyError / schema inconsistency; no pricing error on its own.
**Test:** an RMS row with no resolvable SKU still has a `gcp_sku_id` key (value None).

---

## P-11 — family_mapper: Local SSD hardcoded to ONE 375 GB increment, ignores real capacity
**Lives in:** `scripts/family_mapper.py`, local-SSD attachment entry (~line 568).
**Problem:** `"unit_multiplier": 375.0 / 730.0` — the Local SSD component is priced as exactly one
375 GB increment regardless of the instance's actual NVMe capacity. For storage-optimized boxes
(i3/i3en/d3/h1 and the `*d` suffix families) the local storage is the whole point:
`i3en.24xlarge` has 8×7500 GB = 60 TB of NVMe → priced as 375 GB → ~**160x under-projection** on
the storage line.
**Fix:** scale by the instance's real local-SSD GB. Source the per-type local-NVMe capacity from
the authoritative instance table (`data/ec2-instance-types.json`, which carries storage specs; if
the field isn't present, add it to the refresh that builds that file). Then
`unit_multiplier = (real_local_ssd_gib / 730.0)` (GCP Local SSD is a 375 GB increment but priced
per GiB-month, so use the true GiB). If the capacity is unknown for a type, emit `passthrough`
with a note — do NOT silently price one increment.
**Related (verify + same fix if confirmed):** the GPU path (`map_gpu_row`) appears to drop the
local-NVMe storage line entirely for GPU instances with instance storage (same root cause). If
confirmed, apply the same capacity-scaled Local SSD component there.
**Impact if unfixed:** massive under-projection on storage-optimized and `*d` instance families.
**Test:** `i3en.24xlarge` emits a Local SSD storage component sized to its real NVMe capacity
(tens of TB), not 375 GB; a non-storage instance is unchanged.

---

## P-12 — projection_view: CUD columns miss COALESCE → non-committable rows drop from CUD totals
**Lives in:** `scripts/projection_view.py` (the `gcp_cost_1yr_cud` and `gcp_cost_3yr_cud` CASE
expressions, ~lines 143 and 152).
**Problem:** The 1yr column uses `c1.rate_usd` and the 3yr column uses `c3.rate_usd` with NO
fallback. Non-committable SKUs (storage, egress, networking, per-request) have no CUD rate, so the
`c1_pick`/`c3_pick` LEFT JOINs miss and `c1.rate_usd`/`c3.rate_usd` are NULL → the whole row's CUD
cost becomes NULL and is dropped from the 1yr/3yr totals. Result: CUD totals exclude all
non-committable spend, which **overstates the CUD savings** shown to the customer.
**Fix:** wrap both with a fallback to the on-demand rate (non-committable items cost the same
under a CUD plan — you pay on-demand for storage/egress regardless):
- line ~143: `ELSE c1.rate_usd` → `ELSE COALESCE(c1.rate_usd, od.rate_usd)`
- line ~152: `ELSE c3.rate_usd` → `ELSE COALESCE(c3.rate_usd, od.rate_usd)`
(The Spot branch already uses `od.rate_usd`; leave it.) Rebuild/refresh any job that re-derives
the view.
**Impact if unfixed:** overstated 1yr/3yr CUD savings on every bill with storage/egress/network
spend (i.e. all of them).
**Test:** a projection with a storage row (no CUD rate) includes that row's on-demand cost in both
the 1yr and 3yr CUD totals; the totals are no longer less than the sum of their committable-only
rows.

---

## P-13 — gp3 throughput ceiling checks an account-wide aggregate, not per-volume
**Lives in:** `scripts/apply_static_mappings.py`, `map_block_storage` gp3 throughput branch
(~line 3437).
**Problem:** The IOPS branch was fixed to guard with a per-volume `(not gp3_like)` aggregate
check, but the **throughput** branch (`elif gp3_like and provisioned * 1024.0 >
_HYPERDISK_BALANCED_MAX_MIBPS`) was not given the matching guard. On multi-volume accounts the
`provisioned` quantity is an aggregate across volumes, so it can exceed the single-volume
Hyperdisk Balanced ceiling even though no individual volume does — wrongly escalating gp3 to an
**unpriced Extreme-PD passthrough** instead of pricing it as Hyperdisk Balanced.
**Fix:** mirror whatever per-volume / aggregate guard the IOPS branch uses (find the IOPS branch's
guard a few lines above and apply the same logic to the throughput branch) so the ceiling is
tested per-volume, not against the summed quantity. Confirm by reading both branches side by side
before editing.
**Impact if unfixed:** multi-volume gp3 accounts get wrong Extreme-PD passthrough instead of
priced Hyperdisk Balanced.
**Test:** a multi-volume gp3 account whose summed throughput exceeds the ceiling but whose
per-volume throughput does not → priced as Hyperdisk Balanced, not Extreme-PD passthrough.

---

## P-14 — Tiered rates: blend on the bill's TRUE total (SUM), not MAX; price egress the same way
**Lives in:** `scripts/apply_rates.py` — the max-usage pre-fetch (~574-582), the blend call
(~600-603, `blended_rate`), and the synthetic egress injection (~760-765).
**Problem:** GCP tiered SKUs (egress, GCS storage tiers, Cloud Logging, BigQuery) get cheaper as
cumulative monthly usage grows. The code blends ONE rate per SKU using `MAX(total_usage)` across
all rows mapped to that SKU, then applies that single rate to every row. MAX is neither the true
cumulative total nor a per-row amount, so the effective rate is wrong in a bill-shape-dependent
direction (under- or over-projection). Separately, egress is injected at the flat **tier-0**
(most expensive) rate, over-projecting egress-heavy bills.
**The pricing math (this is exact, not an approximation):** GCP tiers are cumulative per month.
For a SKU whose total monthly usage is `Q`, with tiers `[(start_0, rate_0), (start_1, rate_1), …]`,
the exact monthly cost is the sum over tiers of `(usage falling in that tier) × tier_rate`:
`cost = Σ max(0, min(Q, next_start) - start_i) × rate_i`. The existing `blended_rate(tiers, Q)`
already computes exactly this and divides by `Q` to return the effective per-unit rate — so it is
correct **if fed the right `Q`**. The only bug is the `Q` it's fed.
**Fix:**
1. Change the pre-fetch from `MAX(c.total_usage)` to `SUM(c.total_usage)` (line ~576) so `Q` is
   the bill's TRUE cumulative usage for that SKU. Rename the dict `max_usage` → `total_usage` for
   clarity. `blended_rate(tiers, SUM)` then yields the exact effective rate for the whole bill's
   volume, and applying it to every row distributes that exact total proportionally to each row's
   usage (the per-row split is correct because `Σ(row_usage × eff_rate) = Q × eff_rate = exact
   tiered total`).
2. **Egress:** stop injecting egress at the flat tier-0 rate. Give the egress synthetic SKU its
   real tier table and route it through the SAME `blended_rate(tiers, SUM)` path, so egress is
   priced by the actual tier rules on the bill's total egress volume — not a flat top-tier rate.
3. Leave single-tier SKUs (`len(tiers)==1`) exactly as they are.
**Impact if unfixed:** every tiered SKU is mis-priced by a bill-shape-dependent amount; egress-heavy
bills over-project egress ~30-40%.
**Test:** (a) a SKU with two rows (50 TB + 2 TB) on a 3-tier egress ladder prices the total at the
exact tiered cost (hand-compute `Σ tier_qty × rate` on 52 TB and assert the sum of both rows'
projected costs equals it); (b) an egress-heavy bill no longer prices all volume at tier-0.

---

## P-15 — NAT Gateway: split public vs private, map to the RIGHT GCP product, model Cloud NAT per-VM
**Lives in:** `scripts/apply_static_mappings.py` — the NAT entries in `FLAT_HOURLY_MAP` (~328-333)
and the NAT handling in `map_data_transfer` (~3777). Also emits an assumption flag (see AR-5).
**Problem:** AWS has TWO kinds of NAT gateway — **public** (private subnet → internet) and
**private** (VPC-to-VPC, no internet). Today ALL NAT gateway-hours map to GCP "Private NAT", which
is wrong for the common public case (GCP Private NAT cannot do internet egress) AND drops GCP
Cloud NAT's per-VM-hour billing. The rate matched by luck.
**Background (so the mapping is right):**
- AWS **public** NAT gateway ≈ GCP **Cloud NAT (Public)**. GCP Cloud NAT bills **per VM-hour**
  (each VM using the NAT), not per gateway. AWS bills per gateway-hour and does NOT expose the VM
  count in the CUR — so the VM count must be estimated.
- AWS **private** NAT gateway ≈ GCP **Private NAT** (the current SKU) — keep that mapping.
**Fix:**
1. **Detect the NAT type** from usage_type/operation: if it indicates a private NAT gateway
   (contains "private" / the private-NAT operation), route to GCP **Private NAT** (current rate).
   Otherwise treat it as **public** and route to GCP **Cloud NAT** (Public) — fix the product name
   (today's "Private NAT" target is wrong for public NAT).
2. **Model Cloud NAT per-VM-hour for public NAT.** Compute a bill-derived VMs-per-gateway:
   - `n_gateways = max(1, round(SUM(nat_gateway_hours) / 730))` (gateway-hours → gateway count), or
     the count of distinct NAT gateway resources if the CUR exposes resource ids.
   - `n_vms = ` distinct EC2 instances in the bill (distinct `instance_type`/resource rows in the
     compute groups), as a proxy for the fleet behind the NAT.
   - `vms_per_gateway = max(1, round(n_vms / n_gateways))` when `n_vms` is derivable;
     **fallback = 16** (mid of the common 10–20 per-AZ pattern) when the bill lacks instance
     detail (PDF/summary bills).
   - GCP Cloud NAT VM-hours for a public-NAT row = `nat_gateway_hours × vms_per_gateway`
     (`unit_multiplier = vms_per_gateway` on the gateway-hour quantity), priced at the Cloud NAT
     per-VM-hour uptime SKU.
3. **Mark it an assumption.** This VM count is an estimate — set the assumption flag + text on the
   row (see AR-5): `assumption="GCP Cloud NAT bills per-VM-hour; AWS CUR omits VM count — estimated
   <vms_per_gateway> VMs/gateway (<derived|fallback>)"`. Keep the NAT **data-processing** rows
   (per-GB) handled as they are today.
**Impact if unfixed:** wrong GCP product on public NAT, and the entire per-VM cost dimension
dropped (under-projection on multi-VM fleets).
**Test:** (a) a public NAT gateway-hours row → Cloud NAT (Public), VM-hours = gateway-hours ×
derived ratio, assumption flag set; (b) a private NAT gateway-hours row → GCP Private NAT; (c) a
PDF bill with NAT but no instance rows → fallback ratio 16, assumption flag set.

---

## Report back (per item)
For each P-item: files changed, the test added, and the before/after on a representative row.
