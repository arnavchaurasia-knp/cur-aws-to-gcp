# WORK ORDER — Row state as declared fields; notes become generated output only

**For an implementing AI / engineer with no prior context. Read this file fully
before editing anything.** All paths are relative to
`skill/aws-gcp-cost-projection/` unless they start with `internal/`.

**Prerequisite:** `WORKORDER_passthrough_reason_provenance.md` is already
applied — `passthrough_reason` and `decided_by` exist on `aws_li_to_gcp_li`.
This work order generalises that same idea to every remaining case.

This changes the **data model and the note-generation path**. It does NOT change
any price, SKU choice, classification or mapping. **If a dollar figure moves,
you have made a mistake.** Note *text* will change — that is intended (§6).

---

## 0. The governing rule

> **A note is output. It is never input.**
>
> Every fact the pipeline acts on lives in a declared, structured field with a
> closed value domain — exactly like `strategy`, `rate_source` and
> `passthrough_reason` already do. The human-readable sentence is **generated
> from those fields** at the end of the run, by one script. Nothing anywhere
> reads a note back to decide anything.

Two consequences that drive the whole design:

1. **Notes are derived, not accumulated.** Today seven scripts *append* to
   `projection_note`, so every writer needs a "have I already written this?"
   guard. If the note is regenerated from state each time, those guards stop
   existing. There is no `note_markers` column in this design and no
   idempotency problem to solve.
2. **Free text from a mapper or the LLM is preserved, but quarantined.** It
   moves to its own column, is rendered into the final note, and is never
   matched against.

---

## 1. The problem

`projection_note` is a free-text English sentence written for a human. It is
also the pipeline's control plane: **six scripts branch on `ILIKE`/`LIKE`
substring matches against that prose.** Full inventory of what is still
prose-driven:

| File:line | Pattern | What it is really asking |
|---|---|---|
| `apply_rates.py:347` | `NOT LIKE '%license-premium-not-modeled%'` | already flagged this row's licence premium? |
| `apply_rates.py:605` | `LIKE '%no-catalog%'` | how many rows lost their rate to a missing catalog? |
| `apply_rates.py:1059` | `LIKE '%no-rate-fallback%'` | which rows have no reachable rate? |
| `apply_rates.py:1106` | `NOT LIKE '%global charge; priced at <r> rate%'` | already appended the global note? |
| `apply_rates.py:1133` | `NOT LIKE '%priced at <r> rate%'` | already appended the dominant-region note? |
| `apply_rates.py:1145` | `LIKE '%cheapest available region%'` | which rows fell back to a cheaper region? |
| `calibrate_confidence.py:92` | `NOT ILIKE '%<marker>%'` | generic "already noted?" guard |
| `calibrate_confidence.py:139,140,159,160` | `'%Windows%'`, `'%license-premium%'` | is this a licence-bearing row? |
| `calibrate_confidence.py:149` | `'%license%'`, `'%BYOL%'` | same |
| `detect_outliers.py:209,293` | `NOT LIKE '%no-rate-fallback%'` | exclude unpriced rows from outlier detection |
| `projection_view.py:156` | `LIKE '%needs_reprice%'` | already appended the needs_reprice note? |
| `projection_view.py:162` | `LIKE '%' \|\| od.dominant_note \|\| '%'` | already appended this region's note? |
| `service_classifier.py:253-272` | 8 patterns | does this row already record an unresolved state, so I must not overwrite it? |

Why this is a defect, not just ugly:

**(a) Notes accumulate, so matches collide.** A row that picks up the word
"Windows" from an unrelated appended note is treated as licence-bearing by
`calibrate_confidence.py:139` and has its confidence capped. A row whose note
accumulates "no rate" anywhere is treated by `service_classifier.py:253` as
unpriced. Neither row actually changed.

**(b) Matches are substrings, not tokens.** `%license%` also matches
"licensed", "unlicensed", "re-licensing review". `%unavailable in%` matches both
"unavailable in us-east1" and "feature unavailable in this tier".

