# WORK ORDER — Dense-HDD instance storage priced as GCP Local SSD

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: one mapper file, one data file, one test file.** You are changing two
call sites in `scripts/apply_static_mappings.py`, adding one field to
`data/ec2-instance-types.json` (and the script that regenerates it), and adding
tests. You are **not** changing rates, the projection view, the gates, or any
other mapper. Expect **no dollar movement on any real bill in the corpus** —
no corpus bill contains a dense-storage instance. Dollar movement appears only
on synthetic fixtures and on future bills containing `d2`/`d3`/`d3en`/`h1`.

---

## 1. The problem in plain English

AWS instance families that ship **local disks** fall into two physically
different groups:

- **NVMe flash** — `m5d`, `c5d`, `r5d`, `i3`, `i3en`, `i4i`, `im4gn`,
  `is4gen`, `z1d`, and every GPU family (`g4dn`, `g5`, `p4d`, …).
- **Spinning HDD** — the *dense storage* families: `d2`, `d3`, `d3en`, `h1`.
  These ship enormous capacity cheaply: `d3en.8xlarge` is 16 x 14 TB of HDD.

The family mapper prices local storage against a GCP SKU literally named
`"SSD backed Local Storage"` (~$0.08/GiB-month) and **never checks which group
the instance is in**. Dense-HDD capacity is therefore priced at flash rates.

### Worked example — `d3en.8xlarge`

Real instance: 32 vCPU, 128 GiB RAM, **224,000 GiB of HDD**
(`data/ec2-instance-types.json` → `"d3en.8xlarge": {"storage_gb": 224000.0}`).

| | |
|---|---|
| storage component multiplier | `224000 / 730 = 306.85` GiB-month per instance-hour |
| GCP Local SSD rate | ~$0.08 / GiB-month |
| projected storage | ~**$17,920 / month** |
| whole instance, AWS on-demand list (~$6.31/hr) | ~**$4,600 / month** |
| over a 2.04-month bill, storage alone | ~**$36,500** |

**The storage line alone projects at roughly 4x the entire real instance.**
The arithmetic is correct — 224,000 GiB really is 224,000 GiB, and the GiB-month
conversion really is right. The *premise* is wrong: that capacity is spinning
rust, and it is being billed at flash prices. That is why no plausibility gate
fires; nothing in the pipeline knows the storage media.

**GCP has no dense-HDD local-storage product.** Local SSD is flash only. There
is no equivalent to buy, so there is no honest SKU to price against.

### The two call sites

Both read `storage_gb` and neither checks media. Verified by reading the file:

| site | function | lines |
|---|---|---|
| CPU instances | `map_gce_row` | `scripts/apply_static_mappings.py:8155-8198` (the `"SSD backed Local Storage"` fallback is **8160**, the multiplier **8174**) |
| GPU instances | `map_gpu_row` | `scripts/apply_static_mappings.py:8445-8468` (desc **8450**, multiplier **8458**) |

The CPU site is entered by this condition at **line 8156**:

```python
if "d" in parsed["suffixes"] or parsed["prefix"] in ("i", "im", "is", "d", "h"):
```

`prefix in ("d", "h")` is exactly what pulls the dense families in.
`parse_instance("d3en.8xlarge")` returns `{'prefix': 'd', 'gen': 3,
'suffixes': 'en', ...}` — verified by running it.

Reproduced end to end (verified, not inferred):

```
$ python -c "...; fm.map_gce_row(r_d3en, fm.parse_instance('d3en.8xlarge'))"
core    map 32.0    Deterministic mapping: GCE N4D Core ...
ram     map 128.0   Deterministic mapping: GCE N4D RAM ...
storage map 306.85  Local SSD suffix / storage-optimized VM attachment (224000 GiB, ...)
```

---

## 2. The decisive fact you need before you start

**`data/ec2-instance-types.json` has no storage-media field.** The complete set
of per-type keys across all 1,310 entries is:

```
accelerator, arch, chip, license_fraction, ram_gb, storage_gb, vcpus
```

So this is **not** "read the existing field". You must introduce the
distinction. The generator already has the information and throws it away:

`scripts/refresh_ec2_specs.py:78` —

```python
def _parse_storage(storage: str) -> float | None:
```

It receives the AWS Price List `storage` attribute — a string such as
`"24 x 14000 HDD"` or `"2 x 1900 NVMe SSD"` — and returns only a number
(`scripts/refresh_ec2_specs.py:147-155` persists `storage_gb` and nothing else).
The media word is right there in the string and is discarded.

