# DESIGN — Row-state registry: one declaration for every decision the pipeline makes

Status: **proposal, for decision.** Not a work order. Nothing here is implemented.

Supersedes the field-by-field approach in
`WORKORDER_note_prose_to_state_columns.md`, which should be re-cut against this
design if it is accepted.

---

## 1. The question this answers

The pipeline makes decisions about every line item — which strategy, which SKU,
which region's rate, whether a licence premium applies, why a row is
passthrough. Those decisions have real consumers: gates, confidence
calibration, outlier detection, `explain_row`, the report, and cross-job
analysis.

Today they are stored in three incompatible ways:

1. **As columns** — `strategy`, `rate_source`, `mapping_confidence`,
   `passthrough_reason`. Fine, but undeclared (see §2).
2. **As English prose** inside `projection_note`, matched back with `ILIKE` by
   six scripts. This is the one being removed.
3. **Nowhere** — re-derived on demand. `explain_row.py` reconstructs "why is
   this row priced this way" from raw data every time, because the answer was
   never written down.

The question is not "which new columns do we add." It is: **where do decisions
live, who may write them, and how does someone adding the next one know what to
do?**

---

## 2. The real problem: the schema is not declared anywhere

`aws_li_to_gcp_li` has no owner. Evidence from the current tree:

```
ALTER TABLE aws_li_to_gcp_li ADD COLUMN passthrough_reason  -- apply_rates.py:424
ALTER TABLE aws_li_to_gcp_li ADD COLUMN passthrough_reason  -- ensure_full_coverage.py:96
ALTER TABLE aws_li_to_gcp_li ADD COLUMN passthrough_reason  -- merge_mappings.py:216
ALTER TABLE aws_li_to_gcp_li ADD COLUMN passthrough_reason  -- projection_view.py:228
```

The same column is defensively created in four files. `decided_by` likewise.
`rate_source` is **not in the `CREATE TABLE` at all** — it exists only because
`apply_rates.py:439` adds it, so a DB where that script has not run has a
different schema from one where it has.

Consequences:

- **Adding a field costs 4–6 edits** in unrelated files, and missing one fails
  silently (a `SELECT` of a missing column throws at runtime, inside a
  `try/except` that logs and continues).
- **No value domain.** Nothing stops `rate_source = 'no-rate'` coexisting with
  `'no_rate'`. Both already appear in prose form across jobs.
- **No write permissions.** The previous work order had to hand-police which
  fields the LLM may set by curating `merge_mappings.COLUMNS` — correct, but
  the rule lives in a Python list, not next to the field.
- **No discoverability.** There is no file you can read to learn what the
  pipeline records about a row. You have to grep.

The scattering is the cause. The prose matching is downstream of it.

---

## 3. Principles

**P1 — One declaration.** Every field on the mapping row is declared in exactly
one file, with its type, domain, writer tier, and human rendering.

**P2 — Schema is generated, never hand-written.** No `CREATE TABLE` column
list, no `ADD COLUMN` statements in business-logic scripts. One function reads
the registry and makes the table match it.

**P3 — Three layers, each derived from the one before.**
`decision → state → prose`. Each has exactly one writer. Nothing reads
backwards up the chain.

**P4 — Write permission is a property of the field,** declared beside it, not
enforced by remembering to curate a list elsewhere.

**P5 — Adding a field is one edit.** That is the scalability test this design
is measured against.

---

## 4. The design

### 4.1 Layer map

```
  mappers / apply_rates / validators
            │ write
            ▼
  ┌───────────────────────────────┐
  │  STATE  (aws_li_to_gcp_li)    │   declared in data/row_state_registry.json
  │  closed-domain columns        │   written only via scripts/row_state.py
  └───────────────────────────────┘
            │ read                        │ read
            ▼                             ▼
  gates · calibration · outliers    scripts/render_notes.py
  explain_row · cross-job queries           │ writes
                                            ▼
                                   projection_note  (prose, output only)
```

The optional fourth layer (a decision log) is discussed in §8; it sits *above*
state, not beside it.

### 4.2 The registry: `data/row_state_registry.json`

One entry per field. This file is the contract.

