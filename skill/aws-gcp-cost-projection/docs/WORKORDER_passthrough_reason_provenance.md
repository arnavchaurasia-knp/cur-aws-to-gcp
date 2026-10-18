# WORK ORDER — Structured passthrough reason + decision provenance

**For an implementing AI / engineer with no prior context. Read this file fully
before editing anything. Every file path below is relative to
`skill/aws-gcp-cost-projection/`.**

This is a **data-model + instrumentation** change. It does NOT change any price,
any SKU choice, any classification, or any mapping. If a dollar figure moves,
you have made a mistake.

---

## 1. The problem

A row in `aws_li_to_gcp_li` can have `strategy='passthrough'`, which means
"carry the AWS cost forward 1:1 as the GCP cost". Ten different scripts set
that value, for ten completely different reasons:

| # | Who sets it | Real meaning |
|---|---|---|
| 1 | `apply_static_mappings.py` (various mappers) | Deliberate: GCP genuinely has no equivalent (S3 Early-Delete, FSx ONTAP, DynamoDB) |
| 2 | `apply_rates.py` (`enforce_accelerator_passthrough`) | Inferentia/Trainium/GPU — needs manual sizing |
| 3 | `apply_rates.py` (Aurora storage/IO block) | Unit model mismatch with the vCPU SKU |
| 4 | `apply_rates.py` (`no-catalog` block) | We wanted to map it, the catalog had no SKU |
| 5 | `service_classifier.py` (`mode: review`) | `service_map.json` policy says passthrough |
| 6 | `fix_storage_misroute.py` | Backstop: LLM put a non-storage service on Cloud Storage |
| 7 | `ensure_full_coverage.py` | **Defect**: the LLM dropped the row entirely |
| 8 | `validate_fix.py` (3 sites) | **Defect**: mapped SKU has no reachable rate / echo bug / detail-less row |
| 9 | `apply_review_fixes.py` | LLM vetoed a review candidate |
| 10 | `outlier_gate.py` | **Defect**: over-projection clamped (writes `needs_reprice`) |

**The bug: the database cannot tell these apart.** The schema
(`merge_mappings.py`, `CREATE TABLE aws_li_to_gcp_li`) has no field for *why*.
There is no `passthrough_reason`, no `decided_by`, no `decision_source`. All ten
produce a row that is byte-identical apart from an English sentence appended to
the free-text `projection_note` column.

Three consequences, all real and all observed:

1. **A broken run looks exactly like a good run.** If a static mapper raises an
   exception, `apply_static_mappings.main()` catches it, writes an empty mapping
   file, and those rows end up as case 7 passthrough. The projection then equals
   the AWS bill, which reads as a plausible finding. No gate objects.

2. **Intent is encoded as prose, and the prose is matched with `ILIKE`.**
   `apply_static_mappings.py` defines `INTENTIONAL_PASSTHROUGH_NOTE_ILIKE =
   ["%no GCS equivalent%", "%no GCP equivalent%", "%no direct GCP equivalent%"]`
   and `intentional_passthrough_exclude_clause()`. Four scripts
   (`auto_review.py`, `ensure_catalog_coverage.py` ×2, `llm_override_guard.py`)
   use it to decide "don't repair this row". A grep pattern is acting as a type
   system.

3. **The LLM can grant its own rows immunity.** `projection_note` is one of the
   fields the Phase-2 LLM writes. If the model writes "no GCP equivalent at this
   tier" into a note, `llm_override_guard._is_intentional_passthrough_note()`
   does a plain substring test on it and that row becomes permanently exempt
   from every deterministic repair pass.

**Goal of this work order:** make the reason a first-class, closed-enum column,
so that (a) gates can count defect-passthrough dollars, (b) repair scripts can
check a flag instead of a sentence, and (c) the LLM cannot assert intent.

---

## 2. Files to read before you start

Read these in full. Do not skim.

- `scripts/merge_mappings.py` — owns the `aws_li_to_gcp_li` schema and `COLUMNS`
- `scripts/apply_static_mappings.py` lines 118–150 — `INTENTIONAL_PASSTHROUGH_NOTE_ILIKE`, `intentional_passthrough_exclude_clause`, and the `_emit()` row-builder (~line 632)
- `scripts/validate_fix.py` — gate structure, `report["violations"][...]`, `n_hard`
- `scripts/projection_view.py` — the `gcp_projection` VIEW `SELECT` list
- `scripts/config_loader.py` — how `data/*.json` files are loaded
- `tests/conftest.py` and `tests/test_merge_mappings.py` — test conventions