**(c) Rewording a human sentence changes behaviour.** Change a mapper's note
from "no rate found" to "rate not found" and `service_classifier` silently stops
protecting that row — it overwrites the note with generic text implying the row
was priced normally. Nothing errors.

**(d) There is nothing to test.** No enumerable state means no assertable
invariant.

---

## 2. The design

### 2.1 One declared registry: `data/row_state.json`

Every structured state field, its closed value domain, and the human sentence
each value renders to. This file is the single source of truth for both the
logic and the prose — they can no longer drift, because the prose is generated
from the same entry the logic reads.

### 2.2 Fields

Already present on `aws_li_to_gcp_li`, unchanged by this work order:
`strategy`, `passthrough_reason`, `decided_by`, `rate_source`,
`mapping_confidence`, `is_assumption`, `assumption_note`.

**Extended:**

- `rate_source` — add one value, `no_catalog` (the catalog DB was unavailable,
  so no rate could be attempted; distinct from `no_rate`, where the catalog was
  present but had no reachable rate).

**New:**

| Column | Domain | Replaces |
|---|---|---|
| `rate_region_basis` | `exact` \| `global` \| `dominant` \| `cheapest_available` \| NULL | the three region-note matchers and their guards |
| `rate_region_used` | a region name, e.g. `us-central1` | `projection_view.py:162`'s runtime-computed string match |
| `license_status` | `none` \| `premium_not_modeled` \| `modeled` \| NULL | `%Windows%`, `%license%`, `%BYOL%`, `%license-premium%` |
| `review_flags` | comma-delimited token set, e.g. `,service_model_change,verify_with_customer,` | the mapper-specific states in `service_classifier`'s 8 patterns |
| `mapper_note` | free text | the mapper's / LLM's own explanatory sentence, quarantined |

`review_flags` token vocabulary (declared in the registry):
`service_model_change`, `log_ingestion_model`, `architecture_review`,
`verify_with_customer`, `region_unavailable`, `cost_may_differ`.

Tokens are stored comma-wrapped (`,a,b,`) and always matched with delimiters, so
no token can be matched as another's prefix.

### 2.3 `projection_note` becomes derived-only

One new script, `scripts/render_notes.py`, is the **only** writer of
`projection_note`. It `SET`s (never appends), composing the sentence from the
row's state fields plus `mapper_note`. Because it is a pure function of state,
running it twice produces the same text — idempotency is structural, not
guarded.

Every other script stops writing `projection_note` and writes state instead.

### 2.4 `service_classifier`'s 8 patterns become a derived condition

They all ask one question — *"does this row already record an unresolved or
caveated state?"* With the fields above that is:

```sql
(
      COALESCE(rate_source,'') IN ('no_rate','word_overlap','no_catalog','unknown')
   OR COALESCE(rate_region_basis,'') IN ('dominant','cheapest_available')
   OR passthrough_reason IS NOT NULL
   OR COALESCE(license_status,'') = 'premium_not_modeled'
   OR COALESCE(review_flags,',') <> ','
)
```

No new column needed.

### 2.5 Legacy DBs: one shim, not permanent fallbacks

An earlier draft of this work order kept every prose test as an `OR` fallback
"for older job DBs". **That is rejected** — it would make the prose coupling
permanent, which is the thing being removed.

Instead: one isolated, deletable script, `scripts/backfill_row_state.py`, parses
legacy notes into the new columns. It runs **once** at the start of
`apply_rates.py` and no-ops when the new columns already carry values. Its
docstring must say it is a migration shim and may be deleted once no job DB
predating this change can be resumed.

---

## 3. Files to read before you start

- `scripts/apply_rates.py` — region `UPDATE`s (~L1100-1150), licence block
  (~L340-360), no-catalog path (~L600-610)
