# DESIGN — One field registry, and an LLM schema-mapper that cannot lie

Status: **proposal, for decision.** Not a work order. Supersedes
`DESIGN_field_resolution.md`, which this incorporates and extends.

The risky part of this design — "can a model's column mapping be trusted?" —
was **prototyped and tested against the real bills** before writing. Results in
§6. They changed the design.

---

## 1. What this solves

Three requirements, one mechanism:

1. **Every field, every service, in one place** — not 37 alias lists inlined in
   SQL f-strings across 4 format branches.
2. **Handle any bill shape** — renamed columns, missing columns, new columns,
   typos in headers.
3. **Debuggable** — for any field on any row, one lookup answers "where did
   this come from, and was it trusted?"

---

## 2. Measured starting point

Header coverage of today's `c()` alias set, against the real bills:

| bill shape | recognised | unrecognised |
|---|---|---|
| AWS CUR (50 cols) | 10/14 checked | `product/instanceType`, `product/vcpu`, `product/memory` |
| vendor export (12 cols) | 6/12 | `Unit Price`, `Currency`, `Account ID`, `Account Reference`, `Cloud Account`, `Resource` |
| vendor export (10 cols) | 6/10 | `Unit Price`, `Currency`, `Account ID`, `Resource` |
| simple CSV (6 cols) | 6/6 | — |
| `usage-june-2026.csv` (2 cols) | 0/2 | `Cloud cccount`, `eventussecurity` |

Two things this shows:

- The gap is mostly **columns we never added**, not exotic renames. `Currency`
  and `Unit Price` are present in three bills and unmapped, and there is a
  whole multi-currency code path downstream.
- **`Cloud cccount`** is a real typo in a real header. No hand-maintained alias
  list will ever contain it. That single column is the argument for the model
  step.

---

## 3. The registry — one file, all fields

`data/field_registry.json`. One entry per logical field, covering every format
and every service.

```jsonc
{
  "instance_vcpus": {
    "sql_type": "INTEGER",
    "aggregation": "attribute",        // key | sum | attribute | derived  (section 7)
    "required_for_mechanics": ["compute_breakdown","compute_arm",
                               "compute_burstable","compute_windows"],

    // What a column must LOOK LIKE to be this field. Used to verify any
    // proposed mapping, whether it came from the alias list or the model.
    "contract": { "kind": "number", "int_like": true, "min": 0.5, "max": 1024 },

    // Ordered, highest authority first. First to yield a contract-passing
    // value wins and stamps instance_vcpus_source.
    "resolvers": [
      { "kind": "column", "authority": "bill",
        "names": ["product/vcpu", "vcpu", "vcpus", "cores"] },
      { "kind": "regex", "authority": "derived",
        "from": ["usage_type","operation"], "pattern": "(\\d+)\\s*v?cpus?\\b" },
      { "kind": "table", "authority": "reference",
        "file": "ec2-instance-types.json", "key_field": "instance_type",
        "value": "vcpus" }
    ]
  }
}
```

Resolver kinds: `column`, `regex`, `table`, `formula`, `llm`.
Authority order `bill > derived > reference > inferred` is asserted at load, so
a table can never silently outrank the bill.

**This file is the single place.** Adding a field, a format, or an alias is one
entry. Nothing in `ingest.py` carries a column name any more.

---

## 4. The schema-mapper — model on the header, once

The model never sees a data row's worth of work. It runs **once per bill**, on
the header plus a tiny sample, and emits a mapping.

```
input   : column names + 3 sample values each + per-column profile (section 6)
output  : ColumnMapping[]   — structured, schema-constrained
cost    : one short call per bill, O(1) in bill size
```

Structured output, so it cannot invent a field:

```python
from enum import Enum
from pydantic import BaseModel, Field

FieldName = Enum("FieldName", {k: k for k in REGISTRY})   # generated FROM the registry

class ColumnMapping(BaseModel):
    column: str
    field: FieldName | None            # None = "not one of ours"
    confidence: float = Field(ge=0, le=1)
    reason: str

class SchemaMapping(BaseModel):
    mappings: list[ColumnMapping]
    unmapped_but_interesting: list[str]   # columns it thinks we should want
```

Because `FieldName` is generated from the registry, the model is structurally
incapable of naming a field that does not exist. That is the first guardrail.
The second is §5.

**When it runs:** concurrently with the file load. Headers are available as
soon as the file is opened, so the call overlaps parsing and blocks only the
field-resolution step. On a cache hit (§8) it does not run at all.

---

## 5. The model proposes, the data disposes

Every mapping — from the alias list *or* the model — is checked against the
field's `contract` using the column's actual values. **A proposal that fails
its contract is rejected, regardless of source.**

This is the part I prototyped rather than assumed.

---

## 6. Test results — three rounds, each one changed the design

The verifier was prototyped and run against the real bills before this design
was finalised. It was wrong twice.