```jsonc
{
  "_meta": { "version": 1, "table": "aws_li_to_gcp_li" },

  "fields": {
    "strategy": {
      "sql_type": "TEXT",
      "kind": "enum",
      "domain": {
        "map":            { "note": null },
        "break_down":     { "note": null },
        "passthrough":    { "note": null },
        "ignore":         { "note": null },
        "needs_reprice":  { "note": "Carried at AWS cost pending re-pricing." },
        "outlier_triage": { "note": null }
      },
      "writable_by": ["mapper", "pipeline"],
      "required": true,
      "description": "How this row's GCP cost is produced."
    },

    "rate_source": {
      "sql_type": "TEXT",
      "kind": "enum",
      "domain": {
        "exact_sku":    { "note": null },
        "word_overlap": { "note": "SKU matched by word overlap — verify before quoting." },
        "no_rate":      { "note": "No GCP rate reachable for this SKU; carried at AWS cost." },
        "no_catalog":   { "note": "GCP catalog unavailable at run time; carried at AWS cost." },
        "passthrough":  { "note": null },
        "ignored":      { "note": null },
        "unknown":      { "note": "Rate provenance not recorded." }
      },
      "writable_by": ["pipeline"],
      "groups": { "unresolved": ["no_rate", "no_catalog", "word_overlap", "unknown"] },
      "description": "How the GCP rate for this row was obtained."
    },

    "rate_region_basis": {
      "sql_type": "TEXT",
      "kind": "enum",
      "domain": {
        "exact":              { "note": null },
        "global":             { "note": "Global charge; priced at the {rate_region_used} rate." },
        "dominant":           { "note": "No rate in {gcp_region}; priced at the {rate_region_used} rate." },
        "cheapest_available": { "note": "No rate in {gcp_region}; priced at the cheapest available region ({rate_region_used})." }
      },
      "writable_by": ["pipeline"],
      "groups": { "fallback": ["dominant", "cheapest_available"] },
      "description": "Which region's rate was actually applied."
    },

    "review_flags": {
      "sql_type": "TEXT",
      "kind": "flagset",
      "domain": {
        "service_model_change": { "note": "Service model differs between clouds — review the architecture, not just the price." },
        "architecture_review":  { "note": "Architecture review recommended." },
        "verify_with_customer": { "note": "Verify this assumption with the customer." }
      },
      "writable_by": ["mapper", "pipeline"],
      "description": "Caveats that do not change the number but change how it should be read."
    },

    "mapper_note": {
      "sql_type": "TEXT",
      "kind": "free_text",
      "writable_by": ["mapper", "llm"],
      "readable_as_logic": false,
      "description": "The mapper's or LLM's own explanation. Rendered into the note; never matched against."
    },

    "projection_note": {
      "sql_type": "TEXT",
      "kind": "derived",
      "writable_by": ["render_notes"],
      "readable_as_logic": false,
      "description": "Generated from state by scripts/render_notes.py. Output only."
    }
  }
}
```

`kind` drives everything mechanical:

| `kind` | Storage | Matching |
|---|---|---|
| `enum` | `TEXT`, one value from `domain` | `= 'v'` / `IN (...)` |
| `flagset` | `TEXT`, comma-wrapped `,a,b,` | delimiter-safe `LIKE '%,a,%'` |
| `number` / `bool` | native | native |
| `free_text` | `TEXT` | **never** matched — enforced by test |
| `derived` | `TEXT` | **never** matched; single writer |

`writable_by` tiers:

- `llm` — the model may supply it in a mapping JSON file.
- `mapper` — a deterministic mapper may supply it.
- `pipeline` — only post-merge scripts (`apply_rates`, validators) set it, from
  catalog facts. An LLM-supplied value is dropped with a warning.
- `render_notes` — exactly one script.

### 4.3 The accessor: `scripts/row_state.py`

The only module that knows the registry. Everything else imports from it.

```python
# --- schema ---
ensure_schema(con)
    # Reads the registry, diffs against PRAGMA table_info, issues the needed
    # ADD COLUMNs. Idempotent. THE ONLY PLACE ANY DDL FOR THIS TABLE LIVES.
    # Replaces the 14 scattered ALTER statements, including the 4 duplicates.

# --- predicates (for SQL consumers) ---
eq("rate_source", "no_rate")                -> "rate_source = 'no_rate'"
in_group("rate_source", "unresolved")       -> "rate_source IN ('no_rate','no_catalog',...)"
has_flag("review_flags", "architecture_review")
                                            -> "COALESCE(review_flags,',') LIKE '%,architecture_review,%'"
add_flag_expr("review_flags", "architecture_review")   # idempotent SET expression

# --- writes (for Python consumers) ---
set_state(con, keys, by="apply_rates.py", rate_region_basis="dominant",
          rate_region_used="us-central1")
    # Validates every value against its domain and the caller's tier.
    # Stamps decided_by. Raises on an undeclared field or value — loudly, in
    # tests and dev; in production the caller decides, but the default is raise
    # because an undeclared value is a code bug, not a data condition.

# --- introspection ---
fields(kind="enum") / domain_of("strategy") / note_for("rate_source", "no_rate")
```

Why an accessor rather than "just use the column name": it puts domain
validation and the writer tier **at the call site**, which is the only place
they can actually be enforced. A script physically cannot write
`rate_source = 'no-rate'`.

### 4.4 Consumers

- **Gates / calibration / outliers** call `in_group(...)` and `eq(...)` to build
  predicates — no literal enum strings in business logic, so renaming a value
  is one registry edit.
- **`render_notes.py`** walks the registry, collects the `note` template for
  each set value, fills placeholders, joins. Pure function of state.
- **`explain_row.py`** becomes a lookup: print the row's state fields with their
  registry `description`s instead of re-deriving.
- **The report** reads `projection_note` for display only.
- **Cross-job analysis** becomes possible: `GROUP BY rate_region_basis` across
  all 75 job DBs answers "how much spend is priced at a fallback region" — a
  question that is currently unanswerable.