T1 therefore has two halves: persist the media from the generator, **and** ship
a small hardcoded family list so the fix works today without a catalog refresh.

---

## 3. The family list

Dense **HDD** (apply the fix):

```
d2, d3, d3en, h1
```

All four are present in `data/ec2-instance-types.json` (18 types: `d2.*` x4,
`d3.*` x4, `d3en.*` x6, `h1.*` x4) — verified by listing the keys.

**NVMe — do not touch.** `i3`, `i3en`, `i4i`, `im4gn`, `is4gen`, `z1d`, every
`*d` suffix family (`m5d`, `c5d`, `r5d`, `m6gd`, …), and every GPU family.
`i3en`, `im4gn` and `is4gen` all have local **NVMe SSD**, and they are the
families most likely to be confused with dense storage because their capacities
are also large (`i3en.24xlarge` = 60,000 GiB). They are **correct today** and
their projected dollars must not change.

Be conservative: match on the family token only, from an explicit set. Do not
match on `prefix in ("d", "h")` — `dl1` (Habana Gaudi) has prefix `d` under some
parses and is an accelerator instance, not dense storage.

---

## 4. Tasks

### T1 — Record the storage media

**a. Generator** (`scripts/refresh_ec2_specs.py`): alongside `storage_gb`,
persist the media parsed from the same AWS `storage` attribute string. Add a
sibling of `_parse_storage`:

```python
def _parse_storage_media(storage: str) -> str | None:
    """'24 x 14000 HDD' -> 'hdd'; '2 x 1900 NVMe SSD' -> 'nvme'."""
    s = (storage or "").lower()
    if not s or "ebs only" in s:
        return None
    if "hdd" in s:
        return "hdd"
    if "nvme" in s or "ssd" in s:
        return "nvme"
    return None
```

and write it as `"storage_media"` into `spec_entry` next to
`scripts/refresh_ec2_specs.py:155`, only when it is not `None`.

**Do not run the generator** as part of this work order — it downloads the full
AWS price list. Existing entries keep having no `storage_media`; that is why (b)
exists.

**b. Fallback family list** in `scripts/apply_static_mappings.py`, near the
other module-level constants (around `_EC2_TYPES`, line 621-623):

```python
# AWS dense-storage families ship spinning HDD, not NVMe flash. GCP has no
# dense-HDD local-storage product at all, so this capacity must never be priced
# against the "SSD backed Local Storage" SKU (~$0.08/GiB-mo, ~10x too high).
_DENSE_HDD_FAMILIES = {"d2", "d3", "d3en", "h1"}


def _local_storage_media(itype, type_entry):
    """'hdd' | 'nvme' | None for an instance type.

    Prefers the storage_media field written by refresh_ec2_specs.py; falls back
    to the dense-HDD family list for catalogs generated before that field
    existed. Returns None when there is no local storage at all.
    """
    media = (type_entry or {}).get("storage_media")
    if media:
        return media
    fam = (itype or "").strip().lower().split(".")[0]
    if fam in _DENSE_HDD_FAMILIES:
        return "hdd"
    return "nvme" if (type_entry or {}).get("storage_gb") else None
```

### T2 — `map_gce_row`: do not price HDD capacity as Local SSD

In `scripts/apply_static_mappings.py`, inside the block that begins at line
8156, after `type_entry` and `real_local_ssd_gib` are read (lines 8164-8165),
branch **before** the existing `if real_local_ssd_gib and ... > 0:` at 8167:

```python
if _local_storage_media(clean_itype, type_entry) == "hdd":
    ssd_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": None,
        "component": "storage",
        "strategy": "ignore",
        "unit_multiplier": 0.0,
        "gcp_region": gcp_region,
        "passthrough_reason": "no_gcp_equivalent",
        "projection_note": (
            f"Dense-storage instance local disk ({real_local_ssd_gib:g} GiB spinning HDD) — "
            f"no GCP equivalent: GCP Local SSD is flash-only and has no dense-HDD tier, so this "
            f"capacity is NOT priced (pricing it at Local SSD rates over-projects ~10x). "
            f"CPU/RAM are projected normally; the disk capacity needs a manual decision "
            f"(Persistent Disk Standard or Cloud Storage) before this instance is migratable."
        ),
        "mapping_confidence": 0.50,
        "is_workload": True,
        "break_down": True,
    }
elif real_local_ssd_gib and real_local_ssd_gib > 0:
    ... unchanged existing entry ...
else:
    ... unchanged existing entry ...
```

