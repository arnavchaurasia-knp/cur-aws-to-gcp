# WORK ORDER — Equivalence crosswalk, provisional claims, and a review queue ranked by money

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`;
`internal/` and `cmd/` are from the repo root.

**Scope: the equivalence registry and its review workflow.** You are not
changing any mapper, any SKU choice, or any price. Claims authored here are not
yet consumed by the pipeline — that is a later work order. **Dollar totals must
not move.**

Background: `DESIGN_conversion_contracts.md`,
`DESIGN_conversion_open_questions.md` sections B and D.

---

## 1. What this builds, and why

The pipeline constantly asserts that an AWS thing is "like" a GCP thing —
Glacier Flexible like Coldline, an ACU like 2 vCPU, a Kinesis shard like
Pub/Sub throughput. Today those assertions live in mapper code and prose
comments. They cannot be listed, reviewed, or counted.

There will be roughly **350 of them**. Nobody reviews 350 claims on a schedule.
So this work order builds three things:

1. **A crosswalk file** — every claim written down once, in a standard shape.
2. **Provenance on each claim** — human-reviewed, or provisional (model-authored).
3. **A review queue ranked by money** — so attention goes to the claim carrying
   $400k, not claim #137 of 350.

---

## 2. The format: a crosswalk, not a lookup table

This is a known problem shape. Mapping values between two vocabularies where
equivalence is *partial* is what healthcare data integration calls a
**crosswalk** (ICD-9 to ICD-10, SNOMED to LOINC). Use its vocabulary rather
than inventing one.

`data/equivalences.json`:

```jsonc
{
  "_meta": {
    "format": "crosswalk",
    "degrees": ["equal","equivalent","wider","narrower","inexact","unmatched","disjoint"]
  },

  "storage_class": [
    {
      "aws": "standard", "gcp": "standard",
      "degree": "equal",
      "scope": ["storage_time"],
      "comment": "Same access profile, no minimum duration on either side.",
      "decided_by": "human", "decided_on": "2026-03-11"
    },
    {
      "aws": "glacier_flexible", "gcp": "coldline",
      "degree": "inexact",
      "scope": ["storage_time"],
      "comment": "Coldline's 90-day minimum matches Glacier Flexible's 90-day minimum; Archive's 365-day minimum does not. Retrieval pricing differs and is NOT covered by this claim.",
      "confidence_cap": 0.75,
      "decided_by": "human", "decided_on": "2026-03-11"
    },
    {
      "aws": "glacier_flexible", "gcp": "standard",
      "degree": "disjoint",
      "scope": ["storage_time"],
      "comment": "Explicitly not equivalent. Asserting this prevents the 120x over-projection seen historically.",
      "decided_by": "human", "decided_on": "2026-03-11"
    }
  ]
}
```

### The seven degrees

| degree | meaning |
|---|---|
| `equal` | the same concept |
| `equivalent` | functionally the same for this scope |
| `wider` | the GCP target covers more than the AWS source |
| `narrower` | the target covers less |
| `inexact` | close, with a stated difference — **requires `comment` and `confidence_cap`** |
| `unmatched` | no GCP equivalent exists — routes to passthrough |
| `disjoint` | explicitly NOT the same — recording a known-wrong pairing |

### Three rules the loader must enforce

1. **`scope` is required and non-empty.** A claim with no scope applies to
   nothing. This is what stops a storage-time claim being reused for a
   retrieval charge.
2. **`inexact` requires `comment` and `confidence_cap`.** A caveat that is not
   written down is not a caveat.
3. **`disjoint` and `unmatched` are valid, useful answers.** Do not treat an
   absent claim as "probably fine" — absent means unknown, and unknown is not a
   pass.

---

## 3. Tasks

### T1 — The file and its loader

Create `data/equivalences.json` and `scripts/equivalences.py`:

```python
lookup(attribute, aws_value, gcp_value, scope) -> Claim | None
is_provisional(claim) -> bool            # decided_by != "human"
confidence_cap(claim) -> float | None
all_claims() -> list[Claim]
```

`lookup` returns `None` when no claim covers that (attribute, pair, scope).
**`None` is "unknown", not "allowed".** Callers must treat it as a failure.

Validate on load and fail loudly: unknown degree, missing scope, `inexact`
without comment or cap, duplicate (attribute, aws, gcp, scope) with conflicting
degrees.

**Seed it with the storage-class family only** — the six AWS classes against
the four GCP classes, as far as you can justify each. Leave every other
attribute family empty. One worked family proves the format; a half-guessed
registry is worse than an empty one.

### T2 — Exposure tracking (read this carefully — the obvious approach fails)

The review queue ranks claims by the money that depends on them. That needs
data **across jobs**.

**Job directories are ephemeral on Render** (`/data` has no persistent disk;
see `DESIGN_state_durability_on_render.md`). Computing exposure by scanning job
directories will silently under-count after any redeploy, and will read as "low
exposure" rather than "data missing" — the exact failure class this codebase
keeps producing.

So exposure goes in the **durable application database**
(`internal/db/`, Turso-backed). There is precedent: `admin_audit` already lives
there.

Add a table:

```sql
CREATE TABLE IF NOT EXISTS equivalence_usage (
  job_id       TEXT NOT NULL,
  attribute    TEXT NOT NULL,
  aws_value    TEXT NOT NULL,
  gcp_value    TEXT NOT NULL,
  scope        TEXT NOT NULL,
  rows_used    INTEGER NOT NULL,
  usd_exposed  REAL NOT NULL,
  recorded_at  TEXT NOT NULL
);
```

Write one row per distinct claim used, **at job completion**. Keep it a plain
insert — no aggregation at write time.

**If the durable database is unconfigured**, record that fact rather than
silently writing nowhere: log it and emit a diagnostic. An empty queue must
never be indistinguishable from "no claims used".

### T3 — The review queue report

`scripts/equivalence_review.py`:

```
PROVISIONAL CLAIMS, BY SPEND EXPOSURE

  attribute       aws -> gcp                  degree     bills      usd    decided_by
  storage_class   glacier_flexible -> coldline inexact      47  412,000   llm
  acu             acu -> vcpu_ram              inexact      12   88,000   llm
  shard           shard -> pubsub_throughput   equivalent    2    3,100   llm

