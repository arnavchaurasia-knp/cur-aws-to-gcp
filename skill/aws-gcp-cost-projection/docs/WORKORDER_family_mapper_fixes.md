# WORK ORDER — family_mapper.py correctness + robustness fixes

**For an implementing AI / engineer with NO prior context. Read fully. `family_mapper.py` is the
deterministic COMPUTE pricer (EC2→GCE core+ram, GPU, TPU, RDS→Cloud SQL). It is mature and
config-driven — these are TARGETED fixes, NOT a rewrite. Do NOT restructure the file; each item is
a small, local change. Keep both classifier/pricer suites green.**

Context: a separate effort (`WORKORDER_pricer_architecture.md` AR-3) will later FOLD this file into
`apply_static_mappings.py`. These fixes are correctness/robustness that must survive that merge —
make them here now; they carry over. Line numbers may drift a few lines after other edits — match
on surrounding code.

Test command (from `skill/aws-gcp-cost-projection/`):
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
Pricing basis: AWS On-Demand LIST vs GCP On-Demand LIST. Never emit a confident `strategy="map"`
on fabricated/defaulted inputs — flag or passthrough instead.

---

## FM-1 — Silent tiny-default under-projection when instance specs are unknown (MEDIUM-HIGH)
**Lives in:** `family_mapper.py` — `map_gce_row` (vcpus/ram defaults, ~297-298), `map_gpu_row`
(~775-776), `map_db_row` (~957-958).
**Problem:** Each mapper does `vcpus = r.get("instance_vcpus") or 2` / `ram = … or 8.0`
(GPU: 8 / 32). `_backfill_specs_from_ec2_types` fills these from `ec2-instance-types.json` WHEN the
type is in that file — but for a brand-new / renamed / not-yet-catalogued instance type, the specs
stay missing and the mapper prices a potentially 64-vCPU/512 GB box as **2 vCPU / 8 GB** at
`strategy="map"`, confidence 1.0. Massive silent under-projection on exactly the "new AWS instance
family" case.
**Fix:** track whether vcpus/ram came from a REAL source (row columns or the ec2-types backfill) or
from the `or 2`/`or 8` default. When they are defaulted (no real spec found):
- do NOT emit a confident `map`. Either (a) `strategy="passthrough"` at AWS cost with
  `projection_note="instance specs unknown for <itype> — not in ec2-instance-types.json and CUR
  omitted vCPU/RAM; passthrough at cost, refresh the instance-types catalog"`, or (b) keep the
  core/ram breakdown but set `mapping_confidence ≤ 0.35` AND add the same loud note.
- Record the unknown type in `unknown_families.log` so the refresh picks it up.
Prefer (a) passthrough — a guessed tiny number is worse than an honest cost-parity passthrough.
**Test:** a row with an instance_type NOT in `ec2-instance-types.json` and no vcpus/ram columns →
passthrough (or ≤0.35 confidence) + loud note + logged as unknown; a known type is unchanged.

---

## FM-2 — SUSE/SLES license premium not added (compute_breakdown path) (LOW-MEDIUM)
**Lives in:** `family_mapper.py` — `_license_entries` (~236-256) and `_RHEL_VCPU_BANDS` (~229).
**Problem:** `_license_entries` adds a GCP license component only for RHEL. SUSE Linux (SLES)
instances also carry a "License Included" premium bundled into the AWS On-Demand rate; mapping to
GCP core+RAM only drops it (GCP sells SLES/SLES-for-SAP premium-image licensing SKUs). Windows is
handled elsewhere; SUSE has no handler anywhere, so it's silently lost.
**Fix:** add a SUSE branch in `_license_entries` keyed off `operating_system` containing "suse" /
"sles". VERIFY the exact GCP SLES premium-image SKU name(s) in `data/catalog.duckdb` first
(SLES is a flat per-VM or per-vCPU-band premium like RHEL — confirm which). Emit a `license`
component the same shape as the RHEL one. If no SLES SKU is found in the catalog, emit passthrough
for the license premium with a note rather than dropping it.
**Impact if unfixed:** under-projection on SLES instances (license premium silently omitted).
**Test:** a SLES EC2 row in compute_breakdown emits a license component (or a labelled passthrough
if the SKU isn't in the catalog); a plain-Linux row still emits no license line.

---

## FM-3 — Unparseable compute/db rows are silently dropped, not logged (LOW, observability)
**Lives in:** `family_mapper.py` — `main()` dispatch loops (~1110-1111 and ~1136-1137:
`if not parsed: continue`).
**Problem:** when `parse_instance(itype)` returns None the row is `continue`d BEFORE the
`unknowns.append(...)` at the bottom of the loop, so it's neither mapped nor recorded in
`unknown_families.log`. (It still falls through to the LLM because its key isn't pruned — so no
dollar is lost — but we lose the signal that the parser couldn't read this type.)
**Fix:** before `continue`, append to `unknowns` (e.g. `unknowns.append(f"compute: unparseable
{itype!r}")` / `database: unparseable …`) so the coverage log and the refresh loop see it.
**Impact if unfixed:** blind spot — unparseable instance types never surface for catalog refresh.
**Test:** a row whose itype can't be parsed is recorded in `unknown_families.log`.

---

## FM-4 — TPU row hardcodes us-central1 when region is missing (LOW)
**Lives in:** `family_mapper.py` — `map_tpu_row` (~709: `gcp_region = r.get("gcp_region") or
"us-central1"`).
**Problem:** a TPU-mapped row (Inferentia/Trainium) with no `gcp_region` is priced at the
us-central1 TPU rate, a silent hardcoded-region assumption inconsistent with the rest of the
pipeline's region handling.
**Fix:** fall back to the bill's dominant region (the same `compute_dominant_region` /
region-fallback chain used in `apply_rates.py` AR-4), not a hardcoded literal. If the dominant
region isn't available at call time, keep `us-central1` but add it to the projection_note
("region unknown; priced at <region> TPU rate").
**Impact if unfixed:** wrong-region TPU pricing on region-less rows; undisclosed.
**Test:** a TPU row with no region uses the bill's dominant region (or notes the fallback).

---

## FM-5 — Assumption rows must carry the structured assumption flag (ties to AR-5)
**Lives in:** `family_mapper.py` — `map_tpu_row` (Inferentia/Trainium 1:1 chip parity, ~750-765),
and any defaulted-spec row from FM-1.
**Problem:** the TPU mapping is an explicit PRICE assumption (1 AWS chip → 1 TPU v5e chip, no
benchmark basis, confidence 0.3) but it has no machine-readable flag — the report can't mark it
with a `*` or list it in the Assumptions section (see `WORKORDER_pricer_architecture.md` AR-5).
**Fix:** set `"is_assumption": true` and `"assumption": "<one-line basis>"` on the TPU entry
(basis: "1 AWS Inferentia/Trainium chip ≈ 1 Cloud TPU v5e chip — price parity assumption, no public
benchmark") and on any FM-1 defaulted-spec passthrough. Use the shared `_emit()` helper if AR-5 has
introduced it; otherwise add the two keys directly.
**Test:** a TPU row emits `is_assumption=true` with a basis string; the report (AR-5) renders a `*`
and an Assumptions entry for it.

---

## Report back (per item)
Files changed, the test added, before/after on a representative row, and — for FM-2 — the exact GCP
SLES SKU name you found in the catalog (or that none exists).
