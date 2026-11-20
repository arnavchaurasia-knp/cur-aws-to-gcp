# WORKORDER — spec_match consolidation (S3: one definition of "GCP meets/exceeds AWS")

> **NEW AGENT:** read `ARCHITECTURE_MASTER_TRACKER.md` §0 first. This is subsystem **S3** (target adequacy). Update the
> tracker §2 (S3) and `AUDIT_built_systems_wiring.md` row 2 as you progress.

Status: **DONE — 100% (gated & verified)** · Author: Claude (Opus 4.8) · 2026-11-20.
Related: `scripts/spec_match.py`, `scripts/reconcile_capacity.py`, `scripts/auto_review.py`, `AUDIT_built_systems_wiring.md`.

## Corrected finding (the audit's "3 identical copies" was too simple)

`spec_match.meets_or_exceeds(aws_spec, gcp_spec)` is a clean **Python dict comparator**: numeric fields (vcpu, ram_gb,
iops, throughput_mbps) must be GCP ≥ AWS; categorical fields (arch, burstable, storage_tier, ha_topology) must match
exactly; GCP-missing fields go to `unchecked` (never a silent pass). It has **0 in-repo callers** (built for the "CUR
Line Analysis Tool").

The pipeline's actual capacity checks are **two SQL gates with DIFFERENT rules**:

| Site | Rule | Same as spec_match? |
|---|---|---|
| `reconcile_capacity.py` (post-merge) | **absolute**: `gcp_vcpu < aws_vcpu` (and RAM) per component → repair | ✅ identical rule (numeric ≥) |
| `auto_review.py` (post-hoc review) | **ratio**: `gcp_ram/gcp_vcpu < aws_ram/aws_vcpu` → flag for review | ❌ different (memory:cpu shape, not absolute) |
| `family_mapper` / `apply_static_mappings` | family **selection** (best-fit sizing), not a pass/fail gate | n/a — not a duplicate |

So "equivalent" is defined **two ways** in the pipeline, and `spec_match` (meant to be the one definition) is used by
neither. That inconsistency — not three literal copies — is the real debt.

## Decision required (blocks the dollar-affecting part)

**D-SPEC-1:** Is `auto_review`'s **ratio** rule intentional (detect a bad memory:cpu *shape* even when absolute capacity
is met) or should it converge to the **absolute meet-or-exceed** rule (`spec_match`)?
- **Keep ratio (rename):** it's a distinct "shape mismatch" check, not a capacity check. Keep it, rename its concept
  so it's clearly not the same as capacity adequacy. No dollar move. `spec_match` then owns only the absolute rule.
- **Converge to absolute:** `auto_review` flags rows where GCP < AWS on vcpu OR ram (same as reconcile_capacity).
  Changes which rows get reviewed/repriced → **dollar-affecting**; must be run on real jobs with before/after.

Recommendation: **keep ratio but rename** (the two checks answer different questions — "is it big enough" vs "is it the
right shape"); make `spec_match` the canonical **absolute** definition and lock `reconcile_capacity` to it.

## Plan (after D-SPEC-1)

- [x] S3.1 Make `spec_match.meets_or_exceeds` the canonical **absolute** rule (it already is). Correct its docstring:
  it is the definition for *capacity adequacy*; the ratio/shape check is a separate concern.
- [x] S3.2 Parity-lock `reconcile_capacity`: a test that feeds the rows it flags (or a fixture of (aws,gcp) specs)
  through `meets_or_exceeds` and asserts the same verdict — so the SQL can't silently drift from the Python rule.
  (`test_spec_match_parity.py` covers both unit cases and full in-memory DuckDB SQL execution).
- [x] S3.3 Resolve `auto_review` per D-SPEC-1 (rename-and-keep: clarify shape/ratio and burstable mismatch detection).
- [x] S3.4 Point `family_mapper`'s doc + `spec_match`'s docstring at the accurate picture (selection ≠ comparison).
- [x] S3.5 Flip S3 in the master tracker; note outcome in `AUDIT_built_systems_wiring.md` row 2.

## Why not just "wire all 3 through spec_match"

Two of the three are SQL set-operations at a different layer (post-merge / post-hoc), not Python per-row calls, and one
isn't a comparison at all. Forcing a Python dict comparator into a SQL `WHERE` would mean pulling whole tables into
Python per phase (perf + behavior change) and would silently change `auto_review`'s rule. The clean outcome is **one
canonical rule definition (`spec_match`) + parity tests locking each implementation to it**, not one function literally
called everywhere.
