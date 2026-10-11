# WORK ORDER — Map AWS Inferentia/Trainium → GCP Cloud TPU (fix silent CPU-sizing)

**For an implementing AI / engineer with NO prior conversation context. Read fully.**
Do exactly these steps. Stop and report when done; a reviewer will check.

## The bug (what's wrong today)
AWS AI-accelerator instances (`inf1`,`inf2` = Inferentia; `trn1`,`trn1n`,`trn2` =
Trainium) are classified `mechanic_group='compute_breakdown'` (correct — they ARE
compute instances) and handed to `scripts/family_mapper.py`. There, the dispatch
(around line 944) computes `is_gpu = _AWS_WORKLOAD[prefix] == "accelerated"`, and
`data/family_map.json`'s `aws_workload` marks **only `g` and `p`** as accelerated.
So `inf`/`trn` get `is_gpu=False` → `map_gce_row()` → **priced as a plain CPU VM
(N4D), with the accelerator chips silently dropped.** That is a confident WRONG
number (e.g. inf2.48xlarge → a 192-vCPU VM, ML chips gone).

## The decision (what to build)
Map Inferentia/Trainium chips **1:1 to Cloud TPU v5e chips** — a stated PRICE
assumption, NOT a performance equivalence. GCP sells TPU v5e on-demand (~$1.20/chip-
hour; verified at cloud.google.com/tpu/pricing). AWS inf2 big instances are
~$1.08/chip-hour — comparable, so chip-count parity is roughly cost-neutral and
honest IF labeled. There is NO public Inferentia/Trainium-vs-TPU benchmark (checked
2026-10 across vendor + third-party sources), so no performance ratio is claimed.