- `scripts/calibrate_confidence.py` — short, read it all
- `scripts/service_classifier.py` — the `keep_updates` block (~L240-280)
- `scripts/detect_outliers.py` — ~L209 and ~L293
- `scripts/projection_view.py` — the `projection_note` `CASE` in the view
- `scripts/merge_mappings.py` — schema, `COLUMNS`, `ADD COLUMN` guards
- `scripts/apply_static_mappings.py` — `_emit()` (~L632); it is the central
  row-builder for all ~34 mappers
- `scripts/passthrough_reasons.py` — the pattern this work order copies
- `scripts/render_report.py:174` — the existing note parser (read only; do not
  modify)

---

## 4. Implementation

Five phases. **Run the full suite after each.** Phases A–C are self-contained
and can land before D–E.

---

## PHASE A — Registry and schema

### STEP A1 — `data/row_state.json`

New file. `note_template` strings use `{placeholders}` filled from the row.

```json
{
  "_meta": {
    "description": "Declared row-state fields. Logic reads these; scripts/render_notes.py generates projection_note FROM them. Nothing reads projection_note back. See docs/WORKORDER_note_prose_to_state_columns.md.",
    "version": 1
  },
  "rate_source": {
    "exact_sku":    {"note_template": null},
    "word_overlap": {"note_template": "SKU matched by word overlap — verify before quoting."},
    "no_rate":      {"note_template": "No GCP rate reachable for this SKU; carried at AWS cost."},
    "no_catalog":   {"note_template": "GCP catalog unavailable at run time; carried at AWS cost."},
    "passthrough":  {"note_template": null},
    "ignored":      {"note_template": null},
    "unknown":      {"note_template": "Rate provenance not recorded."}
  },
  "rate_region_basis": {
    "exact":              {"note_template": null},
    "global":             {"note_template": "Global charge; priced at the {rate_region_used} rate."},
    "dominant":           {"note_template": "No rate in {gcp_region}; priced at the {rate_region_used} rate."},
    "cheapest_available": {"note_template": "No rate in {gcp_region}; priced at the cheapest available region ({rate_region_used})."}
  },
  "license_status": {
    "none":                {"note_template": null},
    "premium_not_modeled": {"note_template": "AWS price includes an OS/engine licence premium that is not modeled on the GCP side."},
    "modeled":             {"note_template": "Licence component priced explicitly."}
  },
  "review_flags": {
    "service_model_change":  {"note_template": "Service model differs between clouds — review the architecture, not just the price."},
    "log_ingestion_model":   {"note_template": "Log ingestion is priced on a different basis on GCP."},
    "architecture_review":   {"note_template": "Architecture review recommended."},
    "verify_with_customer":  {"note_template": "Verify this assumption with the customer."},
    "region_unavailable":    {"note_template": "Requested region unavailable for this resource."},
    "cost_may_differ":       {"note_template": "Real cost may differ materially from this estimate."}
  },
  "note_order": [
    "passthrough_reason",
    "rate_source",
    "rate_region_basis",
    "license_status",
    "review_flags",
    "mapper_note"
  ]
}
```

### STEP A2 — `scripts/row_state.py`

New file. Copy the structure of `scripts/passthrough_reasons.py`.

