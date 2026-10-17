# DESIGN — Field resolution: one mechanism for getting every field from every format

Status: **proposal, for decision.** Not a work order. Nothing here is implemented.

---

## 1. The general problem

`product/vcpu` was one instance. The general shape:

> A field the pipeline needs **exists in the bill**, under some name, in some
> format — and we look in exactly one place, miss it, and carry on without it.

Three ways it goes wrong today, all observed:

| | observed |
|---|---|
| **We never look at the column** | A customer CUR carries `product/vcpu`, `product/memory`, `product/instanceType`. `ingest.py` maps `operatingsystem`, `licensemodel`, `databaseengine`, `volumetype`, `region` — not those three. 164 rows / $182,665 on one bill had specs in the bill that our 357-entry local table lacked. |
| **The field is present under a different name** | PDF/XLSX bills put the instance type inside `operation` prose; CUR puts it in `usage_type` and in its own column. Each format gets its own hand-written extraction, so a fix for one format does not help the others. |
| **A lower-authority source silently wins** | We regex the type out of text and look specs up in a static table, when the row itself states them. The table needs `refresh_ec2_specs.py` to chase AWS's new families — a treadmill that exists only because we do not read the column. |

The fix is not "add three columns to ingest". It is to stop writing field
extraction by hand, per field, per format.

---

## 2. What already exists, and why it is nearly right

`ingest.py` has this helper:

```python
def c(names):
    for n in names:
        clean_n = n.lower().replace("/", "_").replace(" ", "_")
        if clean_n in col_map:
            return f'"{col_map[clean_n]}"'
    return "NULL"
```

That is a **first-match-wins resolver over a list of candidate column names** —
exactly the right idea. It is used 37 times. Its limits:

- It only knows **one strategy** (read a column). Regex-from-text, table
  lookup, derivation and LLM inference are hand-written elsewhere, per field.
- The alias lists live **inline inside SQL f-strings**, so they are not
  inspectable, testable, or shared across the 4 format branches.
- It records **nothing** about which source answered.

The design below is `c()` generalised: more strategies, declared in data,
applied uniformly to every field and every format.

---

## 3. Principles

**P1 — Highest authority wins, and authority is declared.** The bill's own
column beats text parsing beats a local table beats a model.

**P2 — Every resolution records its source.** `instance_vcpus_source ∈
{cur_column, text_regex, spec_table, derived, llm, unresolved}`. Same pattern
as `rate_source` and `passthrough_reason`. "Read off the bill" and "guessed
from a table" must never look alike.

**P3 — One declaration per field, not per format.** A field's resolvers list
covers CUR, PDF, XLSX and flat CSV together. Adding a format adds candidate
names, not a new code path.

**P4 — Set-based, not row-based.** Resolvers compile to SQL and run as one
statement per field over DuckDB. See §7.

**P5 — Unresolved is a reported state, not a silent NULL.** Already half-built:
`diag()` emits `instance_spec_unresolved` with rows and dollars.

---

## 4. The registry: `data/field_resolution.json`

```jsonc
{
  "instance_vcpus": {
    "sql_type": "INTEGER",
    "aggregation": "attribute",          // see section 6 — this matters
    "required_for_mechanics": ["compute_breakdown", "compute_arm",
                               "compute_burstable", "compute_windows"],
    "validate": { "min": 0.5, "max": 1024 },
    "resolvers": [
      { "kind": "column", "authority": "bill",
        "names": ["product/vcpu", "vcpu", "vcpus", "v_cpu", "cores"] },

      { "kind": "regex", "authority": "derived",
        "from": ["usage_type", "operation", "product"],
        "pattern": "(\\d+)\\s*v?cpus?\\b", "group": 1 },

      { "kind": "table", "authority": "reference",
        "file": "ec2-instance-types.json",
        "key_field": "instance_type", "value": "vcpus",
        "key_transform": ["strip_suffix:.search", "strip_prefix:kafka.",
                          "strip_prefix:db.", "strip_prefix:cache."] },

      { "kind": "formula", "authority": "derived",
        "expr": "instance_ram_gb / 4",
        "when": "instance_family IN ('c','m')" }
    ]
  },

  "instance_type": {
    "sql_type": "VARCHAR",
    "aggregation": "key",
    "resolvers": [
      { "kind": "column", "authority": "bill",
        "names": ["product/instanceType", "instancetype", "instance_type"] },
      { "kind": "regex", "authority": "derived", "from": ["usage_type"],
        "pattern": "(?:BoxUsage|SpotUsage|HeavyUsage|NodeUsage|Multi-AZUsage):([a-z0-9][a-z0-9.\\-]*)" },
      { "kind": "regex", "authority": "derived", "from": ["operation"],
        "pattern": "\\b([a-z]+\\d+[a-z]*\\.(?:nano|micro|small|medium|large|\\d*xlarge|metal))\\b" }
    ]
  }
}
```

