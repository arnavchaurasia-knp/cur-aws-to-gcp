# Phase 3 — Review

**Run by:** one **fresh** sub-agent — not one of the mapping agents.
The fresh agent comes in without their investment in the choices and
is more willing to overturn them.
**Reads:** `projection-audit/mapping-notes.md`, `aws_li_to_gcp_li`,
`aws_li_catalog`, `data/skus/`.
**Writes:** corrections directly to `aws_li_to_gcp_li`; appends a
`## Review findings` section to `mapping-notes.md`.
**Returns to main:** one-paragraph summary of what changed and what
was confirmed.

## Why this exists

Outlier queries (Phase 5) are blunt instruments — they catch the
*shape* of bugs but not the agent's *reasoning* about them. The notes
file is where the reasoning lives, so review happens there, by an
agent that didn't write the reasoning.

Without this phase, mapping decisions get rubber-stamped by the agent
that made them, and the bugs that survive are exactly the ones with
plausible-sounding rationales.

## Briefing the review agent

Give it:
- The two governing principles from Phase 2:
  **Bill is ground truth** and **Equivalence intent (60/40)**.
- Path to the schema reference: `reference/schemas.md`.
- Path to `mapping-notes.md` (read), `aws_li_to_gcp_li` (read+write),
  `aws_li_catalog` (read), `data/skus/` (read).
- This file (`phases/03-review.md`).

## Three checks (do all three)

### 1. Check every `Alt:` line — was the trade-off honest?

For each `## <aws_li_key>` entry that has an `Alt:` line:
- Did the picked SKU win the 60/40 trade-off honestly?
- Does the rationale rest on a slight perf gain ("retain Intel ISA",
  "production-sized so HA")? Challenge it per **Bill is ground truth**
  — the bill must show the customer is paying for that perf/HA today,
  in a visible line item or a description literal (Multi-AZ, etc.).
- If the rationale doesn't have a line-item or AWS-description anchor,
  **demote to the alternative**. Update the row in `aws_li_to_gcp_li`
  (set the alternative's `gcp_sku_id`, recompute `unit_multiplier` if
  the resource_group changed, update `projection_note`).

### 2. Resolve every `Open:` line

A back-check that came back `fail-by-Nx` or `degenerate` is a real
signal. Walk the bill — find another row in the same convention
(e.g. an S3 PUT row to verify `Usage Quantity` is raw count) and
either confirm the multiplier or fix it.

**Never let an `Open:` survive into Phase 5.** Once outliers fire,
"I noted this earlier" becomes the rationalization that lets the bug
ship.

### 3. Audit every passthrough — assume it's wrong until proven otherwise

`passthrough` is the mapping skill's null answer: AWS cost forwarded
1:1 to GCP, diff=$0, no information for the customer. Phase 2's
guidance is that passthrough is a LAST RESORT — but agents under
time pressure use it as a comfortable fallback. Reverse the
default: when you see a passthrough row, **assume Phase 2 gave
up** and verify there's no real mapping before letting it stand.

For each row where `aws_li_to_gcp_li.strategy = 'passthrough'`:

```sql
SELECT c.product, c.usage_type, c.operation, c.aws_region,
       c.aws_amortized_cost, m.gcp_service, m.projection_note
FROM aws_li_catalog c
JOIN aws_li_to_gcp_li m USING(aws_li_key)
WHERE m.strategy = 'passthrough'
ORDER BY c.aws_amortized_cost DESC;
```

Walk each row top-down by AWS cost. For each, ask:

1. **What is this charge actually billing for?** Read the
   `usage_type` and `operation` literally. Don't accept a vague
   read like "S3 storage" — the bill text says exactly what
   sub-feature this is (S3 Glacier Transition, S3-INT monitoring,
   bucket count, etc.).

2. **Is there a GCP service that does this same work?** Name it
   if so — Cloud Storage Class A ops, GKE cluster fee, Cloud DNS,
   etc. If you can name one, **there is almost certainly a SKU
   for it**. Use `find-sku.sh` to find it.

3. **What was Phase 2's stated reason for passthrough?** Read
   `projection_note`. The only valid reasons are:
   - "No GCP equivalent exists at all" (genuinely no service does
     this work on GCP — rare)
   - "Unit irreconcilable after Branch A/B/m=1/m=N back-check"
     (rare)

   Anything else is overruled. Specifically these are **NOT valid**:
   - "Ratio would be <X vs <service>" — that's information, map it
   - "Rate too low/high vs GCP" — that's information, map it
   - "AWS PRC discount makes this unusual" — show the gap, don't
     erase it
   - "Not sure which SKU" — demote to `confidence: low` and pick

4. **If you find a real mapping**, update the row:
   - `strategy` ← `direct` (or `break_down` etc.)
   - `gcp_service` ← the actual service name
   - `gcp_sku_id` ← the SKU from `find-sku.sh`
   - `unit_multiplier` per the unit-reconcile rules
   - `projection_note` ← one sentence: rationale + back-check ratio

5. **If passthrough genuinely stands** (the rare valid cases),
   rewrite the `projection_note` to be **specific and definitive**:
   *"S3-INT per-object monitoring fee; GCS Autoclass bundles
   tiering monitoring into the storage rate with no separate SKU"*
   — not the templated *"No direct GCP SKU mapping available"*.
   Phase 6 renders `projection_note` into the report's Description
   column, so the reader sees your reasoning, not a placeholder.

A typical Rooter-scale bill should have 0-3 legitimate passthroughs
after Phase 3 (AWS Support, AWS Config item recording if not zero-
costed, maybe one niche sub-feature). If Phase 2 left 20 of them,
fix 17.

### 4. Look across entries for repeated patterns

Five RDS rows all flagged with `aws=$0 — likely free tier`? Not five
free-tier coincidences; one systematic mapping bug. Likewise for any
repeated-shape uncertainty.

When you spot a pattern:
- Investigate what the rows have in common (same usage_type?
  same description prefix? same multiplier?).
- Fix the root cause, not row-by-row.
- Note the pattern in your review-findings summary.

## Writing corrections

The review agent **writes corrections directly to
`aws_li_to_gcp_li`** — there's no propose-back-to-main pattern. That's
how fixes get watered down.

Each correction should also append a brief justification to
`mapping-notes.md` under a new `## Review findings` section at the
bottom. Format:

```markdown
## Review findings (Phase 3)

- **<aws_li_key>** — <one-line: what was wrong, what changed,
  citation to bill or rule>
- ...

### Patterns
- <one-line: pattern noticed across N rows, what was fixed>
```

## Returning to main

One paragraph. Concrete numbers. Example shape:

> Reviewed 217 mapping rows and 34 mapping-notes entries. Demoted
> 3 rows from Cloud SQL Regional → Zonal (descriptions said Single-AZ;
> mapper had inferred HA from instance size). Fixed 1 ElastiCache
> mapping (Memcached → Memorystore for Memcached, mapper had used
> Redis Basic). Confirmed 4 `Open:` lines via cross-row checks
> (S3-ops multiplier=1.0 verified against PUT and GET rows).
> Ready for Phase 4.