```python
#!/usr/bin/env python3
"""Declared row-state fields for the projection.

Logic reads these fields. scripts/render_notes.py generates projection_note
from them. Nothing reads projection_note back to decide anything.
See docs/WORKORDER_note_prose_to_state_columns.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_CFG = _cfg("row_state") or {}

RATE_SOURCE       = _CFG.get("rate_source", {})
RATE_REGION_BASIS = _CFG.get("rate_region_basis", {})
LICENSE_STATUS    = _CFG.get("license_status", {})
REVIEW_FLAGS      = _CFG.get("review_flags", {})
NOTE_ORDER        = _CFG.get("note_order", [])

# rate_source values meaning "this row was not cleanly priced".
UNRESOLVED_RATE_SOURCES = ("no_rate", "word_overlap", "no_catalog", "unknown")
# rate_region_basis values meaning "priced somewhere other than this row's region".
FALLBACK_REGION_BASES = ("dominant", "cheapest_available")


def sql_in_list(values) -> str:
    """SQL literal list, e.g. "'a', 'b'". Values come from our own JSON file."""
    safe = [v for v in sorted(values) if str(v).replace("_", "").isalnum()]
    return ", ".join("'" + v + "'" for v in safe) or "''"


def flag_present_clause(column: str, flag: str) -> str:
    """SQL fragment: TRUE when `flag` is in the comma-wrapped set."""
    return "COALESCE({c}, ',') LIKE '%,{f},%'".format(c=column, f=flag)


def flag_add_expr(column: str, flag: str) -> str:
    """SQL expression returning `column` with `flag` added. Idempotent."""
    return (
        "CASE WHEN COALESCE({c}, ',') LIKE '%,{f},%' THEN {c} "
        "ELSE COALESCE(NULLIF({c}, ''), ',') || '{f},' END"
    ).format(c=column, f=flag)


def flags_of(value):
    """Python-side: parse a comma-wrapped flag set into a list, in registry order."""
    present = set((value or "").strip(",").split(",")) - {""}
    return [f for f in REVIEW_FLAGS if f in present]
```

Verify:

```bash
cd skill/aws-gcp-cost-projection
python -c "import sys; sys.path.insert(0,'scripts'); import row_state as r; print(sorted(r.RATE_REGION_BASIS)); print(r.flag_present_clause('review_flags','architecture_review'))"
```

### STEP A3 — Schema

In `scripts/merge_mappings.py`, after `decided_by TEXT` in the `CREATE TABLE`:

```
            rate_region_basis   TEXT,
            rate_region_used    TEXT,
            license_status      TEXT,
            review_flags        TEXT,
            mapper_note         TEXT
```

Add five `ADD COLUMN` guards in the existing style so older DBs migrate.

**`COLUMNS` changes — read carefully:**

- **Add** `mapper_note` to `COLUMNS`. Mappers and the LLM legitimately supply
  explanatory text.
- **Add** `review_flags` to `COLUMNS`. A mapper knows when it is making a
  service-model substitution.
- **Do NOT add** `rate_region_basis`, `rate_region_used` or `license_status`.
  Those are determined by `apply_rates.py` from catalog facts. A mapper or the
  LLM must not be able to assert them.
- **Remove** `projection_note` from `COLUMNS`, and in the record-normalising
  block in `main()` (the one that already normalises `passthrough_reason`),
  move any incoming `projection_note` into `mapper_note`:

```python
        # projection_note is GENERATED by render_notes.py from state fields.
        # A mapper's or the LLM's own explanatory text is preserved here, shown
        # in the rendered note, and never matched against. See WORKORDER §0.
        if not row_dict.get("mapper_note") and row_dict.get("projection_note"):
            row_dict["mapper_note"] = row_dict["projection_note"]
        row_dict.pop("projection_note", None)
```

This single change retargets all ~135 note-writing sites in
`apply_static_mappings.py` at once — they build dicts that flow through
`merge_mappings`. **Do not edit the 34 mappers individually.**

Validate incoming `review_flags` against the registry the same way
`passthrough_reason` is validated: unknown tokens are dropped with a warning,
never stored.

### STEP A4 — Expose on the view

In `scripts/projection_view.py`'s final `SELECT`, extend the line already
carrying `m.passthrough_reason, m.decided_by`:

```sql
        m.unit_multiplier, m.passthrough_reason, m.decided_by,
        m.rate_region_basis, m.rate_region_used, m.license_status, m.review_flags,
```

Do not touch any `CASE` in this step.

### Phase A verification

```bash
python -m pytest tests/ -q      # 955 must still pass
```

---

## PHASE B — Writers set state instead of appending prose

In every step below: **add the state write, and delete the note append in the
same edit.** Do not leave both — appended prose is exactly what Phase C
replaces.