---

## 5. Adding a field — the scalability test

**Today:** edit `CREATE TABLE`; add `ADD COLUMN` guards in up to 4 files; add to
`merge_mappings.COLUMNS` (or deliberately not); add to the view's `SELECT`; write
the note text at each site; write the `ILIKE` at each reader. **8–12 edits
across 6 files, and missing one fails silently.**

**With the registry:** add one entry to `data/row_state_registry.json`. Schema,
write permissions, note rendering and predicate helpers all follow. **One edit.**

That property is the whole point of the design. If a proposed change to this
design breaks it, the change is wrong.

---

## 6. Enforcement — what keeps it from decaying

Four tests. These are the deliverable as much as the code is.

1. **`test_schema_matches_registry`** — the live table's columns equal the
   registry's fields. Fails if anyone hand-adds a column.
2. **`test_no_ddl_outside_row_state`** — no `ALTER TABLE aws_li_to_gcp_li` or
   `CREATE TABLE aws_li_to_gcp_li` outside `row_state.py`. This is what prevents
   the four-duplicate situation returning.
3. **`test_no_logic_reads_free_text`** — no script uses `projection_note`,
   `mapper_note` or `assumption_note` in a predicate (`LIKE`, `ILIKE`, `IN`,
   `==`, `in note`). Allowed: `render_notes.py`, the migration shim, and display
   paths in `render_report.py`.
4. **`test_values_are_declared`** — every literal assigned to an enum field
   anywhere in `scripts/` exists in that field's domain. Catches typo'd values
   at test time rather than in a customer's report.

---

## 7. Migration path

Phased so each step is revertible and the pipeline keeps working throughout.

- **M1 — Registry + accessor, describing today's schema exactly.** Add
  `ensure_schema()`, call it from `merge_mappings`, delete the 14 scattered
  `ALTER`s. No behaviour change. Add tests 1 and 2.
- **M2 — Adopt the accessor for writes.** Scripts call `set_state()` instead of
  hand-written `UPDATE ... SET`. Still no new fields. Catches existing typo'd
  values.
- **M3 — Add the new fields** (`rate_region_basis`, `rate_region_used`,
  `license_status`, `review_flags`, `mapper_note`), one registry entry each.
  Writers populate them beside the existing note appends.
- **M4 — `render_notes.py`;** `projection_note` becomes derived. Delete every
  note append. Add test 3.
- **M5 — Readers move to predicates;** delete all 13 `ILIKE`s. One-shot
  backfill shim for resumable legacy DBs. Add test 4.

M1 and M2 are pure cleanup and worth doing even if the rest is deferred — M1
alone removes the four-duplicate bug and makes the schema knowable.

---

## 8. Should there also be a decision log?

A `row_decisions` append-only table — `(aws_li_key, component, phase, script,
field, old_value, new_value, reason, ts)` — would record not just *what* a row
is but *how it got there*: "mapper said `map`; `apply_rates` downgraded it at
phase 4; the validator re-labelled it at phase 5."

**Recommendation: design for it, do not build it yet.**

- It is strictly more information, and it is the right long-term model.
- But it does not remove the prose coupling any faster, and it puts a rollup
  step on the hot path that every gate and the note renderer would depend on.
- You already have two thirds of its value: `decided_by` records the last
  writer, and `diagnostics.jsonl` records run-level events with dollar amounts.
  The gap is per-row history, which is `explain_row`'s job — an on-demand tool,
  not a hot path.

If it is added later, **state columns become a materialized projection of the
log**, not a competing source of truth: `log → state → prose`, one writer each,
same shape as the rest of the design. To keep that door open, `set_state()`
should be the only writer from day one — then adding the log means changing one
function, not every caller.

---

## 9. What this does not solve

Worth being explicit, so the design is not oversold:

- It does not improve a single price. It makes decisions legible and uniform.
- It does not fix classification being a one-way door (`mechanic_group` is on
  `aws_li_catalog`, written once by `classify_mechanics`). That is a separate,
  larger piece of work.
- It does not reduce the number of decisions the pipeline makes — only the
  number of places you have to look to find them.
- `aws_li_catalog` (the extracted-facts table) is out of scope. The same
  treatment probably belongs there eventually; it has its own scattering
  problem, and mixing the two would make both migrations harder.

---

## 10. Open choices — your call

1. **Scope of M1.** Registry describing only `aws_li_to_gcp_li`, or
   `aws_li_catalog` too? (Recommendation: mapping table only. Catalog is a
   bigger, separate migration.)
2. **Hard or soft validation.** Should `set_state()` raise on an undeclared
   value, or log and coerce? (Recommendation: raise in tests and CI, log and
   coerce in a customer run — consistent with the fail-open principle from the
   diagnostics work order.)
3. **Decision log now or later.** §8 recommends later.
4. **Do M1+M2 standalone.** They are pure cleanup with no behaviour change and
   would make every later work order smaller. Worth doing first on their own?