One entry per field, ~31 fields. Each resolver is tried in order; the first
that yields a value passing `validate` wins and stamps `<field>_source`.

### Resolver kinds

| kind | reads | compiles to |
|---|---|---|
| `column` | a named column, any alias | `COALESCE(NULLIF("col",''), …)` |
| `regex` | text fields already resolved | `regexp_extract(field, pat, grp)` |
| `table` | a bundled JSON keyed by another field | temp-table `LEFT JOIN` |
| `formula` | other resolved fields | a SQL expression |
| `llm` | the residue only | batched, out-of-band (§5) |

`authority` is `bill` > `derived` > `reference` > `inferred`. The engine
asserts resolvers are listed in non-increasing authority, so a registry edit
cannot accidentally let a table beat the bill.

---

## 5. Hybrid: where the model belongs

The LLM is **the last resolver, on the residue only, batched** — never in the
per-row path.

After the deterministic resolvers run, the engine has a small set of
`(field, distinct unresolved key)` pairs. For `instance_vcpus` on the customer
bill that is ~100 distinct instance types, not 164 rows — resolution is keyed
on the *distinct value*, not the row, so cost scales with vocabulary, not bill
size.

Three rules keep it honest:

1. **It answers about a key, not a row**: "how many vCPU is `c1.24xlarge`?" —
   cacheable, reusable across bills, verifiable.
2. **Its answers are written to a learned-facts file** (the existing
   `data/learned-product-codes.json` pattern), so the second bill never asks
   again, and a human can review what it claimed.
3. **`<field>_source = 'llm'`** and it is subject to `validate`. A model saying
   a 2-vCPU instance has 900 vCPU is rejected, not stored.

This is the hybrid: deterministic resolution for everything expressible as
data, a model only for genuinely open-world lookups, and its output demoted to
a reviewable cache rather than a per-run guess.

---

## 6. The constraint that makes this non-trivial

`ingest.py` aggregates before the catalog exists:

```sql
GROUP BY product, usage_type, operation, aws_region, pricing_model,
         line_item_type, is_workload, license_model, operating_system,
         database_engine, deployment_option, volume_type, pricing_unit,
         billing_month
```

On the customer CUR that is **1,245 raw rows → 47 catalog rows.**

`product/instanceType`, `product/vcpu` and `product/memory` are **not in that
key**. So simply reading them is not enough — they would be dropped by the
aggregation, or worse, silently take an arbitrary row's value.

Hence `aggregation` is a required property of every field in the registry:

| role | meaning | compiles to |
|---|---|---|
| `key` | a dimension; rows differing in it must not merge | added to `GROUP BY` |
| `sum` | a measure | `SUM(...)` |
| `attribute` | functionally dependent on the key | `MAX(...)`, plus a conflict check |
| `derived` | computed after aggregation | post-`GROUP BY` update |

And an `attribute` gets a **conflict assertion**: if two raw rows sharing the
group key disagree on it, that is a modelling error and must be reported, not
silently `MAX`-ed. `product_code` already does the `max()` trick by hand with a
comment explaining it is safe — this makes that reasoning checkable instead of
a comment.

**This is the part a naive implementation gets wrong**, and the reason this is
a design rather than a patch.

---

## 7. Speed