### STEP B1 — `apply_rates.py`, licence premium (~L340-360)

- `SET license_status = 'premium_not_modeled'` (drop the note append).
- Change the guard `NOT LIKE '%license-premium-not-modeled%'` to
  `COALESCE(m.license_status,'') != 'premium_not_modeled'`.

### STEP B2 — `apply_rates.py`, no-catalog path (~L600-610)

- Set `rate_source = 'no_catalog'` instead of `'no_rate'`.
- Change the counting query's `projection_note LIKE '%no-catalog%'` to
  `rate_source = 'no_catalog'`.
- **Then audit every existing `rate_source` reader:**

```bash
grep -rn "rate_source" scripts/*.py | grep -i "no_rate"
```

  Each reader testing `rate_source = 'no_rate'` must be widened to
  `IN ('no_rate','no_catalog')` unless it is specifically about a
  present-but-incomplete catalog. **List every reader you widened and every one
  you did not, with one line of reasoning each, in your summary.**

### STEP B3 — `apply_rates.py`, the three region `UPDATE`s (~L1100-1150)

For each, replace the note append with:

- global block → `rate_region_basis = 'global'`, `rate_region_used = <region>`
- dominant block → `rate_region_basis = 'dominant'`, `rate_region_used = <region>`
- cheapest block → `rate_region_basis = 'cheapest_available'`, `rate_region_used = <region>`

and replace each `NOT LIKE` guard with `rate_region_basis IS NULL`.

Then change the confidence cap at ~L1145 to
`WHERE rate_region_basis = 'cheapest_available'` (drop the `gcp_projection`
subquery on prose).

**Highest-risk step in this work order.** Those `UPDATE`s have long `EXISTS`
chains. Change only the `SET` list and the tail of the `WHERE` — do not
restructure, reindent or tidy the `EXISTS` clauses.

### STEP B4 — `calibrate_confidence.py`

Delete the `not_already_noted` prose-guard machinery (~L92) entirely. The
confidence `UPDATE`s become unguarded `SET`s of confidence only; they no longer
write notes, so there is nothing to double-write.

### STEP B5 — the remaining note writers

`service_classifier.py`, `fix_storage_misroute.py`, `outlier_gate.py`,
`apply_review_fixes.py`, `validate_fix.py`, `reconcile_capacity.py`,
`apply_commitment_ignores.py`, `ingest.py`: each currently appends to
`projection_note`. For each, pick the state field that already expresses what
the sentence said:

- a passthrough/ignore decision → `passthrough_reason` (already exists)
- an unresolved rate → `rate_source`
- a caveat with no field → a `review_flags` token
- genuinely free-form explanation → `mapper_note`

Then delete the append. **If a sentence maps to none of these, add a
`review_flags` token to the registry rather than keeping the append.** Report
any you could not place.

`projection_view.py`'s two note `CASE` branches are handled in Phase C.

### Phase B verification

```bash
python -m pytest tests/ -q
grep -rn "projection_note *=" scripts/*.py | grep -v render_notes.py | grep -v merge_mappings.py
```

The grep should return only `projection_view.py` (Phase C) and read-only
display usages in `render_report.py`.

---

## PHASE C — Generate the note

### STEP C1 — `scripts/render_notes.py`

New file. The only writer of `projection_note`.

