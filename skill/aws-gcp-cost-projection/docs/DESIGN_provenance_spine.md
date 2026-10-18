# DESIGN — The provenance spine: every value knows where it came from, how sure it is, and how old

Status: **proposal, for decision.** Not a work order.

Companion to `AUDIT_pipeline_defects.md`, which lists the twelve findings this
design exists to make structurally impossible rather than individually fixable.

---

## 1. The observation

Twelve findings. Almost all of them are the same bug:

| finding | the absent thing | how it was represented |
|---|---|---|
| D-1 | instance type not extracted | `NULL` — and `NOT LIKE` drops it |
| D-3 | native cost of dropped rows | column omitted from an INSERT |
| D-8 | cache entries from a parallel job | silently overwritten |
| D-9 | billing period of a PDF bill | `''` — identical to "same month" |
| D-10 | length of the billing period | `30` — identical to a real 30-day month |
| D-11 | meaning of an unrecognised unit | `None` — which *disables* the safety check |
| D-12 | age of the price catalog | not recorded at all |

In every case **the absence is encoded as something indistinguishable from a
normal value**, so downstream code cannot branch on it and gates cannot catch
it. The pipeline has no way to say *"I do not know"*, and no way to say *"this
is from July"*.

Fixing twelve sites leaves the thirteenth. The fix is to make the absence
representable.

---

## 2. The idea in one line

> **A value in this pipeline is never a bare scalar. It is a value plus how it
> was obtained, how confident that makes it, and when it was true.**

Three existing pieces of this codebase already do it and are the proof it
works: `rate_source` on a rate, `passthrough_reason` on a passthrough, and
`decided_by` on a mapping row. Each turned "a number appeared" into "a number
appeared *because*". The spine generalises them into one mechanism rather than
three ad-hoc columns.

---

## 3. The envelope

Every derived fact carries four things. In DuckDB that is a column group, not
a struct — flat columns stay queryable and indexable:

```
  <field>                 the value
  <field>_source          who produced it        (enum)
  <field>_status          how much to trust it   (enum)
  <field>_asof            when it was true       (date, only where it decays)
```

### `status` — the five-state enum that does the work

| status | meaning | downstream rule |
|---|---|---|
| `measured` | read directly from the bill | trust |
| `derived` | computed from measured values by a stated rule | trust, record the rule |
| `referenced` | looked up in a table or catalog we maintain | trust, but `asof` applies |
| `defaulted` | a fallback fired because nothing else answered | **must emit a diagnostic with affected spend** |
| `unknown` | nothing answered | **may not be silently consumed** |

`defaulted` and `unknown` are the two the pipeline cannot express today, and
they are the two that every finding above needed.

### The rule that makes it bite

> **A check may not treat `unknown` as a pass.**

D-11 is the canonical violation: an unparsed unit returns `None`, the guard
sees `None` and skips, and a dimensional mismatch prices through. Under the
spine, `unit_class` returns `status=unknown` and the guard must fail or
escalate — never continue.

---

## 4. What each finding becomes

| finding | under the spine |
|---|---|
| **D-1** `NOT LIKE` drops NULLs | predicates are generated from the registry, which knows the column is nullable and emits the `COALESCE`. Hand-written SQL on a spine field is a lint failure. |
| **D-3** materiality row | the row is constructed through the same writer as every other row, so a field it does not set is `status=unknown`, not an absent column. The reconciliation assertion then has something to fail on. |
| **D-8** shared cache race | the cache is a `referenced` source with an `asof`; writes go through one audited helper that merges and `os.replace()`s. A second writer is a conflict, not a silent overwrite. |
| **D-9** blank `billing_month` | `''` is not expressible. The field is `measured` (CUR), `derived` (filename or a date column), or `unknown` — and `unknown` cannot be a `GROUP BY` key that silently merges periods. |
| **D-10** `billing_days = 30` | becomes `status=defaulted`, which obliges a diagnostic naming the spend sized by a guessed denominator. |
| **D-11** unit classes | the guard reads `status`; `unknown` fails. Separately the class model becomes a dimension vector (§6). |
| **D-12** catalog vintage | `asof` is already in the envelope. A rate priced from an 82-day-old catalog carries that date into the projection and into the report. |
| **D-2** scattered thresholds | not a provenance problem — see §6; listed so it is not assumed covered. |
| **D-7** no check on 71.5% of spend | the spine does not supply the check, but it supplies what the check needs: a `compute_breakdown` row whose vCPU is `defaulted` is exactly the population worth asserting over. |