One compiled SQL statement per field, over the pre-aggregation table.
~31 fields × 4 resolvers ≈ 120 expressions, folded into ~31 `UPDATE`s — or a
single `SELECT` with 31 `COALESCE` chains, which is what the engine should
emit. DuckDB executes those vectorised.

What this must **not** be is a Python loop over rows. At 1,245 raw rows the
difference is invisible; at a real enterprise CUR of several million it is the
difference between seconds and hours. The registry is declarative precisely so
it can compile to set operations.

The LLM resolver runs once per distinct unresolved key per bill, with a
persistent cache, so it does not scale with bill size at all.

---

## 8. Two things that fall out for free

**Unmapped-column discovery.** After resolution, the engine knows which of the
bill's columns it consumed. Report the ones it did not:

```
unconsumed columns with non-empty values:
  product/physicalProcessor    1,245 rows   e.g. "AWS Graviton2 Processor"
  product/clockSpeed             980 rows   e.g. "2.5 GHz"
```

That is how "the format is dissimilar but the data is present" stops being
something a human notices by accident. It is also how we would have found
`product/vcpu` without a customer bill exposing it.

**Consistency rules.** A separate declarative section, because a field can be
individually valid and jointly impossible:

```jsonc
"consistency_rules": [
  { "id": "windows_not_on_arm",
    "when": "operating_system ILIKE 'Windows%' AND instance_arch = 'arm64'",
    "severity": "error",
    "message": "Windows does not run on Graviton; one of OS or arch is wrong" },
  { "id": "compute_row_without_size",
    "when": "mechanic_group LIKE 'compute%' AND instance_vcpus IS NULL",
    "severity": "error" }
]
```

The first would have caught the Windows-on-`c7gn` rows at ingest instead of
after a projection.

---

## 9. What this does NOT promise

You asked for something that handles every possible case. I can't honestly
claim that, and a design that claims it is lying:

- **New field names we have never seen still need adding.** The registry makes
  that one line instead of a code change, and §8's unconsumed-column report
  makes it discoverable — but it is not automatic.
- **A bill that genuinely lacks a field stays unresolved.** The guarantee is
  that it is *reported with dollars*, not that a number appears.
- **The LLM resolver can be wrong.** `validate` bounds it and the cache makes
  it reviewable; neither makes it correct.

What the design does promise: **every field is resolved by the highest
authority available, every resolution says where it came from, and every
failure is visible with its dollar impact.** "Error-free" is not attainable;
"no silent error" is, and that is the achievable form of the requirement.

---

## 10. Migration

Each step is independently verifiable, and none changes a number until step 3.

- **M1 — Registry describing today's behaviour exactly.** Lift the 37 `c()`
  alias lists into `data/field_resolution.json`, compile them back to the same
  SQL. Zero behaviour change; proven by identical catalog output on the corpus.
- **M2 — Record `<field>_source`.** Still no behaviour change; now you can see
  which fields come from where, per bill and per format.
- **M3 — Add the missing high-authority resolvers** (`product/instanceType`,
  `product/vcpu`, `product/memory`). **This moves numbers** — 164 rows /
  $182,665 on the customer bill gain real specs. Verified by comparing CUR
  values against the table where both exist, before switching precedence.
- **M4 — Aggregation roles + conflict assertions** (§6).
- **M5 — Unconsumed-column report and consistency rules** (§8).
- **M6 — LLM resolver for the residue**, with the learned-facts cache.

M1 and M2 are pure cleanup and worth doing even if the rest is deferred.

---

## 11. Open choices

1. **Scope**: `aws_li_catalog` only, or also the mapping table? (Recommend:
   catalog only. The mapping table already has `row_state_registry.json`; these
   are siblings, not one registry.)
2. **M3 precedence**: before switching, diff CUR-stated specs against the local
   table on every row where both exist. If they ever disagree, which wins?
   (Recommend: the bill, and report the disagreement — but let's look at the
   data first.)
3. **LLM resolver**: build it in M6, or stop at deterministic + report? The
   residue on the customer bill is 104 rows / $6,755 where neither the bill nor
   the table has specs.