Keep `mappings.append(ssd_entry)` and the `if ssd_sku and ... == "map"` guard
below it exactly as they are — the new branch sets no `gcp_sku_id`, so that
guard simply does not fire.

### T3 — `map_gpu_row`: the same guard

At `scripts/apply_static_mappings.py:8445-8448`, add the same media check
before building the entry:

```python
if real_local_ssd_gib and real_local_ssd_gib > 0 and \
        _local_storage_media(clean_itype, type_entry) != "hdd":
```

**No GPU family is dense-HDD today**, so this changes no output. It is a guard
against a future `storage_media` refresh or a new family, and it keeps the two
near-duplicate sites from drifting apart again. Do not restructure this site
beyond the added condition.

### T4 — Tests

Add to `tests/test_pricer_correctness.py`, next to the existing P-11 tests
(`test_p11_family_mapper_local_ssd_true_capacity` at line 309 and
`test_p11_family_mapper_gpu_local_ssd` at 328 — read them first and copy their
shape):

1. `test_dense_hdd_storage_not_priced_as_local_ssd` — `d3en.8xlarge` through
   `map_gce_row` produces exactly one `component == "storage"` entry with
   `strategy == "ignore"`, `unit_multiplier == 0`, and `"no GCP equivalent"` in
   its note. Assert its multiplier is **not** `224000 / 730`.
2. `test_dense_hdd_cpu_and_ram_still_mapped` — the same row still yields `core`
   and `ram` entries with `strategy == "map"` and multipliers 32 and 128.
3. `test_d2_and_h1_also_treated_as_hdd` — `d2.8xlarge` and `h1.16xlarge` both
   produce an ignored storage component.
4. `test_nvme_families_unchanged` — `i3en.24xlarge` keeps
   `unit_multiplier == 60000 / 730` and `strategy == "map"`; `im4gn.4xlarge`
   and `m5d.large` likewise keep a priced storage component. This is the
   regression guard against over-applying the fix.
5. `test_local_storage_media_helper` — `_local_storage_media("d3en.8xlarge",
   entry)` is `"hdd"`; `("i3en.24xlarge", entry)` is `"nvme"`; an explicit
   `{"storage_media": "nvme"}` on a `d3`-family entry wins over the family list;
   a type with no local storage returns `None`.

---

