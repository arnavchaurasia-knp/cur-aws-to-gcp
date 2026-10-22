# DESIGN — Resolving the four open questions on conversion contracts

Companion to `DESIGN_conversion_contracts.md`. Answers A–D, plus the admin
route for assumptions.

---

## A — The attribute vocabulary: enumerate, don't invent

**Confirmed: both clouds publish their attributes.** This is the same move as
product codes and units — fetch the vocabulary from the authority rather than
discovering it one bug at a time.

**GCP** (`data/catalog.duckdb`, already present):

| column | values |
|---|---|
| `resource_family` | Storage, Compute, License, ApplicationServices, Network |
| `resource_group` | hundreds — `IpAddress`, `Kubernetes`, `SQLServer2016Enterprise`, `RedHat`, … |
| `usage_type` | OnDemand, Preemptible, Commit1Mo, Commit1Yr, Commit3Yr, Commit, CmtCudPremium |

**AWS** (Price List offer files, same source as `refresh_aws_product_codes.py`):
each product carries an `attributes` dict — `instanceType`, `operatingSystem`,
`tenancy`, `volumeType`, `storageClass`, `databaseEngine`,
`deploymentOption`, and so on, varying by service.

### So the work splits cleanly

1. **Generated** — a script enumerates the distinct attribute *keys and values*
   per service from both sides. Nobody guesses what exists.
2. **Human** — only the *equivalence* between an AWS value and a GCP value.
   That is the irreducible judgement, and it is now a bounded list instead of
   an open question.

### One attribute to get right immediately

`usage_type` is the commitment model. An OnDemand AWS row matched to a
`Commit3Yr` GCP SKU is a 50–60% understatement with **identical dimensions and
identical resource group** — a Glacier-class bug hiding in plain sight.

It is currently handled, but in SQL inside `projection_view.py`
(`pricing_type = 'OnDemand'` for the headline, `Commit1Yr`/`Commit3Yr` for the
CUD columns). **Keep that where it is** — do not move commitment selection into
contracts. But *do* add it as an asserted attribute so a mapper cannot pick a
committed SKU for the on-demand headline by accident.

---

## B — "Equivalent" is not boolean

### The problem

Glacier Flexible and Coldline share a 90-day minimum storage duration, so for
**storage-time** they are a fair match. But their **retrieval** pricing differs
materially. A single `glacier_flexible ≈ coldline` claim is true for one charge
type and misleading for another.

### The fix: equivalence is scoped and three-valued

```jsonc
{
  "attribute": "storage_class",
  "aws": "glacier_flexible",
  "gcp": "coldline",
  "verdict": "equivalent_with_caveat",     // equivalent | equivalent_with_caveat | not_equivalent
  "scope": ["storage_time"],               // which charge types this claim covers
  "caveat": "Retrieval pricing differs: Glacier Flexible charges per-GB retrieval plus a request fee; Coldline charges a flat per-GB retrieval. Retrieval charges must be mapped separately, not folded in.",
  "confidence_cap": 0.75,
  "decided_by": "human", "decided_on": "2026-03-11"
}
```

Three rules make this bite:

1. **Scope is required.** A claim with no `scope` applies to nothing. You
   cannot accidentally reuse a storage-time claim for a retrieval charge.
2. **`equivalent_with_caveat` forces an assumption record** on every row that
   uses it — the caveat text becomes the assumption note, and the confidence
   cap applies automatically. A caveat cannot be silently treated as clean.
3. **`not_equivalent` is a first-class answer.** Declaring "these are not the
   same" is useful: it routes the charge to passthrough with a stated reason
   instead of leaving a gap someone later fills with a wrong guess.

### Why scoping is the important half

Without it you get one claim per attribute pair and endless argument about
whether it is "true". With it you get several small, defensible claims:
`glacier_flexible ≈ coldline` *for storage-time*, and separately
`not_equivalent` *for retrieval*. Each is checkable on its own.

---

## C — Magnitude thresholds: consolidate first, then narrow per contract

### The problem

Five different over-projection thresholds across four files and two languages
(audit finding D-2): 1.3x, 1.5x, 2.0x, 3.0x, 50x, with floors of $20, $50 and
$100. Two read `review-config.json`; three are hardcoded, one of those in Go.

Adding `magnitude_plausible` to contracts without fixing this gives you a
sixth.

### The fix, in two steps

**Step 1 — one ladder, one file, asserted order.**

```jsonc
"over_projection": {
  "report_ratio": 1.5,    // surfaced in outlier triage
  "review_ratio": 2.0,    // flagged for the review phase
  "block_ratio":  3.0,    // fails the phase gate
  "clamp_ratio": 50.0,    // forcibly reduced to passthrough
  "cost_floor_usd": 20
}
```