### Round 1 — value-shape contracts only: 2 of 4 planted errors caught

| planted proposal | verdict | |
|---|---|---|
| `lineItem/UsageType` -> `instance_vcpus` | REJECT | 0% of values numeric |
| `Location` -> `region` | REJECT | column is empty (a real finding — it is blank) |
| `identity/LineItemId` -> `usage_type` | **accepted** | text with many distinct values |
| `Resource` -> `service` | **accepted** | same |

Shape alone cannot separate a hash from a usage type. Added a second signal,
**distinct-value ratio**, which separated them cleanly (`LineItemId` 1.00,
`Resource` 0.94, `Meter` 0.03) — 4 of 4.

### Round 2 — the dangerous test: does it reject CORRECT mappings?

Rejecting bad mappings is half the job. A verifier that also rejects good ones
blocks real data. Ran 36 ground-truth-correct mappings across 4 real bills,
with ground truth derived from column semantics rather than from what the
verifier happens to like.

**False-rejection rate: 13.9% — 5 of 36 correct mappings blocked.**

| correct mapping | wrongly rejected because |
|---|---|
| `lineItem/UsageAccountId` -> `account_id` | "numeric column, not text" — AWS account IDs *are* 12-digit numbers |
| `Account ID` -> `account_id` | same |
| `lineItem/LineItemDescription` -> `description` | ratio 0.96 — a description *is* near-unique by nature |
| `Description` -> `description` | ratio 0.90 — same |
| `product/instanceType` -> `instance_type` | ratio 0.90 — legitimately high on a 1,064-type bill |

The flaw: **one global ID-like threshold applied to every text field.** The
signal was right; applying it uniformly was not. Cardinality is a property of
the *field*, not of text in general.

### Round 3 — cardinality declared per field

Two changes:

- Each text field declares an expected cardinality class:
  `categorical` (<=0.50: service, region, operating_system, line_item_type),
  `high` (<=0.95: usage_type, instance_type, operation),
  `unique` (<=1.00: description).
- A new `identifier` kind for values that are numeric but are *dimensions*, not
  measures — an account id must REPEAT, which is exactly what separates it from
  a cost.

```
A. 36 correct mappings   -> accept 36   FALSE REJECT 0    (0.0%)
B.  4 planted errors     -> caught 4    FALSE ACCEPT 0
```

And each planted error is now caught for the *right* reason:

```
lineItem/UsageType  -> instance_vcpus   0% numeric
Location            -> region           column is empty
identity/LineItemId -> usage_type       ratio 1.00 > 0.95 for a 'high' field
Resource            -> service          ratio 0.94 > 0.50 for a 'categorical' field
```

### The resulting contract model

| kind | checks |
|---|---|
| `number` | % numeric, integer-ness, max value |
| `quantity` | unit suffix (`"192 GiB"` -> GiB) |
| `code` | max length, max cardinality, uppercase |
| `identifier` | must repeat — distinct ratio within the field's class |
| `text` | not numeric, distinct ratio within the field's class, sentinel share |
| all | column not empty |

A fourth signal the tests surfaced for free: `Category` is 4% `"N/A"` and
`Location` is 100% empty. A column that is mostly a null-sentinel is flagged
`warn` even when it maps to a plausible field.

### A caution about this test, and a bug worth recording