---

## 3. The design

### 3.1 One new data file: `data/passthrough_reasons.json`

The single source of truth. Each reason has a `kind`:

- `intentional` — the passthrough is the correct, final answer. Repair scripts
  must leave it alone.
- `defect` — the passthrough is a fallback because something failed. It must be
  visible, countable, and gate-able.

### 3.2 Two new columns on `aws_li_to_gcp_li`

- `passthrough_reason VARCHAR` — one of the enum keys, or NULL when
  `strategy NOT IN ('passthrough','needs_reprice')`.
- `decided_by VARCHAR` — the script filename that last set the strategy
  (e.g. `apply_rates.py`). Plain provenance, no enum.

### 3.3 One new gate: `passthrough_integrity`

Added to `validate_fix.py`. Two checks:
1. Any row with `strategy IN ('passthrough','needs_reprice')` and
   `passthrough_reason IS NULL` and `aws_amortized_cost > 1` → violation
   ("a passthrough was written without declaring why").
2. Sum of `aws_amortized_cost` on `defect`-kind passthrough rows, as a share of
   total workload cost, above `defect_passthrough_budget_pct` → violation.

### 3.4 Prose matching becomes a fallback, not the primary test

`intentional_passthrough_exclude_clause()` starts checking the column first and
keeps the ILIKE patterns as an `OR` for pre-existing DBs. **Do not delete the
ILIKE patterns.**

---

## 4. Step-by-step implementation

Do the steps in order. Run the test suite after each step.

---

### STEP 1 — Create `data/passthrough_reasons.json`

New file. Exact content:

```json
{
  "_meta": {
    "description": "Closed enum of reasons a row carries strategy='passthrough' or 'needs_reprice'. kind=intentional means the passthrough is the correct final answer and repair passes must not touch it. kind=defect means something failed and the row must be countable by gates.",
    "version": 1
  },
  "defect_passthrough_budget_pct": 5.0,
  "reasons": {
    "no_gcp_equivalent": {
      "kind": "intentional",
      "label": "No GCP equivalent",
      "description": "GCP has no comparable service or charge type. Permanent decision made by a static mapper or service_map."
    },
    "unit_model_mismatch": {
      "kind": "intentional",
      "label": "Unit models incompatible",
      "description": "Both clouds charge for this, but in units that cannot be converted (e.g. S3 per-1k requests vs GCS class-A ops, Aurora I/O vs Cloud SQL)."
    },
    "accelerator_manual_sizing": {
      "kind": "intentional",
      "label": "Accelerator — manual sizing",
      "description": "Inferentia/Trainium/GPU workload. No automatic equivalent; requires human sizing."
    },
    "service_policy_review": {
      "kind": "intentional",
      "label": "Policy: review",
      "description": "data/service_map.json declares mode='review' for this service."
    },
    "unresolved_sku": {
      "kind": "defect",
      "label": "No SKU resolved",
      "description": "We intended to map this row, but no catalog SKU or no region-reachable rate was found."
    },
    "storage_misroute_backstop": {
      "kind": "defect",
      "label": "Storage misroute backstop",
      "description": "A non-storage service was mapped onto Cloud Storage and was rerouted to passthrough."
    },
    "echo_reset": {
      "kind": "defect",
      "label": "Echo bug reset",
      "description": "gcp_service echoed the AWS product name back; reset to passthrough."
    },
    "detail_less_row": {
      "kind": "defect",
      "label": "No charge-type detail",
      "description": "The source bill has no unit/operation/usage_type detail, so no mechanic can price the row."
    },
    "llm_dropped": {
      "kind": "defect",
      "label": "LLM left row unmapped",
      "description": "The Phase-2 LLM produced no mapping for this row; ensure_full_coverage backstopped it."
    },
    "llm_veto": {
      "kind": "defect",
      "label": "LLM vetoed the fix",
      "description": "A review or outlier candidate was vetoed and the row stayed at AWS cost."
    },
    "clamped_outlier": {
      "kind": "defect",
      "label": "Clamped over-projection",
      "description": "The projected GCP cost exceeded the plausibility threshold and was clamped."
    },
    "mapper_error": {
      "kind": "defect",
      "label": "Mapper raised",
      "description": "The deterministic mapper for this mechanic_group raised an exception."
    },
    "unclassified": {
      "kind": "defect",
      "label": "Reason not declared",
      "description": "A passthrough was written without declaring a reason. Always a bug in the writing script. Also used for LLM-authored passthroughs, which may never self-declare intent."
    }
  }
}
```