REVIEWED CLAIMS: 14        PROVISIONAL: 3        UNKNOWN PAIRS HIT: 7
```

Also list **unknown pairs that were hit** — (attribute, aws, gcp) combinations
the pipeline encountered with no claim at all, ranked by dollars. That list is
the authoring backlog, and it is more valuable than the provisional list
because those are the gaps, not the guesses.

### T4 — Promotion is an explicit act

`scripts/promote_equivalence.py`, mirroring `promote_learned_codes.py`:

- dry run by default; `--apply` writes
- promoting flips `decided_by` to `human` and stamps `decided_on`
- **a promotion must carry a reviewer identity and a comment** — promoting
  without saying who and why is not review, it is rubber-stamping
- atomic write (temp file + `os.replace`)
- never auto-promote

### T5 — Tests

`tests/test_equivalences.py`:

1. `test_loader_rejects_malformed` — missing scope, unknown degree, `inexact`
   without comment or cap each raise.
2. `test_lookup_is_scoped` — a claim scoped to `storage_time` does not match a
   `retrieval` lookup.
3. `test_unknown_is_not_allowed` — `lookup()` for an unlisted pair returns
   `None`, and the helper that callers use treats `None` as failure.
4. `test_disjoint_is_a_real_answer` — the `glacier_flexible -> standard`
   disjoint claim is found and reported as explicitly not equivalent.
5. `test_conflicting_claims_rejected` — two claims for the same
   (attribute, aws, gcp, scope) with different degrees fail the load.

---

## 4. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
go build ./... && go test ./internal/db/
python -m py_compile scripts/equivalences.py scripts/equivalence_review.py scripts/promote_equivalence.py
```

Then:

- `python scripts/equivalence_review.py` runs on an empty database and prints
  an empty queue **with a line stating the database was empty** — not a bare
  empty table.
- Load the storage-class seed and confirm every claim validates.
- **Dollar totals unchanged** on an existing job DB — nothing here is consumed
  by pricing yet.

---

## 5. Do NOT

- Do **not** wire the crosswalk into any mapper or into `resolve_sku`. That is
  a separate work order; this one only authors and reviews claims.
- Do **not** seed attribute families beyond storage class. Half-guessed claims
  are worse than absent ones.
- Do **not** treat a missing claim as permission. Absent means unknown.
- Do **not** compute exposure by scanning job directories — see T2.
- Do **not** auto-promote provisional claims, under any threshold.
- Do **not** change mappers, rates, or `unit_multiplier`.

---

## 6. Done

- [ ] `data/equivalences.json` exists in crosswalk form, seeded with the
      storage-class family only.
- [ ] Loader validates and fails loudly; `lookup()` returns `None` for unknown
      and callers treat that as failure.
- [ ] `equivalence_usage` table exists in the durable database, written at job
      completion, with a diagnostic when the database is unconfigured.
- [ ] Review report ranks provisional claims **and** unknown-pairs-hit by
      dollars.
- [ ] Promotion requires a reviewer and a comment; atomic; never automatic.
- [ ] 5 tests pass; full suite passes; dollar totals unchanged.