While writing round 3 I introduced a loader bug — a dict comprehension over
`list(reader)` exhausts the iterator on the first column, so every column after
the first read as empty. The run reported "4/4 caught", which looked like
success; three of those four were caught for the wrong reason ("column is
empty"). **A green result from a broken harness looks exactly like a green
result.** The fix was to materialise rows once. Any implementation of this
verifier should assert the profiler sees non-zero rows per column before
trusting a verdict.

---

## 7. The constraint that breaks naive implementations

`ingest.py` aggregates before the catalog exists:

```sql
GROUP BY product, usage_type, operation, aws_region, pricing_model,
         line_item_type, is_workload, license_model, operating_system, ...
```

On the customer CUR: **1,245 raw rows → 47 catalog rows.**
`product/instanceType`, `product/vcpu` and `product/memory` are not in that
key. Reading them is not enough — they would be dropped, or take an arbitrary
row's value.

So `aggregation` is mandatory per field:

| role | compiles to |
|---|---|
| `key` | added to `GROUP BY` |
| `sum` | `SUM(...)` |
| `attribute` | `MAX(...)` **plus a conflict assertion** — if two rows in a group disagree, report it rather than silently pick one |
| `derived` | computed after aggregation |

`product_code` already uses the `max()` trick with a comment arguing it is
safe. This makes that argument machine-checked.

---

## 8. Speed

- **Model**: one call per bill, on headers only. Cached by **header
  signature** (sorted column names hashed), so the second bill of the same
  vendor format never calls it. For your corpus that is 5 distinct shapes
  across 15 files — four cache hits immediately.
- **Resolution**: resolvers compile to SQL and run as one statement per field
  over DuckDB, vectorised — never a Python row loop. At 1,245 rows the
  difference is invisible; on a multi-million-row CUR it is the difference
  between seconds and hours.
- **Verification**: profiling is one pass computing count / distinct / numeric
  share per column — a single `SELECT` with aggregate expressions.

---

## 9. Debuggability — the artifact

Per job, `field_resolution_report.json`:

```jsonc
{
  "format": "raw_cur",
  "header_signature": "a3f9…",
  "schema_mapping_source": "cache",        // alias_list | llm | cache
  "columns": [
    { "column": "product/vcpu", "field": "instance_vcpus",
      "proposed_by": "llm", "contract": "pass",
      "profile": { "numeric": 1.0, "distinct": 10, "filled": 0.81 } },
    { "column": "Resource", "field": null,
      "proposed_by": "llm", "contract": "reject",
      "reason": "distinct ratio 0.94 — ID-like, not a service name" },
    { "column": "product/clockSpeed", "field": null,
      "proposed_by": null, "reason": "unconsumed; 980 non-empty values" }
  ],
  "fields": [
    { "field": "instance_vcpus", "resolved_by": "column:product/vcpu",
      "rows_resolved": 1101, "rows_unresolved": 104, "usd_unresolved": 6755 }
  ]
}
```

One file answers: which columns existed, what each became, who proposed it,
whether it passed, which resolver won per field, and what it cost when nothing
did. That is the "supersexy debugging" — and the unconsumed-column list is how
`product/vcpu` would have been found without a customer bill exposing it.

---

## 10. Is the design right? What the test says, and what it does not

**Validated by test (section 6):**
- A proposed mapping *can* be verified from data, in both directions:
  **0% false rejection on 36 correct mappings, 0% false acceptance on 4 planted
  errors**, across 4 real bills.
- The signals needed are cheap — one profiling pass per column.
- Cardinality must be declared **per field**; a global threshold produced a
  13.9% false-rejection rate.
- Structured output constrained to a registry-generated enum makes invented
  field names impossible.

**Not validated, and I will not claim it:**
- **I did not test an actual model call.** This environment has no model
  available — no `agy` on PATH, no Ollama binary, no API key — so the
  schema-mapper itself is unexercised. What is tested is the component that
  makes it safe: the verifier, in both directions, on real bills.
- **The planted errors were written by me.** A real model may fail in ways I
  did not think to simulate. Round 2 is the evidence that this matters — my own
  first verifier was wrong in a direction I had not thought to test until I
  looked for it.
- **Two text fields of the same cardinality class remain confusable.** If a
  bill carries both `Meter` and `Sub-meter`, both categorical with similar
  distributions, nothing here picks correctly. It maps one and reports the
  other as unconsumed — recoverable, not automatic.
- **Two text fields of the same shape remain confusable.** If a bill has both
  `Meter` and `Sub-meter`, both categorical with similar cardinality, nothing
  here picks correctly. It would map one and flag the other as unconsumed —
  recoverable, not automatic.
- **"Handles every possible case" is not achievable.** What is achievable, and
  what this design delivers: *every column is either mapped, rejected with a
  reason, or reported as unconsumed — and every field says which resolver
  answered.* No silent loss. That is the honest form of the requirement.

---

## 11. Migration

- **M1** — Registry describing today's behaviour exactly; lift the 37 `c()`
  lists into it, compile back to the same SQL. Zero behaviour change, proven by
  identical catalog output on the corpus.
- **M2** — Column profiler + contract verifier (§5–6). Run in report-only mode;
  it will immediately list the unconsumed columns above.
- **M3** — `<field>_source` stamping and `field_resolution_report.json` (§9).
- **M4** — Add the missing high-authority resolvers (`product/instanceType`,
  `product/vcpu`, `product/memory`). **First step that moves numbers** — 164
  rows / $182,665 on the customer bill.
- **M5** — Aggregation roles + conflict assertions (§7).
- **M6** — LLM schema-mapper with the header-signature cache (§4, §8).

M1–M3 change no numbers and are worth doing regardless. **M6 is last on
purpose**: once M2's verifier and M3's report exist, the model is landing into
a system that can check and explain it. Shipping the model first would mean
trusting it.

---

## 12. Open choices

1. **Model placement** — M6 as designed (last), or earlier to accelerate
   discovery? Recommend last: §6 shows the verifier is what makes it safe.
2. **Reject band** — is 0.9 the right ID-like threshold? `product/instanceType`
   at 0.87 sits uncomfortably close. Worth re-measuring on more bills.
3. **Scope** — `aws_li_catalog` only, or the mapping table too? Recommend
   catalog only; the mapping table already has `row_state_registry.json`, and
   these should stay siblings rather than merge.