Scope: **only `inf*` and `trn*`.** `dl*` (Habana Gaudi) is third-party silicon with
no GCP equivalent — it must stay the honest no-equivalent passthrough (it is already
handled by family_mapper's `_AWS_GPU_NO_EQUIVALENT` set {GAUDI,...}); do NOT map dl
to TPU.

This fix is entirely in `family_mapper.py` + two new data files + one whitelist line.
**Do NOT touch `classify_mechanics.py`, `spec_dispatch.py`, or the spec/parity tests**
— routing stays `compute_breakdown`, so those are unaffected and must still pass.

## Context to read first (do not edit unless a step says so)
- `scripts/family_mapper.py` — read `map_gpu_row`, `map_gce_row`, the dispatch loop
  (~line 930-965), `_no_gpu_equivalent_entry` (~606), `_AWS_GPU_NO_EQUIVALENT` (~587),
  `parse_instance`, `_resolve_instance_type`.
- `scripts/egress_rates.py` — the precedent for PINNED synthetic-SKU rates when the
  catalog lacks a SKU; note how `EGRESS_SKUS` are whitelisted in `merge_mappings.py`.
- `scripts/merge_mappings.py` — find where it filters phantom SKU IDs / whitelists
  `EGRESS_SKUS`; you will add the TPU synthetic SKU to that whitelist.
- `data/ec2-instance-types.json` — host specs only; has NO accelerator chip counts.
- `scripts/config_loader.py` — `load_data_config(name)` → `data/<name>.json`.
- Python 3.14: no inline `(?i)` in regex; pass `re.IGNORECASE`.
- Run tests from `skill/aws-gcp-cost-projection/`:
  `SKILL_DIR="$(pwd)" python -m pytest tests/ -q`

---

## STEP 1 — Create `data/accelerator-chips.json` (AWS accelerator chip counts)
No reliable chip-count table exists in the repo. Create one. VERIFY every count
against the AWS instance pages (aws.amazon.com/ec2/instance-types/inf2, /trn1, /trn2)
and ADD any instance sizes you find missing. Known values to seed:
```json
{
  "_meta": {"purpose": "AWS AI-accelerator instance -> accelerator family + chip count. Used by family_mapper.map_tpu_row to size the Cloud TPU mapping. VERIFY counts against aws.amazon.com EC2 instance pages before trusting.", "source": "aws.amazon.com/ec2/instance-types/{inf1,inf2,trn1,trn2}", "checked": "2026-10"},
  "chips": {
    "inf1.xlarge": {"family": "inferentia1", "chips": 1},
    "inf1.2xlarge": {"family": "inferentia1", "chips": 1},
    "inf1.6xlarge": {"family": "inferentia1", "chips": 4},
    "inf1.24xlarge": {"family": "inferentia1", "chips": 16},
    "inf2.xlarge": {"family": "inferentia2", "chips": 1},
    "inf2.8xlarge": {"family": "inferentia2", "chips": 1},
    "inf2.24xlarge": {"family": "inferentia2", "chips": 6},
    "inf2.48xlarge": {"family": "inferentia2", "chips": 12},
    "trn1.2xlarge": {"family": "trainium1", "chips": 1},
    "trn1.32xlarge": {"family": "trainium1", "chips": 16},
    "trn1n.32xlarge": {"family": "trainium1", "chips": 16},
    "trn2.48xlarge": {"family": "trainium2", "chips": 16}
  }
}
```
If an inf*/trn* instance appears at runtime that is NOT in this table, `map_tpu_row`
must fall back to chips=1 with a lowered confidence and a note saying the count was
assumed (see Step 3). Do not crash.

## STEP 2 — Create `data/tpu-rates.json` (pinned TPU on-demand rate)
The catalog has no on-demand TPU SKU, so pin the rate (same approach as
`egress-rates.json`). VERIFY the live rate at cloud.google.com/tpu/pricing.
```json
{
  "_meta": {"purpose": "Pinned GCP Cloud TPU on-demand per-chip-hour rates. Catalog lacks on-demand TPU SKUs, so pinned like egress-rates.json. VERIFY at cloud.google.com/tpu/pricing.", "generation": "v5e", "checked": "2026-10"},
  "synthetic_sku_id": "TPU-V5E-ONDEMAND",
  "gcp_sku_name": "Cloud TPU v5e (on-demand, per chip-hour)",
  "default_usd_per_chip_hour": 1.20,
  "by_region": {
    "us-central1": 1.20, "us-east5": 1.20, "us-west1": 1.20, "us-west4": 1.20,
    "us-south1": 1.416, "europe-west1": 1.32, "europe-west4": 1.56, "asia-southeast1": 1.56
  }
}
```

## STEP 3 — Add `map_tpu_row()` to `family_mapper.py` and route inf/trn to it
1. Add a helper to decide accelerator kind from the parsed instance / itype:
   - `inf*` or `trn*` prefix → `"tpu_mappable"`.
   - everything the existing GPU path / `_AWS_GPU_NO_EQUIVALENT` handles → leave as is.
2. In the compute dispatch loop (~line 944), BEFORE the `is_gpu` branch, add:
   ```python
   if _accel_kind(parsed, itype) == "tpu_mappable":
       res = map_tpu_row(r, parsed, itype)
   elif is_gpu:
       res = map_gpu_row(r, parsed)
   else:
       res = map_gce_row(r, parsed)
   ```
3. `map_tpu_row(r, parsed, itype)` returns a list with ONE component:
   - look up `chips` from `data/accelerator-chips.json` by `itype`; if absent, chips=1
     and set `assumed_count=True`.
   - look up rate: `by_region.get(gcp_region, default_usd_per_chip_hour)` from
     `data/tpu-rates.json`; the synthetic SKU id is `TPU-V5E-ONDEMAND`.
   - emit:
     ```
     {
       "aws_li_key": r["aws_li_key"],
       "gcp_service": "Cloud TPU",
       "gcp_sku_id": "TPU-V5E-ONDEMAND",
       "gcp_sku_name": "Cloud TPU v5e (on-demand, per chip-hour)",
       "component": "accelerator",
       "strategy": "map",            # (no rate found -> "passthrough")
       "unit_from": "instance-hour", "unit_to": "chip-hour",
       "unit_multiplier": <chips>,   # instance-hours x chips = chip-hours
       "gcp_region": gcp_region,
       "mapping_confidence": 0.3,
       "projection_note": <NOTE below>
     }
     ```
   - **Emit NO core/ram component** — TPU v5e's per-chip on-demand rate bundles the
     host VM; adding CPU/RAM would double-count vs AWS's bundled instance price.
     Put this reasoning in the note.
   - NOTE text (fill <family> from accelerator-chips.json, e.g. "Inferentia2"):
     > "Mapped 1 AWS <family> chip -> 1 Cloud TPU v5e chip (chip-count parity, <chips> chips). PRICE assumption only, NOT a verified performance equivalence — no public Inferentia/Trainium-vs-TPU benchmark exists (checked 2026-10). TPU v5e per-chip rate bundles the host VM, so no separate CPU/RAM line. Migration requires recompiling models from AWS Neuron SDK to GCP XLA/JAX; treat as directional. [if assumed_count: 'chip count assumed = 1 (instance not in accelerator-chips.json).']"

## STEP 4 — Whitelist the synthetic SKU in `merge_mappings.py`
`merge_mappings.py` strips SKU IDs that don't match the real-SKU regex. Add
`"TPU-V5E-ONDEMAND"` to the same whitelist that already exempts `EGRESS_SKUS` (search
for where egress synthetic SKUs are allowed), so the TPU rows survive the merge.

## STEP 5 — Update the spec note (doc only)
In `data/service_specs.json`, the `ec2.instance_hour` charge_type `notes` currently
says Inferentia/Trainium are a "KNOWN GAP ... land in compute_breakdown". Change that
sentence to: "Inferentia/Trainium (inf*/trn*) are handled by family_mapper's TPU
branch (map_tpu_row -> Cloud TPU v5e, chip-count parity); dl* (Gaudi) stays
no-equivalent passthrough." Do not change `match`/`mechanic`/`fields`.

## STEP 6 — Tests: `tests/test_accelerator_tpu.py`
Cover:
1. `inf2.48xlarge` row → one entry, `gcp_service=="Cloud TPU"`, `unit_multiplier==12`,
   `strategy=="map"`, `mapping_confidence==0.3`, note contains "chip-count parity".
2. `trn1.32xlarge` → `unit_multiplier==16`.
3. No separate core/ram component is emitted for a TPU row.
4. An inf*/trn* instance NOT in accelerator-chips.json → chips==1, note says assumed.
5. Missing/empty tpu-rates → strategy falls back to "passthrough" (no crash).
6. `dl1.24xlarge` (Gaudi) → NOT mapped to Cloud TPU (stays passthrough / no-equivalent).
Make it runnable as pytest and as `python tests/test_accelerator_tpu.py`.

## STEP 7 — Acceptance
- New tests pass. `tests/test_family_mapper_compute.py`, `tests/test_spec_dispatch.py`,
  and the rest of `tests/` still pass (you did not change classify routing).
- `data/accelerator-chips.json` and `data/tpu-rates.json` are valid JSON; chip counts
  verified against AWS pages (cite them in your report).
- `git diff` touches only: `family_mapper.py` (new fn + dispatch branch), the two new
  data files, one whitelist line in `merge_mappings.py`, the one note in
  `service_specs.json`, and the new test. Nothing in classify_mechanics/spec_dispatch.

## STEP 8 — Report back
List: files created/changed, the chip-count table you verified (with AWS source URLs),
the live TPU rate you confirmed, test counts, and any inf/trn size whose chip count
you could not verify.

---

## FOLLOW-UP (found in review 2026-10-11)

**F1 (FIXED & TESTED):** `family_mapper.py` dispatch deleted the `is_gpu = ...`
assignment but kept `elif is_gpu:` → `NameError` on every non-accelerator compute row
(untested because tests call the map_* functions directly, not `main()`'s loop).
Fixed by extracting the decision into `_compute_mapper_kind(parsed, itype)` →
'tpu'/'gpu'/'gce', used by the dispatch loop (no more free `is_gpu` variable).
Covered by `tests/test_accelerator_tpu.py::test_compute_mapper_kind_dispatch`
(m5→gce, g5/p4d→gpu, inf2/trn1→tpu).

**F2 (FIXED & TESTED at unit level; end-to-end still needs a real bill):**
`apply_rates.py` now injects the pinned TPU rate into `gcp_sku_rates`
(`inject_tpu_rates()`, called after the Looker Studio injection; reads
`data/tpu-rates.json`; per-region + a 'global' fallback; OnDemand only) and adds the
TPU synthetic SKU to the `synthetic_ids` exemption. Covered by
`test_accelerator_tpu.py::test_tpu_rate_injected_into_gcp_sku_rates`. KNOWN LIMITATION
(unchanged): OnDemand only — inf/trn rows with Reserved/Spot pricing_model and the
1yr/3yr CUD columns have no pinned TPU rate and fall back. STILL TO VERIFY on a real
job DB (not on the dev box): run a bill with an inf/trn row and confirm the projection
shows gcp_service='Cloud TPU' with cost = instance-hours × chips × chip-rate, not a
passthrough.

--- original F2 description (now resolved) ---
`map_tpu_row` sets
`gcp_sku_id="TPU-V5E-ONDEMAND"` but nothing injects that SKU's rate into
`gcp_sku_rates`, so Phase-4 finds no rate → NULL cost → `validate_fix` downgrades the
row to passthrough. Result: inf/trn currently come out as passthrough-at-AWS-cost, NOT
the intended TPU number. To finish option B, in `scripts/apply_rates.py` (right after
the egress + Looker Studio injection block, ~line 735), inject the pinned TPU rate the
same way, reading `data/tpu-rates.json`:
- For each `(region, rate)` in `tpu-rates.json.by_region`, and a `'global'` row using
  `default_usd_per_chip_hour`, `INSERT INTO gcp_sku_rates` with
  `(synthetic_sku_id, 'Cloud TPU', gcp_sku_name, 'Compute', 'TPU', 'OnDemand', <region>, 'chip-hour', <rate>, 'canonical-tpu', 'https://cloud.google.com/tpu/pricing')`
  `ON CONFLICT DO NOTHING` (match the 11-column egress INSERT shape).
- Add the TPU `synthetic_sku_id` to the `synthetic_ids` set (~line 546) so it isn't
  logged as "not found in catalog".
- KNOWN LIMITATION to note: this injects OnDemand only. inf/trn rows with
  pricing_model Reserved/Spot, and the 1yr/3yr CUD columns, won't find a matching TPU
  rate and will fall back (passthrough / NULL) — acceptable interim, flag for later.
- VERIFY end-to-end on a real job DB (a bill with an inf/trn row): confirm the
  projection shows gcp_service='Cloud TPU' with a non-null cost = instance-hours ×
  chips × chip-rate, not a passthrough. (Could not be verified in review — no job DB
  on the dev box.)
