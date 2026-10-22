# DESIGN — Conversion contracts: one place that knows how every charge converts

Status: **proposal, for discussion.** This is the HLD you asked for, with the
hard parts named rather than glossed.

Supersedes `WORKORDER_unit_dimensions.md` by absorbing it: dimensions become
one field of a contract rather than a separate mechanism.

---

## 1. The problem, stated precisely

Three bugs, same shape:

| bug | AWS side | GCP side | why dimension-checking misses it |
|---|---|---|---|
| Glacier **120x** | Glacier Flexible, GB-Mo | mapped to GCS *Standard* | both are bytes x time |
| S3 requests **50x** | per **1,000** requests | per **10,000** operations | both are counts |
| `GiBy.h` (open) | instance-hours | memory GiB-hour | caught by dimensions |

Only the third is a dimension error. The first two are **same dimension,
different thing** — a different storage tier, and a different counting
quantum.

So the question a correct system must answer is not *"are these the same kind
of quantity?"* It is:

> **"Is this GCP charge the same thing as that AWS charge — and if so, by what
> arithmetic?"**

Nothing in the pipeline holds that answer. It is implicit in 34 mapper
functions across 8,702 lines, which is why each of these was found and fixed
one incident at a time.

---

## 2. What is scattered today

| fact | lives in | machine-readable |
|---|---|---|
| how to recognise an AWS charge | `data/service_specs.json` | yes |
| which GCP service it targets | `data/service_map.json` | yes |
| which GCP **SKU** | mapper code | no |
| the AWS **unit** and its quantum (per 1k, per 10k) | nowhere | no |
| the GCP **unit** | `catalog.duckdb` `usage_unit` | yes, unused for checking |
| the **tier/class** on each side | prose, or inferred in code | no |
| the **conversion arithmetic** | mapper code, per mapper | no |
| why the two are considered equivalent | a comment, sometimes | no |

`data/pricing_matrix.json` — the per-SKU decision table originally specified —
does not exist.

The three JSON files that do exist are keyed the same way (service, then
charge type). **The missing facts belong on that same key.** A fourth file
would be the scattering you want to avoid.

---

## 3. The proposal: one contract per charge type

Extend the existing `charge_type` entry in `service_specs.json` with a
`conversion` block. One key, one place, read by everything.

```jsonc
{
  "id": "s3_storage_glacier_flexible",
  "description": "S3 Glacier Flexible Retrieval storage",
  "match": { "usage_type_re": "GlacierFlexible|Glacier-Flexible" },
  "mechanic": "object_storage",

  "conversion": {
    "kind": "sku",                 // sku | formula | passthrough | ignore

    "aws": {
      "unit": "GB-Mo",
      "dimensions": { "data": 1, "time": 1 },
      "quantum": 1,                // billed per 1 GB-Mo
      "attributes": { "storage_class": "glacier_flexible" }
    },

    "gcp": {
      "service": "Cloud Storage",
      "sku_match": { "description_re": "Coldline Storage" },
      "unit": "GiBy.mo",
      "dimensions": { "data": 1, "time": 1 },
      "quantum": 1,
      "attributes": { "storage_class": "coldline" }
    },

    "assert": ["dimensions_equal", "attributes_equivalent"],

    "equivalence": {
      "storage_class": {
        "claim": "glacier_flexible ~ coldline",
        "reason": "Coldline's 90-day minimum storage duration matches Glacier Flexible's 90-day minimum. Archive's 365-day minimum does not.",
        "decided_by": "human",
        "decided_on": "2026-03-11"
      }
    },

    "factor": "derived"            // from units + quanta; never hand-typed
  }
}
```

### How each bug becomes impossible

**Glacier 120x** — `attributes.storage_class` is `glacier_flexible` on one
side and would have to be declared equivalent to whatever GCP class is
targeted. Mapping it to `standard` fails `attributes_equivalent`, because no
equivalence claim exists for `glacier_flexible ~ standard`. The bug becomes a
**missing declaration**, which fails loudly, rather than a silent mismatch.

**Requests 50x** — `quantum: 1000` vs `quantum: 10000`. The factor is
**derived** from those two numbers, not typed by a human and not absorbed into
`unit_multiplier`. Getting it wrong requires mis-stating the quantum, which is
a single visible number that can be checked against the SKU description.

**`GiBy.h`** — `dimensions_equal` fails. This is the dimension work, now one
assertion among several instead of a separate module.

---

## 4. The four assertions

| assertion | catches | mechanical? |
|---|---|---|
| `dimensions_equal` | hours vs memory-hours | fully |
| `quantum_declared` | per-1k vs per-10k | fully |
| `attributes_equivalent` | Glacier vs Standard, SSD vs HDD, Arm vs x86 | **needs a declared equivalence table** |
| `magnitude_plausible` | everything else — the ratio bands you already have | partly |