```python
#!/usr/bin/env python3
"""render_notes.py <projection.duckdb>

Regenerates projection_note for every row from the row's structured state
fields. This is the ONLY writer of projection_note in the pipeline.

The note is OUTPUT: it is composed from state, never parsed back. Running this
twice produces identical text, so there are no idempotency guards anywhere.

Always exits 0 — a note-rendering problem must never fail a customer-facing run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
import row_state as rs

try:
    from passthrough_reasons import REASONS as PT_REASONS
except Exception:
    PT_REASONS = {}


def _fill(template, row):
    try:
        return template.format(**row)
    except Exception:
        return template


def compose(row):
    """Return the human-readable note for one row. Pure function of state."""
    parts = []
    for field in rs.NOTE_ORDER:
        val = row.get(field)
        if not val:
            continue
        if field == "passthrough_reason":
            desc = (PT_REASONS.get(val) or {}).get("description")
            if desc:
                parts.append(desc)
        elif field == "review_flags":
            for flag in rs.flags_of(val):
                t = (rs.REVIEW_FLAGS.get(flag) or {}).get("note_template")
                if t:
                    parts.append(_fill(t, row))
        elif field == "mapper_note":
            parts.append(str(val))
        else:
            domain = getattr(rs, field.upper(), {}) or {}
            t = (domain.get(val) or {}).get("note_template")
            if t:
                parts.append(_fill(t, row))
    # de-duplicate while preserving order
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "; ".join(out)


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    con = duckdb.connect(sys.argv[1])
    try:
        cols = ["aws_li_key", "component", "strategy", "passthrough_reason",
                "rate_source", "rate_region_basis", "rate_region_used",
                "license_status", "review_flags", "mapper_note"]
        rows = con.execute(
            "SELECT m.rowid, " + ", ".join("m." + c for c in cols) +
            ", c.gcp_region FROM aws_li_to_gcp_li m "
            "LEFT JOIN aws_li_catalog c USING(aws_li_key)"
        ).fetchall()
        updates = []
        for raw in rows:
            row_id = raw[0]
            row = dict(zip(cols + ["gcp_region"], raw[1:]))
            updates.append((compose(row), row_id))
        if updates:
            con.executemany(
                "UPDATE aws_li_to_gcp_li SET projection_note = ? WHERE rowid = ?",
                updates,
            )
            con.commit()
        print("render_notes: regenerated %d note(s)" % len(updates))
    finally:
        con.close()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("render_notes: failed (%s) — continuing" % e)
        sys.exit(0)
```

### STEP C2 — Wire it

In `internal/jobs/orchestrate.go`, add `"?scripts/render_notes.py $DB"` as the
**last** `PostLLMScripts` entry of Phase 4 and of Phase 5, and as the **first**
`PreLLMScripts` entry of Phase 6. Because it is a pure function of state,
running it three times is harmless and guarantees the note always matches the
final state. Change nothing else in those phase specs.

Remember: inside `orchestratorTemplate`, every literal `%` must be `%%`. These
entries are in the Go `phaseSpecs()` function, not the template, so no escaping
is needed there — but do not disturb the template.

### STEP C3 — `projection_view.py`

Delete both note `CASE` branches (the `needs_reprice` dedupe at ~L156 and the
`dominant_note` dedupe at ~L162) and select `m.projection_note` plainly. The
state they were patching in is now rendered into the column by `render_notes.py`
before the view is read.

Keep the `od.dominant_note` computation if other parts of the view use it;
remove it if nothing else does.

### STEP C4 — Legacy shim: `scripts/backfill_row_state.py`

New file. Parses legacy notes into the new columns for job DBs written before
this change, so no prose fallback is needed in live logic.

- No-op if `rate_region_basis`/`license_status` already have any non-NULL value.
- Map: `%no-rate-fallback%` → `rate_source='no_rate'`;
  `%no-catalog%` → `rate_source='no_catalog'`;
  `%cheapest available region%` → `rate_region_basis='cheapest_available'`;
  `%global charge; priced at%` → `'global'`;
  `%priced at % rate — no %` → `'dominant'`;
  `%license-premium%` → `license_status='premium_not_modeled'`;
  `%service model change%` → flag `service_model_change`;
  `%log ingestion%` → flag `log_ingestion_model`;
  `%architecture review recommended%` → flag `architecture_review`;
  `%verify with customer%` → flag `verify_with_customer`;
  `%unavailable in%` → flag `region_unavailable`;
  `%real cost may differ%` → flag `cost_may_differ`.