---

### STEP 2 — Add a tiny shared helper: `scripts/passthrough_reasons.py`

New file. Exact content:

```python
#!/usr/bin/env python3
"""Single source of truth for passthrough reason codes.

Loads data/passthrough_reasons.json and exposes the enum plus the
intentional/defect split. Import this instead of hardcoding reason strings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_CFG = _cfg("passthrough_reasons") or {}
REASONS = _CFG.get("reasons", {})
DEFECT_BUDGET_PCT = float(_CFG.get("defect_passthrough_budget_pct", 5.0))

VALID_REASONS = frozenset(REASONS.keys())
INTENTIONAL_REASONS = frozenset(
    k for k, v in REASONS.items() if v.get("kind") == "intentional"
)
DEFECT_REASONS = frozenset(
    k for k, v in REASONS.items() if v.get("kind") == "defect"
)
UNCLASSIFIED = "unclassified"


def is_valid(reason) -> bool:
    return reason in VALID_REASONS


def kind(reason) -> str:
    """Return 'intentional', 'defect', or 'unknown'."""
    return REASONS.get(reason, {}).get("kind", "unknown")


def sql_in_list(reasons) -> str:
    """Return a SQL literal list, e.g. "'a', 'b'". Safe: keys are from our own
    JSON file and are validated identifiers, never user input."""
    safe = [r for r in sorted(reasons) if r.replace("_", "").isalnum()]
    return ", ".join("'" + r + "'" for r in safe) or "''"
```