A test asserts `report ≤ review ≤ block ≤ clamp`. The ladder becomes something
you can state in one sentence instead of reconstructing from four files. The Go
gate reads this file, or — better — the Go `CheckSQL` is deleted and a Python
gate runner owns it, which also removes the duplicated SQL.

**Step 2 — let contracts narrow the band, never widen it.**

A global 3x band is meaningless: a storage row and a compute row have entirely
different expected ratios. Once a contract declares its factor, the expected
GCP/AWS ratio is roughly known, so a charge type may declare a tighter band:

```jsonc
"magnitude": { "expected_ratio": [0.7, 1.3] }
```

**Rule: a contract may tighten the global ladder, never loosen it.** That
prevents the band becoming an escape hatch for a mapper that produces
implausible numbers.

This also makes the band *earn* its value. A tight band on S3 storage, where
GCP and AWS prices are close, catches the Glacier bug that a 3x global band
would never see.

---

## D — Who reviews equivalence claims: let dollars decide, not a calendar

### The problem

"A human decides" is not a process. Periodic review of 350 contracts will not
happen, and a review board is overhead you do not have.

### The fix: provisional claims, ranked by exposure

**Every claim carries `decided_by` and `confidence`.** Three tiers:

| `decided_by` | status | effect |
|---|---|---|
| `human` | reviewed | full confidence allowed |
| `llm` | **provisional** | works, but caps row confidence and enters the queue |
| *(absent)* | missing | the assertion fails; charge falls to passthrough |

**A provisional claim is usable.** It does not block a projection — it produces
one with a capped confidence and an entry in a review queue.

**The queue is ranked by money.** A report, generated across job runs:

```
provisional equivalence claims, by spend exposure
  glacier_flexible ~ coldline        $412,000   47 bills   decided_by=llm
  acu ~ vcpu+ram                      $88,000   12 bills   decided_by=llm
  shard ~ pubsub_throughput            $3,100    2 bills   decided_by=llm
```

Review is then triggered by **exposure**, not by a schedule: look at the claim
carrying $412k, not at claim #137 of 350. A claim nobody's bills exercise never
needs reviewing, which is correct.

**Promotion is explicit** — the same propose-verify-promote pattern as learned
product codes and schema mappings. One mechanism, three uses.

---

## E — The admin route for assumptions

Your plan: list every assumption in an admin UI, allow editing the ratios,
changes apply to subsequent runs. **This is the right idea** — it turns
assumptions from buried constants into a visible, tunable surface, and you
already have `/api/admin/*` and `ADMIN_EMAILS` to build on.

Four things to get right, or it becomes a way to quietly change customer
numbers.

### E1 — Version the assumption set; stamp it on every job

Without this, a customer who saw $340k last week sees $380k today and nobody
can explain why. With it:

- the assumption set has a version (a hash or an incrementing id)
- every job records which version it used
- a re-run reproduces the original number, or states plainly that assumptions
  changed between runs

**This is non-negotiable.** An admin UI that silently rewrites history is worse
than hardcoded constants, because at least a constant required a deploy.

### E2 — Global default *plus* per-customer override

Your description says changes apply globally. Some assumptions are genuinely
global (ACU = 0.25 vCPU + 2.0 GB is a property of Aurora). Others are
**customer properties**: NAT VMs per gateway, Cognito requests per MAU.

Tuning a customer-specific assumption globally breaks every other customer. So:

```
assumption.scope = "global" | "per_customer"
```

Per-customer ones get a default and a per-prospect override. The projection
then becomes *theirs* rather than generic, which is also a better product.

### E3 — Declared bounds, enforced

A free-text number field is a foot-gun. Each assumption declares `min`/`max`
and the UI refuses values outside it. "NAT VMs per gateway: 1–256", not "any
float".

### E4 — An audit trail

Who changed what, from what to what, when, and why — because an assumption
change moves money. One row per change, shown in the UI next to the current
value.

### The version worth building toward

Once assumptions are bounded and versioned, the admin route stops being a
settings page and becomes a **sensitivity tool**:

> "With NAT VMs per gateway between 8 and 32, this projection is $340k–$410k."

That is a materially better answer than a single confident number, and it is
the honest one. It also directly answers the question a customer will ask:
*"how much of this depends on things you guessed?"*

---

## Suggested order

1. **A** — generate the attribute vocabulary from both price lists. Mechanical,
   unblocks everything else.
2. **C step 1** — consolidate the five thresholds. Do it before contracts add a
   sixth.
3. **B** — scoped, three-valued equivalence. Needed before the first contract
   with a tier attribute.
4. **E1 + E3** — versioning and bounds. Build these *with* the admin route, not
   after; retrofitting versioning once numbers have moved is painful.
5. **D** — the provisional queue. Only meaningful once claims exist.
6. **E2, E4** — per-customer scope and audit trail, as the admin route matures.