Only the third needs human input, and that is unavoidable: **no API tells you
that Glacier Flexible is like Coldline.** That judgement is the actual
intellectual content of your product. The design's job is to make it
*declared, reasoned and reviewable* instead of implicit in a regex.

### Equivalence tables

Per attribute family, declared once and reused:

```jsonc
"storage_class": {
  "aws":  ["standard","standard_ia","onezone_ia","glacier_instant",
           "glacier_flexible","deep_archive"],
  "gcp":  ["standard","nearline","coldline","archive"],
  "equivalent": [
    ["standard",         "standard", "same access profile"],
    ["standard_ia",      "nearline", "both 30-day minimum, ms access"],
    ["glacier_instant",  "coldline", "both 90-day minimum, ms retrieval"],
    ["glacier_flexible", "coldline", "90-day minimum matches; Archive's 365 does not"],
    ["deep_archive",     "archive",  "both 365-day minimum"]
  ]
}
```

That table is five lines and it is the entire Glacier bug class, settled once.

---

## 5. Where the data comes from — honestly

| field | source | effort |
|---|---|---|
| AWS `unit` | Price List API — already fetched for product codes | **free, fetchable** |
| AWS `quantum` | the price-dimension **description** ("per 1,000 requests") | parse, verify by hand |
| GCP `unit` | `catalog.duckdb.usage_unit` | **free** |
| GCP `dimensions` | parsed from the unit (it is a formal notation) | **free** |
| GCP `attributes` | the SKU **description** text ("Coldline Storage") | parse, then **verify** |
| **equivalence claims** | **a human** | the real work |

So roughly two-thirds is generated and one-third is judgement. The generated
parts should be *proposed by a script and verified against the catalog*, the
same propose-then-verify pattern as the schema mapper. The judgement parts are
written once, carry a reason, and are reviewable in a diff.

---

## 6. Size, honestly

Not per SKU. 85,952 GCP SKUs is unmaintainable and unnecessary — SKUs within a
family share a contract.

Per charge type: **54 services x roughly 6 charge types ≈ 350 contracts.** Of
those, perhaps 80 carry non-trivial conversion (a quantum, a tier, a formula);
the rest are plain `passthrough`, `ignore`, or a 1:1 unit match.

That is weeks of careful work, not days. It is also the first artifact that
would let you answer "is our Lambda mapping right?" by reading one entry
rather than tracing 8,702 lines.

---

## 7. The failure mode that would waste all of it

**Building the contracts and leaving the mappers hardcoded.**

You would then have a document describing what the pipeline *should* do, next
to code doing something else — and no way to tell when they diverged. This
project has that exact pattern three times already:

- `service_specs.json` describes classification; `classify_mechanics` still
  does it.
- `check_catalog_age.py`'s docstring says it is wired as a pre-script; it is
  not.
- `runValidatorGate`'s comment says it fails closed; the caller ignores it.

So the sequencing rule is absolute: **a contract must be read by the code the
day it is written.** Concretely — convert one mapper to read its contract
before writing the second contract. If `map_object_storage` cannot be driven
by `s3_storage`'s contract, the schema is wrong and you have learned that for
the price of one entry rather than 350.

A test must assert: every charge type with `conversion.kind == "sku"` is
actually consumed by its mapper. An unconsumed contract fails the build.

---

## 8. What this does not solve

- **It does not pick the SKU.** It validates and converts a choice already
  made, and declares what a valid choice looks like. Selection stays in the
  mappers.
- **It does not make equivalence claims correct.** `glacier_flexible ~
  coldline` is a human judgement that could be wrong; the design makes it
  visible and arguable, not true.
- **It does not cover formulas by itself.** ACU → vCPU+RAM, Redshift → BQ
  slots are modelling decisions with their own arithmetic. They get
  `kind: "formula"` and an expression, but the expression is still a claim.
- **Attributes beyond the obvious.** Redundancy (regional vs multi-regional),
  commitment, network tier all shift price within one dimension and tier. The
  attribute list will grow as bugs find it. This bounds the problem; it does
  not finish it.

---

## 9. Suggested order

1. **One contract, one mapper.** `s3_storage` → `map_object_storage`. Prove the
   schema drives real code. Keep everything else as it is.
2. **The storage-class equivalence table.** Five lines; closes the Glacier
   class.
3. **Quantum on request-type charges.** Closes the per-1k/per-10k class.
4. **Generate the mechanical two-thirds** for the remaining services; leave
   `equivalence` blank and failing until a human fills it.
5. **Dimensions as an assertion** — the `unit_dimensions` work order folded in
   here rather than run separately.

Step 1 is the decision point. If a contract cannot drive one mapper, the rest
should not be written.