- Call it once at the top of `apply_rates.py`'s main flow.
- Docstring must state: *migration shim; delete once no job DB predating this
  change can be resumed.* **This is the only place in the codebase permitted to
  read a note.**

---

## PHASE D — Readers branch on state, prose tests deleted

No fallbacks. The shim in C4 covers legacy DBs.

- **`apply_rates.py:1059`** → `WHERE rate_source IN ('no_rate','no_catalog')`
- **`detect_outliers.py:209,293`** →
  `AND COALESCE(m.rate_source,'') NOT IN ('no_rate','no_catalog')`
  *(Note: the original compares a possibly-NULL note with `NOT LIKE`, which
  yields NULL and silently drops those rows. The `COALESCE` form includes them.
  This is a behaviour fix — report how many extra rows it adds on a real job DB,
  and stop and flag if it is more than a handful.)*
- **`calibrate_confidence.py:139,140,149,159,160`** →
  `m.license_status = 'premium_not_modeled'`. **The bare `%Windows%` matcher is
  deleted, not replaced** — it is the worst matcher in the file. If the query
  has no `aws_li_to_gcp_li` alias in scope, join it; do **not** copy
  `license_status` onto `aws_li_catalog`.
- **`service_classifier.py:253-272`** → the derived condition from §2.4.
  *(This widens slightly: `passthrough_reason IS NOT NULL` now preserves notes
  on passthrough rows that previously matched no prose. That is the intended
  correction.)*

---

## PHASE E — Make the rule enforceable

New `tests/test_notes_are_output_only.py`:

1. `test_no_script_reads_projection_note` — scan every `scripts/*.py`; fail if
   any file other than `render_notes.py`, `backfill_row_state.py` and
   `render_report.py` contains `projection_note` in a predicate context
   (`LIKE`, `ILIKE`, `IN (`, `==`, `.lower()`, `in note`). Allow plain
   `SELECT`/display usage. **This test is the real deliverable of the work
   order** — it is what stops the pattern coming back.
2. `test_render_notes_is_pure` — run `compose()` twice on the same row dict;
   assert identical output and that no state field is mutated.
3. `test_render_notes_idempotent_in_db` — run `render_notes.py` twice over a
   fixture DB; assert every `projection_note` is unchanged by the second run.
4. `test_registry_covers_every_value` — every value written by any script for
   `rate_source` / `rate_region_basis` / `license_status` / `review_flags`
   exists in `data/row_state.json`.
5. `test_llm_cannot_set_derived_state` — a mapping record carrying
   `rate_region_basis` / `license_status` is ignored by `merge_mappings`;
   a record carrying `projection_note` lands in `mapper_note`.
6. `test_review_flag_not_prefix_matched` — add `region_unavailable`; assert
   `flag_present_clause(col, 'region')` is false.

---

## 5. Verification — run all of these, paste the output

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/row_state.py scripts/render_notes.py scripts/backfill_row_state.py scripts/apply_rates.py scripts/calibrate_confidence.py scripts/service_classifier.py scripts/detect_outliers.py scripts/projection_view.py scripts/merge_mappings.py
go build ./... && go vet ./internal/...
```

**Row-equivalence proof — the one that matters.** For each condition you
rewrote, the new condition must select the same rows as the old one, on a real
job DB that has been through Phase B and the C4 shim:

```bash
python - <<'PY' <path-to-projection.duckdb>
import duckdb, sys
c = duckdb.connect(sys.argv[1], read_only=True)
CASES = [
  ("no-rate-fallback",
   "SELECT aws_li_key FROM aws_li_to_gcp_li WHERE projection_note LIKE '%no-rate-fallback%'",
   "SELECT aws_li_key FROM aws_li_to_gcp_li WHERE rate_source IN ('no_rate','no_catalog')"),
  # ... one tuple per rewritten site ...
]
for name, old, new in CASES:
    a = {r[0] for r in c.execute(old).fetchall()}
    b = {r[0] for r in c.execute(new).fetchall()}
    print(f"{name}: old={len(a)} new={len(b)} added={len(b-a)} removed={len(a-b)}")
    for k in sorted(a - b)[:5]:
        print("   REMOVED", k)
