# WORKORDER — Pricing truth, region fidelity & parts-based dispatch (no-regex)

**Status:** proposed · **Author pass:** 2026-11-06
**Single consolidated workorder** for the issues surfaced reviewing the `asia-southeast1` report (EBS snapshot tier mis-map; `APS1-WAW1-AWS-Out-Bytes` direction mis-route; inter-zone "free" error). Excludes the row-4 *explanation* (patch-vs-mechanism / industry context) — that was answered in chat, it is not work.

> **How to use this workorder (anti-hallucination contract).**
> **One phase per PR.** Do **not** start a phase until the previous phase's **STOP GATE** is green.
> Every phase is **additive first**: add the new signal/column/field alongside the old path, prove it matches on real bills, and only then flip authority. Never delete a regex path and add its replacement in the same PR. If a phase's acceptance test can't be run (missing fixture bill, no API key, etc.), **STOP and report** — do not assume it passes.
> **Pricing-truth rule (AGENTS.md):** every rate/unit/free-tier fact used below must be verified against the provider's official pricing page and cited in code. The bundled catalog can publish misleading $0 SKUs — see Phase 1.

---

## Background facts (verified — do not re-derive)

- **AWS CUR carries the authoritative transfer signal already:** `product/fromRegionCode` + `product/toRegionCode`. The `datatransfer` spec already *declares* these fields; they are discarded at ingest.
- **Ingest discards it:** `ingest.py` (region fallback chain, ~line 1906) folds `product_from_region_code` into the single `aws_region` column and **never reads `product_to_region_code`**. The from→to pair is destroyed before classification.
- **`aws_raw` is transient:** dropped (~line 712/716) after dedup. Any cardinality/loss check must run **before** that drop and persist its result.
- **Dedup key:** `GROUP BY product, usage_type, operation, aws_region, pricing_model, line_item_type, is_workload, billing_month`.
- **Only COST is reconciled today** (`COST_RECONCILE`): sum($) preserved. **Cardinality is NOT reconciled** — a structural drop that preserves $ is invisible.
- **Inter-zone egress is $0.01/GiB, NOT free** (official: cloud.google.com/vpc/network-pricing). Only *same-zone* internal-IP traffic is free.
- **The catalog $0 is NOT a fetch error.** The raw GCP dump (`data/skus/9662-B51E-5089.json.gz`) shows GCP's API itself returns `Network Inter Zone Data Transfer Out` at **0 units / 0 nanos = $0.00** (SKU `C1F1-02CA-F355` + per-region `...in <City>` variants), while `DE9E-AFBC-A15A` returns `10,000,000 nanos = $0.01`. GCP's catalog is self-contradictory; **a refetch reproduces the same values.** Fix is in code, not a re-pull.

---

## Phase 0 — Cardinality & loss reconciliation harness (prerequisite, no behavior change)

**Goal:** be able to *see* ingestion loss before changing any routing.

**Do:**
1. Before `aws_raw` is dropped, compute + persist an `ingest_stats` table (one row per job): `raw_row_count`, `distinct_dedup_tuples_in` (= `COUNT(DISTINCT dedup-tuple)` over `aws_raw`), `catalog_row_count`, `raw_cost_sum`, `catalog_cost_sum`, and a per-service JSON breakdown `{product_code: {distinct_tuples, rows, cost}}` in vs out.
2. Add the invariant assertion: `distinct_dedup_tuples_in == catalog_row_count` (conservation of distinct types) + keep existing `COST_RECONCILE`. Mismatch → `data_gaps.json` entry + WARNING (observe first; not fatal until Phase 5).

**Is cardinality uniform? No** — per-bill, per-service property, never a constant: raw count scales with hourly granularity × resources (millions); distinct-type count scales with how many instance types/regions/usage types/operations a service uses (EC2/RDS → hundreds; Support/Tax → 1–2). So the only safe check is a **per-bill conservation invariant (in == out)**, never a threshold or cross-bill comparison. Dedup *intentionally* collapses raw rows — a raw-count drop is expected; the distinct-tuple count must be conserved.

**STOP GATE:** run on ≥3 real bills (CUR Parquet, CSV, PDF). `ingest_stats` populated, invariant holds (or every violation explained). Commit the baseline stats.

---

## Phase 1 — Correct inter-zone egress rate (standalone, ship-now, low-risk)

**Goal:** stop projecting free inter-zone transfer. Self-contained; independent of the parts work.

**Do:**
1. In `egress_rates.py`, **delete the "free since Oct 2023 / 5 legacy regions" branch** in `interzone_sku_for_region()`. Return **$0.01/GiB for all regions** (cite `cloud.google.com/vpc/network-pricing` in a comment). Stop selecting the $0 SKU `C1F1-02CA-F355` / per-region `$0` SKUs.
2. Enforce the general rule: a direction's canonical rate resolves to `strategy='map'`, `rate_source='canonical-egress'`, with the real rate. Never resolve a known lane to `NULL`/`needs_reprice`, and never project $0 for a billed charge.
3. (Guard) Add a unit test asserting inter-zone resolves to $0.01/GiB in a non-legacy region (e.g. asia-southeast1), not $0.

