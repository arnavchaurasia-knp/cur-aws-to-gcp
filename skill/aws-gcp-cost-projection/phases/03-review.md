# Phase 3 — Review

**Run by:** one **fresh** sub-agent — not one of the mapping agents.
The fresh agent comes in without their investment in the choices and
is more willing to overturn them.
**Reads:** `projection-audit/mapping-notes.md`, `aws_li_to_gcp_li`,
`aws_li_catalog`, `data/skus/`, and `review_flags.md`.
**Writes:** corrections directly to `aws_li_to_gcp_li`; appends a
`## Review findings` section to `mapping-notes.md`.
**Returns to main:** one-paragraph summary of what changed and what
was confirmed.

## FIRST LINE OF THIS PHASE — write progress marker

Before any other work, write `progress.json` in the job working directory:

```python
import json
with open("progress.json", "w") as f:
    json.dump({"phase": 3, "phase_name": "Review", "last_activity": "Verifying mappings"}, f)
```

## Briefing the review agent

Give it:
- The two governing principles from Phase 2:
  **Bill is ground truth** and **Equivalence intent (60/40)**.
- Path to `mapping-notes.md` (read), `aws_li_to_gcp_li` (read+write),
  `review_flags.md` (read).

## Three checks (do all three)

### 1. Check every `Alt:` line — was the trade-off honest?

For each `## <aws_li_key>` entry that has an `Alt:` line in `mapping-notes.md`:
- Did the picked SKU win the 60/40 trade-off honestly?
- Does the rationale rest on a slight perf gain? Challenge it per **Bill is ground truth**.
- If the rationale doesn't have a line-item or AWS-description anchor,
  **demote to the alternative**. Update the row in `aws_li_to_gcp_li`.

### 2. Resolve every `Open:` line

A back-check that came back `fail-by-Nx` or `degenerate` is a real
signal. Walk the bill — find another row in the same convention
and either confirm the multiplier or fix it.

### 3. Audit every passthrough using `review_flags.md`

A mechanical script (`auto_review.py`) has already run before this phase.
It auto-corrected deterministic math errors and outputted a file named `review_flags.md`.

Read `review_flags.md`. It contains two sections:
1. **ILLEGAL PASSTHROUGH ROWS FOUND**: You MUST fix every single row listed here. Look up the SKU via `find-sku.sh` and UPDATE the database.
2. **OTHER PASSTHROUGHS TO AUDIT**: Review these. If you can find a real mapping, fix it. If it is genuinely a valid passthrough (e.g. AWS Support), update the `projection_note` in the DB to explicitly state why.

## Writing corrections

**Phase 3 never re-enters Phase 2.** All fixes happen via direct SQL UPDATE on `aws_li_to_gcp_li`. You already have the full catalog — look up the correct SKU via `find-sku.sh` and UPDATE in place. Do not re-run any Phase 2 script or prompt.

Write corrections directly to `aws_li_to_gcp_li`.
Append a brief justification to `mapping-notes.md` under a new `## Review findings` section at the bottom.

## Returning to main

One paragraph. Concrete numbers. Example shape:

> Reviewed 217 mapping rows and 34 mapping-notes entries. Demoted
> 3 rows from Cloud SQL Regional → Zonal. Fixed 1 ElastiCache
> mapping. Confirmed 4 `Open:` lines via cross-row checks. Re-mapped 2 illegal passthroughs from review_flags.md.
> Ready for Phase 4.