PY
```

Run it against a DB captured **before** your changes (keep a copy). `removed`
must be 0 except at the three documented sites (detect_outliers' COALESCE fix,
the deleted `%Windows%` matcher, service_classifier's widening). State the count
and reason for each.

**Dollars must not move.** Before and after, same job DB:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute('SELECT round(SUM(aws_amortized_cost),2), round(SUM(gcp_projected_cost),2) FROM gcp_projection').fetchone())" <db>
```

**Note text WILL change — check it reads well.** Dump 30 notes before and after
and put both lists in your summary:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); [print('-',r[0]) for r in c.execute('SELECT DISTINCT projection_note FROM aws_li_to_gcp_li WHERE projection_note IS NOT NULL AND projection_note != %s LIMIT 30' % repr('')).fetchall()]" <db>
```

Every rendered note must be a readable English sentence with no duplicated
clauses, no trailing separators and no template placeholders left unfilled
(grep the output for `{`).

---

## 6. Expected, intended changes

State these explicitly in your summary rather than treating them as regressions:

- **`report.html` is NOT byte-identical.** Note *text* changes by design. Every
  **number** must be identical — that is the invariant, not the prose.
- `projection_note` no longer accumulates across phases; it reflects final state
  only. A row that was repaired mid-run no longer carries the history of its
  repairs in the note. That history now lives in `decided_by`,
  `passthrough_reason` and the diagnostics channel.
- Three reader conditions widen/narrow as documented above.

---

## 7. Do NOT do any of the following

- Do **not** read `projection_note` to decide anything, anywhere, ever. The only
  exceptions are `render_notes.py` (writes it) and `backfill_row_state.py` (the
  one-time shim, which must say so in its docstring).
- Do **not** keep prose tests "as a fallback". The shim replaces them.
- Do **not** add `rate_region_basis`, `rate_region_used` or `license_status` to
  `merge_mappings.COLUMNS`.
- Do **not** populate `license_status = 'modeled'`. Nothing models licences yet.
- Do **not** edit the 34 mappers individually — `_emit` and `merge_mappings`
  cover them.
- Do **not** restructure the `EXISTS` chains in `apply_rates.py`'s region
  updates.
- Do **not** modify `render_report.py`, including its note parser at L174. If
  the new note format breaks that parser's output, report it and stop — do not
  fix the report in this work order.
- Do **not** change what any condition *means*. You are changing how a state is
  detected, never which rows are in that state.
- Do **not** "fix" anything else you notice. List it at the bottom of your
  summary.

---

## 8. Done criteria

- [ ] `data/row_state.json` and `scripts/row_state.py` exist and load.
- [ ] Five new columns exist on fresh and migrated DBs; only `mapper_note` and
      `review_flags` are in `COLUMNS`; `projection_note` is not.
- [ ] `rate_source` has a documented `no_catalog` value and every `'no_rate'`
      reader has been reviewed (listed in the summary).
- [ ] No script appends to `projection_note`. `render_notes.py` is its only
      writer, and it `SET`s.
- [ ] `render_notes.py` runs in Phases 4, 5 and 6 and is provably idempotent.
- [ ] All 13 prose matchers are gone from live logic; the only note reads left
      are in `render_notes.py`, `backfill_row_state.py` and `render_report.py`.
- [ ] `test_no_script_reads_projection_note` passes and would fail if the
      pattern were reintroduced.
- [ ] Row-equivalence shows `removed = 0` except at the three documented sites.
- [ ] AWS and GCP totals unchanged to the cent.
- [ ] 30 sampled notes read as clean English with no placeholders or duplicates.
- [ ] 6 new tests pass; the full suite passes with no new failures.