**STOP GATE:** a known same-region inter-zone row prices at $0.01/GiB (≈ AWS inter-AZ parity after GiB/GB conversion — note GCP per-GiB ≈ $0.0093/AWS-GB, marginally cheaper), strategy `map`, not `—`/`needs_reprice`. Golden tests updated.

---

## Phase 2 — Preserve region fidelity at ingest (additive)

**Goal:** stop destroying the from→to pair. Pure data capture; no classifier change.

**Do:**
1. Add `from_region` + `to_region` (GCP-decoded) columns to `aws_li_catalog`, **alongside** the untouched `aws_region`.
2. Source priority: `from_region` = `product/fromRegionCode` → `region_prefix` token 1 → `aws_region`; `to_region` = `product/toRegionCode` → `region_prefix` token 2 → `NULL`.
3. Extend `region_prefix.py`: add `decode_region_pair()` (both leading tokens, not just the first); add **missing codes** — `WAW1`→`europe-central2` (Warsaw) and any others found by `SELECT DISTINCT` of leading tokens across all bills diffed against `UT_PREFIX_TO_GCP`.
4. Add the new columns to the dedup `GROUP BY` **only if** Phase-0 stats show no cardinality explosion; otherwise carry as `MAX()`-per-group passthrough.

**STOP GATE:** every data-transfer row has non-null `from_region`; inter-region rows have `from_region != to_region`; `WAW1`→`europe-central2`; Phase-0 invariant holds; **classifier output byte-identical to pre-Phase-2** (diff `mechanic_group`/`gcp_sku_name`).

---

## Phase 3 — Parts-based direction for `data_transfer` (first output change; data transfer only)

**Do:**
1. Add pure `transfer_direction(parts)` in `usage_type_parser.py` (regex legitimate here). Decision table:
   | condition | direction |
   |---|---|
   | `flow == in` | ingress → `ignore` (free) |
   | `to_is_internet` (dest not a region) | internet |
   | `from_region == to_region` | interzone |
   | `from_region != to_region`, both real regions | interregion |
2. **Trust order:** CUR `from/to` columns (Phase 2) win. Fall back to `operation` locality text **only for flat/PDF bills** where usage_type can carry a corrupted sibling label (the documented `$116.51` case) — this keeps the one case the old regex got right while fixing WAW.
3. Point the `datatransfer` spec's `data_transfer` charge_type at the parsed direction field instead of `map_data_transfer`'s regex.

**STOP GATE:** WAW classifies **interregion**, priced on the inter-region SKU (~$0.08/GiB), not `needs_reprice`. Same-region inter-zone prices at Phase-1's $0.01/GiB. Diff every data-transfer row vs baseline, explain each change.

---

## Phase 4 — Generalize parts-based sub-classification (one service per PR)

**Goal:** same "decide the sub-charge-type from structured parts, declared per service" pattern for the other regex-in-mechanic offenders.

1. **EBS snapshot tier (row-4 fix)** — add an `ebs` spec (none exists). charge_types discriminate standard vs archive by a **structured token** (`SnapshotArchiveStorage` / operation contains `archive`), not the `is_snapshot` substring. Standard → `Storage PD Snapshot` ($0.061); archive → `Regional Archive Snapshot Data Storage` ($0.0234). The *mechanism* (spec_dispatch) is general; each service's tier split must be **authored in its spec**.
2. Audit remaining `map_*` functions in `apply_static_mappings.py` for substring-on-blob sub-classification; migrate each to a spec charge_type reading parsed fields. One PR each.

**STOP GATE per service:** specced path reproduces the correct mapping on real bills for that service; cardinality + cost invariants hold; other services unchanged.

---

## Phase 5 — Flip authority / retire the regex path

**Do:** remove `_transfer_target`'s regex direction logic and migrated `map_*` substring branches (keep `usage_type_parser` — regex there is legitimate). Make the Phase-0 conservation invariant and the Phase-1/3 strategy invariant **fatal gates**.

**STOP GATE:** full suite green; golden-mapping tests updated; no `needs_reprice` row that has a known canonical rate; cardinality + cost invariants fatal and passing on all fixture bills.

---

## Out of scope / non-goals
- No embedding/semantic SKU matching — this pipeline is deterministic structured dispatch (product_code → spec → parsed-field predicates), LLM reserved for genuine long-tail sizing. Keep it that way.
- No catalog refetch for the inter-zone issue (see Background — it is not a fetch error).
- No change to the LLM phase, catalog refresh cadence, or CUD/commitment logic.