Verify it loads:

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import passthrough_reasons as p; print(len(p.VALID_REASONS), sorted(p.INTENTIONAL_REASONS))"
```

Expected: `13 ['accelerator_manual_sizing', 'no_gcp_equivalent', 'service_policy_review', 'unit_model_mismatch']`

If `config_loader.load_data_config` cannot find the file, check how other
`data/*.json` files are named there and match that convention exactly. Do not
invent a new loader.

---

### STEP 3 — Add the two columns to the schema

In `scripts/merge_mappings.py`:

**3a.** In the `CREATE TABLE IF NOT EXISTS aws_li_to_gcp_li (...)` block, after
the `assumption_note      TEXT` line, add:

```
            passthrough_reason  TEXT,
            decided_by          TEXT
```

**3b.** Immediately below, where the existing `if "gcp_sku_unit" not in
existing_cols:` / `is_assumption` / `assumption_note` guards are, add two more
in the same style (this is what upgrades older job DBs):

```python
    if "passthrough_reason" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN passthrough_reason TEXT")
    if "decided_by" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN decided_by TEXT")
```

**3c.** Add both to the `COLUMNS` list at the top of the file, at the END of the
list (order must match the INSERT, which is generated from this list, so
appending is safe):

```python
    "assumption_note",
    "passthrough_reason",
    "decided_by",
]
```

**3d.** In `main()`, right before `records.append(...)`, normalise what came in
from the mapping JSON files. Find this existing block:

```python
    for r in collapsed_rows:
        row_dict = dict(r)
        if not row_dict.get("assumption_note") and row_dict.get("assumption"):
            row_dict["assumption_note"] = row_dict["assumption"]
        records.append(tuple(row_dict.get(col) for col in COLUMNS))
```

and replace the body with:

```python
    for r in collapsed_rows:
        row_dict = dict(r)
        if not row_dict.get("assumption_note") and row_dict.get("assumption"):
            row_dict["assumption_note"] = row_dict["assumption"]
        # passthrough_reason is a closed enum. A mapper may declare one; the LLM
        # may not (an LLM-authored sentence must never grant a row immunity from
        # repair passes — see WORKORDER_passthrough_reason_provenance.md §1.3).
        _strategy = row_dict.get("strategy")
        _reason = row_dict.get("passthrough_reason")
        if _strategy in ("passthrough", "needs_reprice"):
            if not _pr.is_valid(_reason):
                if _reason:
                    print(
                        "WARNING: unknown passthrough_reason "
                        + repr(_reason) + " on " + str(row_dict.get("aws_li_key"))
                        + " -> unclassified", file=sys.stderr)
                row_dict["passthrough_reason"] = _pr.UNCLASSIFIED
        else:
            row_dict["passthrough_reason"] = None
        if not row_dict.get("decided_by"):
            row_dict["decided_by"] = row_dict.get("_source_file") or "merge_mappings.py"
        records.append(tuple(row_dict.get(col) for col in COLUMNS))
```

and add near the other imports at the top of `merge_mappings.py`:

```python
import passthrough_reasons as _pr
```

---

### STEP 4 — Stamp the reason at every site that writes passthrough

For each site below: add `passthrough_reason='<code>'` and
`decided_by='<script>.py'` to the existing `SET` clause. **Change nothing else
on those lines — not the note text, not the WHERE clause, not the strategy.**

Use the quoted anchor string to locate each site; line numbers may have drifted.

#### 4a. `scripts/apply_rates.py`

| Anchor (unique substring) | reason |
|---|---|
| `'accelerator component folded into passthrough'` | `accelerator_manual_sizing` |
| `'AI accelerator (Inferentia/Trainium/GPU) — Manual sizing required` | `accelerator_manual_sizing` |
| `'Aurora/RDS Storage+IO row — unit mismatch with vCPU SKU; '` | `unit_model_mismatch` |
| the `UPDATE` whose note mentions `no-catalog` (near `projection_note LIKE '%no-catalog%'`) | `unresolved_sku` |

`decided_by='apply_rates.py'` on all four.

Example of the edit shape — before:

```python
        conn.execute("UPDATE aws_li_to_gcp_li SET strategy='passthrough', "
                     "gcp_service='Manual Sizing Required', gcp_sku_id=NULL, gcp_sku_name=NULL, "
```

after:

```python
        conn.execute("UPDATE aws_li_to_gcp_li SET strategy='passthrough', "
                     "passthrough_reason='accelerator_manual_sizing', decided_by='apply_rates.py', "
                     "gcp_service='Manual Sizing Required', gcp_sku_id=NULL, gcp_sku_name=NULL, "
```

#### 4b. `scripts/validate_fix.py` — three sites

| Anchor | reason |
|---|---|
| `' [validator: map-with-no-rate downgraded to passthrough]'` | `unresolved_sku` |
| `' [validator: echo bug — gcp_service was AWS name, reset to passthrough]'` | `echo_reset` |
| `' [validator: detail-less row downgraded to passthrough]'` | `detail_less_row` |

`decided_by='validate_fix.py'` on all three.

#### 4c. `scripts/service_classifier.py`

In the `review_updates` `UPDATE` (the one with
`strategy = 'passthrough', gcp_sku_id = NULL, gcp_sku_name = NULL`), add
`passthrough_reason = 'service_policy_review', decided_by = 'service_classifier.py',`.

#### 4d. `scripts/fix_storage_misroute.py`

In the `UPDATE` with `SET strategy = 'passthrough',`, add
`passthrough_reason = 'storage_misroute_backstop', decided_by = 'fix_storage_misroute.py',`.

#### 4e. `scripts/ensure_full_coverage.py`

This script INSERTs rather than updates. Find where it builds the row it inserts
(the places using the `"ignore" if _is_ignore(...) else "passthrough"` expression)
and include `passthrough_reason = 'llm_dropped'` (NULL when the resolution is
`ignore`) and `decided_by = 'ensure_full_coverage.py'` in the INSERT column list
and values. Keep the existing `NOTE` text unchanged.

#### 4f. `scripts/apply_review_fixes.py`

In the `UPDATE` with `SET strategy = 'passthrough',`, add
`passthrough_reason = 'llm_veto', decided_by = 'apply_review_fixes.py',`.

#### 4g. `scripts/outlier_gate.py`

In the `UPDATE` with `SET strategy = 'needs_reprice',`, add
`passthrough_reason = 'clamped_outlier', decided_by = 'outlier_gate.py',`.

#### 4h. `scripts/apply_static_mappings.py` — the mappers

The mappers build dicts, not SQL. There are many passthrough emissions, so do
this **centrally**, not per mapper:

1. In `_emit()` (~line 632, the standardized row-builder), accept an optional
   `passthrough_reason=None` keyword and put it in the returned dict. Also set
   `decided_by = "apply_static_mappings.py"` on every row `_emit` returns.
2. Still inside `_emit()`, add the default: if `strategy == "passthrough"` and
   no `passthrough_reason` was passed, infer it from the note text using the
   existing `INTENTIONAL_PASSTHROUGH_NOTE_ILIKE` patterns —
   `no_gcp_equivalent` if the note matches one of them, else `unclassified`.
3. Do **not** go through all 34 mappers adding explicit reasons in this work
   order. The `_emit` default handles them. A later pass can replace the
   note-sniffing default with explicit per-mapper reasons.

4. In `main()`, in the per-handler `except Exception` block (the one that prints
   `WARNING: {group} mapper raised ... — skipping group`), the empty result
   currently hides the failure. Change it so the group's rows are emitted as
   explicit `mapper_error` passthroughs instead of an empty list:

```python
        except Exception as e:
            print(f"WARNING: {group} mapper raised {type(e).__name__}: {e} — "
                  f"emitting {len(by_group[group])} row(s) as mapper_error passthrough",
                  file=sys.stderr)
            result = [
                {
                    "aws_li_key": _r["aws_li_key"],
                    "gcp_service": "Manual Review — Mapper Error",
                    "strategy": "passthrough",
                    "component": None,
                    "unit_multiplier": 1.0,
                    "gcp_region": _r.get("gcp_region"),
                    "projection_note": f"[mapper_error] {group} mapper raised "
                                       f"{type(e).__name__}: {e}",
                    "mapping_confidence": 0.0,
                    "is_workload": True,
                    "break_down": False,
                    "passthrough_reason": "mapper_error",
                    "decided_by": "apply_static_mappings.py",
                }
                for _r in by_group[group]
            ]
```

This single change is what makes a crashed mapper visible instead of
indistinguishable from a correct passthrough.

---

### STEP 5 — Expose the columns on the `gcp_projection` view

In `scripts/projection_view.py`, in the final `SELECT` list, find:

```sql
        m.strategy, m.gcp_service, m.gcp_sku_id, m.component,
        m.unit_multiplier,
```

and change to:

```sql
        m.strategy, m.gcp_service, m.gcp_sku_id, m.component,
        m.unit_multiplier, m.passthrough_reason, m.decided_by,
```

Nothing else in that file changes. Do not touch any `CASE` expression — the
dollar arithmetic must stay byte-identical.

---

### STEP 6 — Add the `passthrough_integrity` gate to `validate_fix.py`

Add a function alongside the other gate functions (model it on
`passthrough_budget_exceeded`, which is already in this file — copy its
structure, its `try/except` shape, and its violation-dict keys):

```python
def passthrough_integrity(con) -> list:
    """Two checks:
      1. a passthrough/needs_reprice row that never declared WHY (NULL reason)
      2. defect-kind passthrough spend over the configured budget
    """
    import passthrough_reasons as _pr
    violations = []
    try:
        undeclared = con.execute("""
            SELECT m.aws_li_key, c.product, c.aws_amortized_cost
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING(aws_li_key)
            WHERE m.strategy IN ('passthrough', 'needs_reprice')
              AND m.passthrough_reason IS NULL
              AND c.aws_amortized_cost > 1
            ORDER BY c.aws_amortized_cost DESC
        """).fetchall()
        for key, product, cost in undeclared:
            violations.append({
                "gate": "passthrough_integrity",
                "aws_li_key": key,
                "message": f"passthrough written with no passthrough_reason "
                           f"({product}, ${cost:,.2f}) — the writing script must declare one",
            })

        defect_list = _pr.sql_in_list(_pr.DEFECT_REASONS)
        row = con.execute(f"""
            SELECT
              COALESCE(SUM(CASE WHEN m.passthrough_reason IN ({defect_list})
                                THEN c.aws_amortized_cost ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN c.is_workload THEN c.aws_amortized_cost ELSE 0 END), 0)
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING(aws_li_key)
        """).fetchone()
        defect_spend, workload_spend = float(row[0]), float(row[1])
        pct = (100.0 * defect_spend / workload_spend) if workload_spend else 0.0
        if pct > _pr.DEFECT_BUDGET_PCT:
            by_reason = con.execute(f"""
                SELECT m.passthrough_reason, count(*), SUM(c.aws_amortized_cost)
                FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING(aws_li_key)
                WHERE m.passthrough_reason IN ({defect_list})
                GROUP BY 1 ORDER BY 3 DESC
            """).fetchall()
            detail = "; ".join(f"{r}: {n} row(s), ${s:,.2f}" for r, n, s in by_reason)
            violations.append({
                "gate": "passthrough_integrity",
                "aws_li_key": None,
                "message": f"defect-kind passthrough is {pct:.1f}% of workload spend "
                           f"(${defect_spend:,.2f}), over the "
                           f"{_pr.DEFECT_BUDGET_PCT:.1f}% budget. Breakdown — {detail}",
            })
    except Exception as e:
        violations.append({
            "gate": "passthrough_integrity",
            "aws_li_key": None,
            "message": f"passthrough_integrity gate could not run: {e}",
        })
    return violations
```

Then register it next to the existing registrations — find the line

```python
    report["violations"]["passthrough_budget"] = passthrough_budget_exceeded(con)
```

and add below it:

```python
    report["violations"]["passthrough_integrity"] = passthrough_integrity(con)
```

This makes it count toward `n_hard`, which is correct: both conditions are real
defects.

---

### STEP 7 — Make the repair scripts check the column, keeping prose as fallback

In `scripts/apply_static_mappings.py`, change
`intentional_passthrough_exclude_clause()` to:

```python
def intentional_passthrough_exclude_clause(column):
    """SQL AND-clause excluding rows that are INTENTIONAL, permanent passthroughs.

    Primary test: the structured passthrough_reason column (kind='intentional').
    Fallback: the legacy projection_note ILIKE patterns, kept so job DBs created
    before passthrough_reason existed still behave correctly. Do not remove them.

    `column` is the fully-qualified projection_note reference, e.g.
    'm.projection_note'. The matching passthrough_reason column is derived from
    the same table alias.
    """
    note_clause = " AND ".join(
        f"COALESCE({column}, '') NOT ILIKE '{p}'" for p in INTENTIONAL_PASSTHROUGH_NOTE_ILIKE
    )
    alias = column.rsplit(".", 1)[0] + "." if "." in column else ""
    try:
        import passthrough_reasons as _pr
        intent_list = _pr.sql_in_list(_pr.INTENTIONAL_REASONS)
        reason_clause = (
            f"COALESCE({alias}passthrough_reason, '') NOT IN ({intent_list})"
        )
        return f"({reason_clause}) AND ({note_clause})"
    except Exception:
        return note_clause
```

**Important:** every caller of this function joins `aws_li_to_gcp_li` under the
alias used in `column` (verified for `auto_review.py` and
`ensure_catalog_coverage.py`, both of which pass `"m.projection_note"`). If you
find a caller that does not, leave that caller on the note-only clause rather
than producing invalid SQL.

In `scripts/llm_override_guard.py`, extend
`_is_intentional_passthrough_note()` so callers can pass the reason:

```python
def _is_intentional_passthrough_note(note: str | None, reason: str | None = None) -> bool:
    if reason:
        try:
            import passthrough_reasons as _pr
            return reason in _pr.INTENTIONAL_REASONS
        except Exception:
            pass
    if not note:
        return False
    note_lower = note.lower()
    for pattern in INTENTIONAL_PASSTHROUGH_NOTE_ILIKE:
        if pattern.replace("%", "").lower() in note_lower:
            return True
    return False
```

Then update its call site inside `validate_override()` to read the row's
`passthrough_reason` from the DB and pass it. Because merge forces LLM-authored
passthroughs to `unclassified` (Step 3d), the LLM can no longer self-declare
intent — which is the point of this work order.

---

### STEP 8 — Tests

New file `tests/test_passthrough_reason.py`. Follow the fixture style in
`tests/test_merge_mappings.py`.

Write exactly these five tests:

1. `test_enum_loads_and_splits` — every reason in
   `data/passthrough_reasons.json` has `kind` ∈ {`intentional`, `defect`};
   `INTENTIONAL_REASONS` and `DEFECT_REASONS` are disjoint and together equal
   `VALID_REASONS`.
2. `test_merge_defaults_llm_passthrough_to_unclassified` — feed
   `merge_mappings` a mapping record with `strategy='passthrough'` and no
   `passthrough_reason`; assert the stored value is `'unclassified'`.
3. `test_merge_rejects_unknown_reason` — a record with
   `passthrough_reason='totally_made_up'` is stored as `'unclassified'`.
4. `test_merge_nulls_reason_for_mapped_rows` — a record with `strategy='map'`
   and `passthrough_reason='no_gcp_equivalent'` is stored with
   `passthrough_reason IS NULL`.
5. `test_gate_flags_undeclared_passthrough` — build a tiny DB with one
   passthrough row at `aws_amortized_cost=100` and `passthrough_reason IS NULL`;
   assert `passthrough_integrity(con)` returns exactly one violation.

---

## 5. Verification — run all of these, paste the output

```bash
cd skill/aws-gcp-cost-projection
python -m pytest tests/ -q
```

All tests must pass. The pre-existing count is the baseline — **nothing that
passed before may fail now.**

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import passthrough_reasons as p; assert p.INTENTIONAL_REASONS.isdisjoint(p.DEFECT_REASONS); assert p.INTENTIONAL_REASONS | p.DEFECT_REASONS == p.VALID_REASONS; print('enum ok')"
```

Syntax-compile every file you touched:

```bash
python -m py_compile scripts/merge_mappings.py scripts/apply_rates.py scripts/validate_fix.py scripts/service_classifier.py scripts/fix_storage_misroute.py scripts/ensure_full_coverage.py scripts/apply_review_fixes.py scripts/outlier_gate.py scripts/apply_static_mappings.py scripts/projection_view.py scripts/llm_override_guard.py scripts/passthrough_reasons.py
```

On an existing job DB (any `projection.duckdb` you have), confirm the migration
and that every passthrough now declares a reason:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute(\"SELECT COALESCE(passthrough_reason,'<NULL>'), decided_by, count(*) FROM aws_li_to_gcp_li WHERE strategy IN ('passthrough','needs_reprice') GROUP BY 1,2 ORDER BY 3 DESC\").fetchall())" <path-to-projection.duckdb>
```

**The dollars must not move.** Before your changes, record:

```bash
python -c "import duckdb,sys; c=duckdb.connect(sys.argv[1]); print(c.execute('SELECT round(SUM(aws_amortized_cost),2), round(SUM(gcp_projected_cost),2) FROM gcp_projection').fetchone())" <path-to-projection.duckdb>
```

Run it again after. **Both numbers must be identical.** If either changed, you
altered pricing logic — revert and find out where.

---

## 6. Do NOT do any of the following

- Do **not** delete `INTENTIONAL_PASSTHROUGH_NOTE_ILIKE` or any ILIKE pattern.
  They remain the fallback for older job DBs.
- Do **not** change any `CASE` expression in `projection_view.py`.
- Do **not** change any `unit_multiplier`, SKU choice, rate lookup, or
  `mechanic_group`.
- Do **not** change `strategy` values anywhere. You are labelling existing
  decisions, not re-deciding them.
- Do **not** reword existing `projection_note` text.
- Do **not** add `passthrough_reason` to any LLM prompt in
  `internal/jobs/orchestrate.go`. The LLM must not set this field — that is the
  whole point.
- Do **not** touch Phase 6 / `render_report.py` in this work order.
- Do **not** "fix" any issue you notice along the way. Note it at the bottom of
  your summary instead.

---

## 7. Done criteria

- [ ] `data/passthrough_reasons.json` and `scripts/passthrough_reasons.py` exist
      and load.
- [ ] `aws_li_to_gcp_li` has `passthrough_reason` and `decided_by`, on both
      fresh and migrated DBs.
- [ ] All 10 passthrough-writing sites stamp a reason and `decided_by`.
- [ ] A crashed mapper now produces `mapper_error` rows, not an empty file.
- [ ] LLM-authored passthroughs land as `unclassified`, never as an
      intentional reason.
- [ ] `gcp_projection` exposes both columns.
- [ ] `passthrough_integrity` is registered in `validate_fix.py` and counts
      toward `n_hard`.
- [ ] `intentional_passthrough_exclude_clause()` checks the column first, prose
      second.
- [ ] 5 new tests pass; the full suite passes with no new failures.
- [ ] On a real job DB, the AWS total and GCP total are unchanged to the cent.
