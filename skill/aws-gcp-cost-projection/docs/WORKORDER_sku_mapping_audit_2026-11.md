# WORKORDER — SKU-mapping audit remediation (2026-11-04)

Scope: SKU mapping / classification / license modelling only. **Security is explicitly
out of scope** for this workorder (per request). Baseline at audit time: `pytest tests/`
= 1238 passed, exit 0.

This workorder records what a multi-agent + direct code audit found. Items are grouped
by **confirmed-in-code** (fix these) vs **audit-incomplete** (verify before coding —
five audit agents were cut off by a session rate limit and three areas were not finished).

---

## A. Confirmed in code — actionable

### A-1 (HIGH, real safety hole) — `license_fraction` has no upper clamp
**File:** `scripts/license_split.py:67-69`
**Problem:** `get_aws_license_split()` applies the stored `license_fraction` directly:
```python
frac = (itype_info.get("license_fraction", {}) or {}).get(os_key)
if frac is None or frac <= 0:
    return None
aws_lic_cost = round(aws_cost * frac, 2)          # <-- no upper bound
```
The only guard is `frac <= 0`. A `license_fraction` at/near `1.0` — whether from a bad
`refresh_ec2_specs.py` computation or an admin-edited value — yields an absurd
instance/license split (e.g. $0.01 instance + $499.99 license) while passing every
downstream validation bound. `docs/WORKORDER_licensing_and_safe_ratios.md` Item 4 flags
this as "do first"; all its boxes are unchecked.

**Fix:**
1. Add a sane upper bound. OS license premium as a fraction of the license-*inclusive*
   OD price is well under 0.5 in practice (Windows ≈ 0.2–0.4, RHEL lower). Clamp to a
   calibrated max — suggest `_MAX_LICENSE_FRACTION` per `os_key` (windows/rhel/ubuntu_pro/
   suse), default conservative `0.6`. If `frac` exceeds it, treat as bad data: return
   `None` (graceful "(incl. OS license)" fallback) **and** emit a one-line stderr/log
   warning naming the itype + os_key + value, so the bad fraction is visible, not silent.
2. Validate on write: wherever `license_fraction` is produced
   (`scripts/refresh_ec2_specs.py`) and wherever an admin can edit it, reject/round-trip
   values outside `(0, _MAX_LICENSE_FRACTION]`.
3. Test: `tests/` — add a case asserting a 0.95 fraction returns `None` (not a split),
   and a case asserting a normal 0.3 fraction still splits correctly.