Nine of twelve become structural. Three stay as their own work.

---

## 5. Where it lives, and what it costs

### Storage

Flat sibling columns on `aws_li_catalog` and `aws_li_to_gcp_li`, generated from
the field registry — not a JSON blob. Blobs are unqueryable, unindexable, and
invite typo'd keys; the registry already has to exist for
`DESIGN_field_registry_and_schema_mapping.md`, so the columns come free from
the same declaration.

Roughly 31 catalog fields → up to 93 extra columns. That is fine in a columnar
store where unread columns cost nothing, and only fields that can be absent or
can decay need the full set — `aws_li_key` needs none.

### Writing

One helper, mirroring `row_state.set_state()` which already exists:

```python
set_field(con, keys, "instance_vcpus", value=96,
          source="cur_column:product/vcpu", status="measured")
```

Direct `UPDATE ... SET <spine field>` becomes a lint failure, the same way
direct DDL became one after the row-state registry landed.

### Speed

No per-row Python. Status is assigned by the same set-based SQL that resolves
the value — a `CASE` over the resolver chain, in the statement that is already
running. The diagnostics rollup is one `GROUP BY status` per field at the end
of ingest.

### Honest cost

Three real ones, worth saying before anyone commits:

- **Schema churn.** Every field gains up to three columns. Migrations, wider
  `SELECT`s, more to read in a table dump.
- **Writer discipline.** The spine only holds if nothing bypasses `set_field`.
  That needs the lint test on day one, not later.
- **It makes things fail that used to pass.** `unknown` can no longer be
  consumed silently, so the first run after adoption will surface work that was
  previously invisible. That is the point, and it is also a real cost — budget
  for the backlog it exposes rather than being surprised by it.

---

## 6. What this deliberately does NOT solve

- **D-2, scattered thresholds.** A separate problem: one config file, read by
  both Python and Go, with an asserted ordering (report ≤ block ≤ clamp).
- **D-11's class model.** The spine fixes *unknown units pass the guard*. It
  does not fix *compound units are mis-classified*. That needs units modelled
  as dimension vectors (`GB-Second = {data:1, time:1}`) rather than labels —
  related work, different change.
- **D-7, no check over the dominant mechanic.** The spine supplies the
  population; someone still has to write the assertion.
- **Correctness.** A `measured` value read from a bill that is itself wrong is
  still wrong. The spine makes provenance legible; it does not make data true.

---

## 7. Migration

Each step is independently verifiable and none changes a number until step 4.

- **S1** — Add `status` and `source` to the fields that already have a
  precedent: `rate_source` → `rate_status`, `passthrough_reason` → status,
  `decided_by` → source. Pure rename-and-widen; proves the shape on code that
  already works.
- **S2** — Extend the field registry so every field declares whether it can be
  absent and whether it decays. Generate the columns. No behaviour change.
- **S3** — Route writes through `set_field`; add the lint. Still no behaviour
  change — this is the step that makes the rest hold.
- **S4** — Flip the consumers: `unknown` fails checks instead of skipping them;
  `defaulted` emits diagnostics. **This is where behaviour changes and where
  the hidden backlog appears.**
- **S5** — `asof` on catalog-derived values; vintage in the report (D-12).
- **S6** — Retire the ad-hoc columns the spine subsumes.

S1–S3 are worth doing even if S4 is deferred: they cost nothing in behaviour
and make the state of the pipeline legible for the first time.

---

## 8. Open choices

1. **Scope of S2** — all 31 catalog fields, or only the ~12 that feed pricing?
   (Recommend the 12. The rest can follow once the shape is proven.)
2. **`defaulted` severity** — diagnostic only, or should a defaulted value
   above some spend threshold block? (Recommend diagnostic only at first; the
   corpus will tell us the volume before we decide to block on it.)
3. **Report disclosure** — how much provenance belongs in the customer-facing
   report? Rate vintage is clearly fair. Per-row status probably is not.
   (Recommend: vintage and a single coverage line; everything else internal.)
