# WORKORDER — deterministic `_select` + ambiguous-resolution diagnostic (systemic)

Status: READY FOR AGY · Author: Claude (Opus 4.8) · 2026-11-19 · Loop: Claude modelled → AGY implements → operator "verify".
Origin: generalization of the multi-region fix ([[project_multiregion_resolve_sku_guard]]). A catalog audit (below) shows the same bug class on many services, not just region scope.

---

## The bug class

`lookup_sku_in_catalog._select` (`scripts/apply_static_mappings.py` ~:1940) takes the SKUs whose description matches a mapper's `desc_pattern` and serve the region, then returns the **first one in catalog file order** (after skipping `preemptible` and `_NOISE_QUALIFIERS` variants). When a *generic* pattern matches several priced variants that differ only by a qualifier **not** in the denylist, the pick is decided by file order — which is:
- **non-deterministic across catalog refreshes** (SKU order in `data/skus/*.json.gz` can change on a refresh → a correct projection today can silently flip tomorrow), and
- **silently wrong** when the file-order winner is the pricier/cheaper variant.

`_NOISE_QUALIFIERS` is a denylist of axes someone already got burned by (autoclass, dual/multi-region, regional, enterprise plus, performance optimized, extended support, confidential mode, early delete). Anything else that splits price is unguarded.

## Audit evidence (catalog scan, us-central1, 2026-11-19)

28 services have ≥1 group of same-region SKUs matching at **different rates**. Most are data-transfer/replication SKUs that mappers resolve with specific directional patterns (low risk). The genuinely unguarded axes:

| Axis | Example | Spread | In denylist? |
|---|---|---|---|
| **Spot** | `Autopilot Arm Spot Pod CPU` $0.012 vs `Autopilot Arm Pod CPU` $0.032 | 2.6× | ❌ (only `preemptible` is) |
| **Premium/Standard tier** | Dataproc Serverless Shuffle Std $0.04 vs Premium $0.10; Cloud Speech Std vs Premium | 1.5–2.5× | ❌ (`Premium` not listed) |
| **Edition** | Vertex AI Search Standard $35 vs Plus $60; Dialogflow Agent Assist Standard vs Enterprise | 1.5–1.7× | partial |
| **Same name, different unit** | `Cloud IDS` $0.07 vs $1.50 | 21× | n/a — inspect |

## Fix — two safe parts + one review loop

### Part A (safe, targeted): add `"spot"` to `_NOISE_QUALIFIERS`
`scripts/apply_static_mappings.py` ~:1261. GCP renamed preemptible→Spot; the denylist still only has `"preemptible"`. A Spot SKU must never be picked unless the pattern asks for it (a mapper wanting Spot names it). Add `"spot"`. Low risk, clear correctness.

### Part B (structural): make `_select` deterministic instead of file-order
Replace "first in file order" with a **stable, explicit tiebreak** so resolution is reproducible across catalog refreshes. Within each existing pass (pass-1 non-noise, pass-2 allow-noise, pass-3 allow-preemptible), sort candidates by:
1. **most specific** — fewest description tokens NOT present in `desc_pattern` (closest match to intent), then
2. **lowest rate** (conservative, deterministic), then
3. **sku_id** (final deterministic tiebreak).

`_sku_meta` currently returns a SKU without its rate; Part B needs the rate to sort — read it from `tiered_rates`/the SKU's `pricingInfo` (a helper already exists for `_family_hourly_rate`; reuse or add a small `_sku_rate(sku)`). Keep the pass structure (preemptible/noise precedence) unchanged — only the WITHIN-pass ordering becomes deterministic.

### Part C (observability): diagnostic on ambiguous resolution
When a pass has **>1 surviving candidate with ≥2 DISTINCT rates**, emit before returning:
```
diag(WARN, "ambiguous_sku_resolution", "apply_static_mappings.py",
     f"{gcp_service} / {desc_pattern!r} @ {gcp_region}: {n} priced variants, "
     f"picked {chosen_id} (${chosen_rate}); others: {other_ids_rates}",
     detail={...})
```
`diag`/`WARN` are already imported (~:49). This surfaces the real tier/edition/Spot collisions in `diagnostics.jsonl` for triage WITHOUT silently changing more dollars than Parts A/B. It does not change the projection.

### Review loop (after this lands, not in this workorder)
Triage `ambiguous_sku_resolution` warnings from a real job run; for each genuine collision either tighten the mapper's `desc_pattern` or add the axis to `_NOISE_QUALIFIERS`. Inspect the `Cloud IDS` 21× case (likely a unit mismatch, may already be caught by G-1b unit validation).

## Tests
- `_select` determinism: given a candidate set in two different orders, the chosen SKU is identical (lock out file-order dependence).
- Part A: a pattern matching both a Spot and non-Spot SKU resolves to the non-Spot one.
- Part B: when a generic pattern matches Standard + Premium at the same region, the choice is deterministic and documented (assert the specific expected SKU).
- Part C: an ambiguous resolution emits exactly one `ambiguous_sku_resolution` diagnostic; an unambiguous one emits none.
- Regression: the full suite stays green; any test whose resolved SKU changes is a real shift to review and bless explicitly.

## Verification battery (operator on "verify")
`ast.parse` + import + no-truncation (`lookup_sku_in_catalog`/`_select`/`main` present) → targeted resolver tests → full suite → empirical: confirm Spot no longer wins a generic Autopilot pattern, and resolution is order-independent → commit main + push origin2 main:AGY.

## Dollar impact
Part A removes Spot-rate under-projection on affected rows. Part B changes dollars only where file order was already a coin-flip (makes it deterministic + conservative). Part C is zero-dollar (logging). The combination converts a silent, refresh-fragile denylist into a deterministic resolver with a safety net — defends every service at once.