## 5. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_static_mappings.py scripts/refresh_ec2_specs.py
```

- Full suite passes. The baseline is **1,024 tests** (measured on this branch,
  2026-10-22 — note a stale `1,019` appears in an older work order); you should
  end at 1,029.
- **Expected dollar change on real bills: none.** No bill in the corpus carries
  a `d2`/`d3`/`d3en`/`h1` line item; the only thing exercising this path is a
  synthetic fixture. If a real-bill total moves, you have changed an NVMe
  family by mistake — that is a bug, not progress.
- On a synthetic `d3en.8xlarge` row, the storage component's projected cost goes
  from ~$17,920/month to **$0**, and the row's note states why.
- `i3en.24xlarge`, `im4gn.*`, `is4gen.*`, `m5d.*` and all GPU families keep
  identical projected dollars. Check this explicitly; it is the whole risk of
  this change.

---

## 6. Do NOT

- Do **not** set the dense-HDD storage component to `strategy="passthrough"`.
  This is the trap in this work order. A passthrough component is priced at
  the **whole line item's** `aws_amortized_cost`
  (`scripts/projection_view.py:157`), deduplicated only among passthrough rows
  of that key (`passthrough_rank`, `scripts/projection_view.py:56-61`). The
  core and ram components are `map`, so a passthrough storage component would
  **add the entire instance's AWS cost on top of them** — a fresh ~2x
  over-projection replacing the old 4x one. `ignore` is the correct strategy
  for a component that must not be priced.
- Do **not** apply this to `i3`, `i3en`, `i4i`, `im4gn`, `is4gen`, `z1d`, any
  `*d`-suffix family, or any GPU family. They are NVMe and correct today. §3.
- Do **not** broaden the condition at line 8156, or re-key it on
  `prefix in ("d", "h")`. Match the family token against the explicit set.
- Do **not** substitute a different GCP SKU (Persistent Disk Standard,
  Hyperdisk Throughput, Cloud Storage) to "keep a number in the cell". PD
  Standard is *network* storage with different throughput and failure
  semantics; choosing it is a migration-architecture decision, not a mapping.
  See §7.
- Do **not** run `scripts/refresh_ec2_specs.py` to regenerate
  `data/ec2-instance-types.json`. It pulls the full AWS price list and would
  rewrite 1,310 entries inside a change meant to be reviewable. The fallback
  family list exists precisely so you do not need to.
- Do **not** touch `_parse_storage`'s numeric behaviour. `storage_gb` values
  are correct; only the media was missing.
- Do **not** add a new `passthrough_reason`. `no_gcp_equivalent` already exists
  and is `kind: intentional` (`data/passthrough_reasons.json:8-12`), which is
  what keeps repair passes and the defect-passthrough budget gate off this row.

---

## 7. Out of scope, deliberately

**Choosing a GCP destination for dense-HDD capacity.** 224 TB of local HDD has
no like-for-like target: Persistent Disk Standard, Hyperdisk Throughput and
Cloud Storage each change throughput, durability and failure semantics. Picking
one is an architecture decision with a cost consequence and needs a human. This
work order makes the gap **visible and unpriced**; it does not fill it.

**The `h1`/`d2` generation question.** `d2` and `h1` are previous-generation
and may be absent from new bills entirely. Leave them in the list anyway — they
cost nothing to carry and an old bill may contain them.

---

## 8. Done

- [ ] `_DENSE_HDD_FAMILIES` and `_local_storage_media()` exist in
      `scripts/apply_static_mappings.py`, and prefer a `storage_media` field
      over the family list.
- [ ] `refresh_ec2_specs.py` persists `storage_media` (generator not run).
- [ ] `map_gce_row` emits an ignored, explained storage component for dense-HDD
      instances; core and ram are untouched.
- [ ] `map_gpu_row` carries the same guard, with no change to current output.
- [ ] 5 new tests pass; full suite passes (1,029).
- [ ] NVMe families' projected dollars verified unchanged.
- [ ] Summary states that no real-bill total moved, and lists anything noticed
      but not fixed.

---

## 9. How the industry handles this

Three points, each tied to a task above.

### 9.1 "No equivalent" is a first-class outcome, not a zero

Cloud migration assessment tooling treats an unmatched resource as an explicit
finding rather than silently costing it. AWS's Migration Evaluator and Azure
Migrate both report workloads they cannot size as *needs review* alongside the
priced ones, so the total is never quietly completed with a guess. Google's own
migration guidance for AWS-to-GCP compute is explicit that Local SSD is
**flash-only** and that there is no dense-HDD local option — the correct answer
is a redesign (network storage or object storage), not a substitution.

**This is T2's `ignore` plus a stated note**, and it is why §7 refuses to pick a
destination SKU.

### 9.2 Cost models should fail loudly on a wrong premise, not quietly on wrong arithmetic

FinOps practice distinguishes a *rate* error from a *scope/assumption* error;
the second is the dangerous one because every downstream number stays
self-consistent. The FinOps Foundation's guidance on forecasting and the FOCUS
specification's insistence on explicit, declared charge attributes both push the
same way: record what a charge **is**, do not infer it from a number. Our
`storage_gb` with no media is exactly an undeclared attribute, and the 4x error
passed every gate because the multiplication was flawless.

**That is §2 and T1** — persist the media rather than letting the mapper assume.

### 9.3 Provenance beats re-derivation

The AWS Price List already states `"24 x 14000 HDD"`. Parsing it down to a
number and then trying to recover the lost dimension from family-name heuristics
is strictly worse than keeping it. The family list in T1b is a bridge for
catalogs generated before the field existed, not the intended long-term source
of truth — which is why `_local_storage_media` checks `storage_media` first.

**Sources:**
[AWS EC2 instance store volumes (D3/D3en/H1 are HDD)](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/InstanceStorage.html) ·
[AWS D3/D3en dense storage instances](https://aws.amazon.com/ec2/instance-types/d3/) ·
[Google Cloud Local SSD overview (flash only)](https://cloud.google.com/compute/docs/disks/local-ssd) ·
[Google Cloud — migrate from AWS to Google Cloud: compute](https://cloud.google.com/architecture/migrate-amazon-ec2-to-compute-engine) ·
[AWS Migration Evaluator](https://aws.amazon.com/migration-evaluator/) ·
[Azure Migrate assessment — readiness and unsupported findings](https://learn.microsoft.com/azure/migrate/concepts-assessment-calculation) ·
[FOCUS specification](https://focus.finops.org/focus-specification/)