**Verification:** existing `tests/` stay green; new clamp test passes; grep a sample
`data/ec2-instance-types.json` to confirm no shipped fraction already exceeds the clamp
(if one does, that's a data bug to fix in refresh, not a reason to raise the clamp).

### A-2 (MED) — 65 `.metal` instance types lack `license_fraction` → asymmetric display
**Files:** `data/ec2-instance-types.json` (65 `.metal` shapes missing the key),
`scripts/license_split.py:67`, `scripts/refresh_ec2_specs.py`
**Problem:** For a Windows `.metal` row, the AWS side falls back to a lumped
`(incl. OS license)` cost (frac is `None`), but the GCP side still emits a separate
Windows license component (the flat per-vCPU SKU needs only vcpus, which metal has).
Result: the exact instance+license asymmetry the feature exists to remove, left in place
for metal shapes. No wrong number — degrades gracefully — but cosmetically inconsistent.
**Fix:** have `refresh_ec2_specs.py` compute `license_fraction` for `.metal` shapes
(they have a published Windows/Linux OD pair like any other), or, if a metal pair is
unavailable, borrow the non-metal sibling's fraction with a logged note.

### A-3 (LOW-MED) — accelerator instances get a license component they shouldn't
**File:** `scripts/apply_static_mappings.py:8641` (`_license_entries` called
unconditionally for RHEL/Ubuntu/SUSE)
**Problem:** On GPU/accelerator instances the AWS accelerator premium is baked into the
base rate; emitting a separate OS-license component double-attributes. No accelerator
exclusion. (`WORKORDER_licensing_and_safe_ratios.md` Item 2, unchecked.)
**Fix:** skip `_license_entries` when the instance family is an accelerator shape
(reuse the GPU/accelerator detection already in `family_mapper` / `_gpu_profile`).

### A-4 (LOW) — Oracle License-Included not split per spec
**File:** `scripts/apply_static_mappings.py:8374` vs `:8383`
**Problem:** the Oracle branch returns a BYOL-style passthrough *before* the
`license_model` check, so Oracle **License-Included** rows are never split as spec'd.
**Fix:** branch on `license_model` first; License-Included → split, BYOL → passthrough.
(`WORKORDER_licensing_and_safe_ratios.md` Item 3, unchecked.)

---

## B. Confirmed healthy — no action (recorded so they are not re-opened)

- **Identity-first routing is real.** `canonical_service()` keys on AWS `product_code`
  first (`aws_normalizer.py:136`), name-alias fallback is longest-first + word-boundary
  for ≤4-char aliases (ec2→ecr bug fixed). `classify_with_trace` routes through
  `spec_dispatch` as the primary engine (`classify_mechanics.py:1314`); legacy regex
  `RULES` is fallback-only; a spec error routes to `misc` loudly instead of the generic
  "Storage" rule (`:1316-1324`). The "name contains cloud/storage but isn't storage"
  misroute is structurally prevented.
- **Autofix-before-gate ordering fixed.** Both Layer-4 autofixes sit before the `GATES`
  marker (`validate_fix.py:1592`, `:1635`, marker `:1731`); `unreachable_rate` and
  `null_gcp_service` gates run on post-fix state.

---

## C. Verified directly after the agents were rate-limited

The three agent areas that were cut off were re-checked by hand. Results:

### C-1 SKU-id usage & rate-fill — HEALTHY, no action
- `incremental_rerate.py:40-55` self-loads rates on the next run for any
  `gcp_sku_id IS NOT NULL` not yet in `gcp_sku_rates` — the self-heal path is real.
- `validate_fix.py:1592` Layer-4 autofix downgrades any `map`/`break_down` row whose
  `gcp_projected_cost` is NULL **or $0** to `passthrough`, covering all three failure
  cases (SKU resolved but rate missing, `gcp_sku_id` NULL, and $0 from wrong-unit tier
  parsing). The report never shows a silent $0 for a material row.
- Identity key is `product_code` (see Section B). No action.

### C-2 Logging & data-completeness — HEALTHY, no action
- `aws_normalizer.py:24` prints the unrecognized-product-names + spend table to stdout —
  the claim is backed.
- `ingest.py:875 check_faithfulness()` + `:906` raise a hard "Cost reconciliation failed"
  error when catalog total ≠ reference total — AWS-in must equal catalog-out, so no line
  item is silently dropped. PDF path reconciles gross → stated pre-tax with a logged
  single reconciliation row (`:1575-1577`). No action.

### C-3 Assumptions — MOSTLY healthy; ONE real gap

**Healthy:**
- Defaults **are** consumed: `config_loader.py:40 load_assumptions()` loads
  `data/assumption-defaults.json` and merges the per-job snapshot
  `<job_dir>/assumptions.json` over them — per-job **overrides work end-to-end**.
- There is even a ratio-group **read-validation** clamp (`config_loader.py` Task 4) so an
  out-of-range assumption (e.g. Aurora ACU vCPU/RAM) can't reach a projection — the same
  class of safety A-1 is missing for license fractions, already present here.

**Findings:**

#### C-3a (MED, real) — assumptions are NOT disclosed in the CSV/XLSX export
**Files:** `scripts/export_report.py` (0 occurrences of "assumption"),
`scripts/render_report.py` (27 occurrences).
**Problem:** assumptions are surfaced in the HTML/PDF report but the CSV/XLSX export
contains no assumption disclosure. The exported deliverable — the one a customer keeps —
is therefore not self-describing about the modelling assumptions behind its numbers.
**Fix:** add an "Assumptions" sheet (or per-row assumption column where one applies) to
`export_report.py`, sourced from the same `load_assumptions()` + `assumption_impact`
registry the HTML report uses, so both outputs disclose identically (mirror the
license-split symmetry pattern already used across the two report writers).

#### C-3b (LOW, by-design note, not a bug) — `assumption_impact.json` is descriptive, not a $ sensitivity
`data/assumption_impact.json` is a **registry** mapping each assumption →
(`services`, `charge_types`, `mapper`, `decision_it_changes` prose). It is loaded as-is
(`assumptions_impact.py:13`); it does **not** contain, and the tool does not compute, a
recomputed dollar delta per assumption. So the numbers are not "faked" — there are no
numbers. If a quantified "this assumption moves the total by $X" sensitivity is wanted,
that is a **new feature** (re-run projection with the assumption perturbed), not a fix.
Flag for product decision; no code bug.

---

## Implementation order
A-1 (safety, do first) → A-2 → A-3 → A-4 → C-3a. C-3b is a product decision, not a fix.
Sections B, C-1, C-2 are verified healthy — do not re-open.
