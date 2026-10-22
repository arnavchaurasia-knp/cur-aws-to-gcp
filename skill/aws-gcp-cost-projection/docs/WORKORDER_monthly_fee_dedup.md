# WORK ORDER — Per-month flat-fee dedup: the missing month dimension

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: two mappers and one new helper.** You are not changing ingest, not
changing the dedup key, and not changing any SKU or rate. Expect dollar totals
to **rise** on multi-month bills that contain AWS WAF Enterprise-feature fees,
and to stay identical on every other bill.

---

## 1. The problem in plain English

Some AWS charges are **flat monthly subscriptions**. A bill covering three
months carries that fee three times — once per month. GCP charges its
equivalent the same way.

A bill can also list the *same* monthly fee on several lines within one month
(multiple accounts, multiple rule groups). Pricing every line at the full
monthly rate would multiply one real charge. So both mappers below "price it
once and zero the rest".

**The bug: one of them zeroes the rest across the whole bill, with no notion of
month at all.** On a 12-month bill it prices one month and zeroes eleven.

### The two sites

| site | keyed on | multi-month behaviour |
|---|---|---|
| `map_shield`, `scripts/apply_static_mappings.py:4321` | `enterprise_sub_priced_months` — a **set** of billing months | correct when `billing_month` is populated |
| `map_flat_hourly`, `scripts/apply_static_mappings.py:2476` | `enterprise_enrollment_priced` — a bare **bool** | **always wrong on multi-month** |

`map_shield` got this right. `map_flat_hourly` did not, and it is the same
shape of charge — Cloud Armor Enterprise Paygo enrollment, $200/mo flat.

**Worked example.** A 12-month CUR with correct `billing_month` values, one WAF
Bot Control fee row per month:

- today: the first row is priced, the other eleven are zeroed → **$200**
- correct: each month's row is priced once → **$2,400**

A **$2,200 understatement**, in the direction that over-promises savings. It
does not require any data defect — a perfectly clean CUR produces it.

### The second, smaller problem: the blank month

When a bill is a PDF or XLSX with no usable date column, ingest writes
`billing_month = ''` — the empty string, not NULL (`scripts/ingest.py:1119`).
On the 12-bill corpus that is **928 of 1,477 rows (63%)**.

The empty string is a *value*, and every blank row shares it. So `map_shield`'s
otherwise-correct month set collapses: `r.get("billing_month") or "default"`
(`scripts/apply_static_mappings.py:4329`) puts every month in one bucket, and
Shield Advanced ($3,000/mo) is priced once for the whole bill.

**This is latent, not observed.** No bill in the corpus is both multi-month and
blank-month, and no corpus bill contains a Shield row at all. Treat it as a
known trigger, not a measured loss — and fix it by making it *visible*, not by
guessing a month count.

### Why you must not "just use NULL instead of empty string"

The dedup key is an **md5 over concatenated strings** that includes
`billing_month` (`scripts/ingest.py:1133`). In SQL, `NULL` concatenated with
anything is `NULL`, so a NULL month would make the whole key NULL for every
affected row — collapsing the entire catalog into one row and destroying the
bill. **Do not change the sentinel.** Fix the consumers.

> §7 records what established practice says about all of this — subscription
> billing's cycle-scoped idempotency key, FOCUS's mandatory billing period, and
> Kimball's rule for unknown members. Read it if you want the reasoning behind
> the tasks; the tasks themselves are self-contained without it.

---

## 2. Tasks

### T1 — One definition of "how many months does this bill cover"

Today `scripts/apply_rates.py:686` computes this in SQL and nothing else does.
Add a Python helper in `scripts/apply_static_mappings.py`, near the other
module-level helpers:

```python
def months_covered(rows):
    """How many distinct billing months this bill spans.

    Returns (count, known). `known` is False when no row carries a usable
    billing month -- ingest writes '' for bills with no date column, and ''
    is a single shared value, not a month. Callers must not treat an unknown
    month count as 1 real month without saying so.
    """
    months = {m for m in (r.get("billing_month") for r in rows) if m}
    if not months:
        return 1, False
    return len(months), True
```

`known=False` must never be silently equivalent to `known=True, count=1`. That
distinction is the whole point — see §4.

### T2 — Give `map_flat_hourly` the month dimension

In `map_flat_hourly` (`scripts/apply_static_mappings.py:2476`):

- replace `enterprise_enrollment_priced = False` with
  `enterprise_enrollment_priced_months = set()`
- replace `if not enterprise_enrollment_priced:` with a month lookup, exactly
  mirroring `map_shield`:

```python
billing_month = r.get("billing_month") or "default"
...
if billing_month not in enterprise_enrollment_priced_months:
    ...
    if sku_id:
        enterprise_enrollment_priced_months.add(billing_month)
else:
    # zeroed branch, unchanged except for the note
```

- in the zeroed branch's note, name the month it was priced for, the way
  `map_shield` already does at line 4399 — `"... already priced for month
  {billing_month} -> $0 dedup"`.

**Read `map_shield` lines 4321–4402 first and copy its shape.** Do not invent a
different one; the goal is that these two sites stop differing.

### T3 — Say so when the month is unknown

Both mappers now key on `billing_month or "default"`. When the month is
unknown, `"default"` is a real bucket and the dedup still collapses — that is
unavoidable without a date, and **that is acceptable as long as it is stated**.

In each of the two mappers, when a flat monthly fee is priced **and**
`months_covered(rows)` returns `known=False`, append to that row's
`projection_note`:

```
 [month unknown: this bill carries no billing period, so a monthly fee is
 priced once; if the bill spans multiple months this is understated]
```

Keep the wording neutral and factual — it is customer-visible. Do not change
`strategy`, `unit_multiplier`, or any dollar amount. This task adds a
sentence, nothing else.

This is the "surface the offending rows" half of the unknown-member practice in
§7.3 — naming the condition, not repairing it.

### T4 — Tests

Add to `tests/test_static_mappers.py`:

1. `test_waf_enrollment_priced_once_per_month` — three rows with
   `billing_month` `2026-01`, `2026-02`, `2026-03` produce **three** priced
   enrollment rows, not one.
2. `test_waf_enrollment_deduped_within_a_month` — three rows all `2026-01`
   produce **one** priced row and two zeroed.
3. `test_shield_priced_once_per_month` — the same two cases for `map_shield`,
   pinning the behaviour that is already correct so it cannot regress.
4. `test_blank_month_fee_carries_unknown_note` — rows with `billing_month=''`
   produce a priced row whose note contains `month unknown`.
5. `test_months_covered_distinguishes_unknown` — `months_covered([])` and rows
   with only `''` return `(1, False)`; rows with two real months return
   `(2, True)`.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_static_mappings.py
```

- Full suite passes. **Record the count before you change anything** and confirm
  it rises by exactly 5. At the time of writing the baseline was 1,024, but it
  drifts as other work lands — your own measured baseline is the authority, not
  this number.
- On a single-month bill, **every dollar total is unchanged.** If any moves,
  you changed dedup behaviour within a month — that is a bug, not progress.
- On a synthetic multi-month bill with WAF Enterprise fee rows, the WAF
  enrollment total is `$200 x months`, not `$200`.

---

## 4. Do NOT

- Do **not** change `billing_month` from `''` to `NULL` anywhere. It feeds an
  md5 string concatenation; NULL would null the dedup key for every affected
  row and collapse the catalog. See §1.
- Do **not** try to infer a month count from row counts, usage hours, or
  `billing_days`. `billing_days` falls back to a bare `return 30`
  (`scripts/ingest.py:1829`) and **is currently read by no script** — it is not
  a source of truth.
- Do **not** multiply a monthly fee by `months_covered` as a substitute for the
  per-month dedup. On a bill with real months, each month already has its own
  row; multiplying would double-count. The dedup is the correct mechanism — it
  was only missing its key.
- Do **not** change `map_shield`'s pricing logic. Its month handling is already
  right; you are pinning it with tests and adding the unknown-month note.
- Do **not** attempt to recover the billing period from undated PDF/XLSX bills.
  That is real work with its own design questions and is **explicitly out of
  scope** — see §5.

---

## 5. Out of scope, deliberately

**Recovering the billing period from bills that have no date column.** That is
the only thing that would make the blank-month case fully correct rather than
merely visible. It needs its own investigation: which bill formats state a
period, where, and how reliably. Do not start it here.

**`billing_days`.** The `return 30` fallback is a fabricated default, but the
column is read by nothing, so it moves no money today. Leave it; note it in
your summary if you touch that file.

---

## 6. Done

- [ ] `months_covered()` exists and distinguishes unknown from one month.
- [ ] `map_flat_hourly` keys its enrollment dedup on billing month, mirroring
      `map_shield`.
- [ ] Both mappers state in the note when the month is unknown.
- [ ] 5 new tests pass; full suite passes at your recorded baseline + 5.
- [ ] Single-month dollar totals unchanged; multi-month WAF enrollment scales
      with months.
- [ ] Summary states what moved, and lists anything you noticed but did not fix.

---

## 7. How the industry handles this

Three established practices bear on this work order. Each one changes something
concrete above; none is here as decoration.

### 7.1 A recurring charge is deduplicated by a key scoped to its billing cycle

This is the subscription-billing idempotency pattern. A recurring charge is
applied once per cycle, and the guard against applying it twice is a key that
**includes the cycle** — not a flag meaning "already done". Shopify's billing
API is a plain example: a billing attempt carries a client-generated
idempotency key to prevent duplicate payments, and it carries a *billing cycle
selector* that defaults to the current cycle. The key and the cycle travel
together, because a key without a cycle would suppress every future cycle too.

**That is exactly the defect in T2.** `enterprise_enrollment_priced` is a key
with no cycle component, so it suppresses all later months. `map_shield`'s set
of billing months is the same pattern done correctly. The useful way to read
this work order: *the dedup is a cycle-scoped idempotency key, and one of the
two sites lost its cycle.*

### 7.2 The billing period is mandatory and non-null — our blank is a data defect

FOCUS, the FinOps Foundation's open billing-data specification, makes
`BillingPeriodStart` a **required column that cannot contain nulls**. Every
charge record states the period it belongs to; it is not optional, and it is
not allowed to be absent. FOCUS also carries `ChargeFrequency` (One-Time,
Recurring, Usage-Based) as an explicit dimension, so a recurring charge is
*labelled* as recurring rather than inferred from its description — though as
of v1.4 that column is Recommended rather than Required.

Two consequences for us:

- A bill with no billing period is a **defective input**, not a shape to be
  accommodated. That is why §5 scopes period-recovery out as its own
  investigation rather than letting the implementer paper over it here.
- Our `is_sub` detection sniffs words like `monthly`, `subscription` and `fee`
  out of free text (`apply_static_mappings.py:4365`). FOCUS's answer is a
  declared frequency field. **Do not build that now** — it is noted so the
  direction is on record, not as a task.

### 7.3 Unknown must be a distinct, named member — and the rows must be surfaced

Kimball's guidance on nulls in a dimensional model is that a fact row's
dimension key is never null; the dimension carries a **default row** standing
for the unknown case. The part usually forgotten is the rest of it: Kimball
prescribes *different* special rows for different reasons — "Not applicable"
where the dimension genuinely does not apply, versus "Missing key" where the
source failed to supply one — and says to **troubleshoot the offending rows**
rather than just bucket them.

(The familiar `-1` surrogate key is community convention, not Kimball's; any
sentinel works provided it is a real, named member.)

Our `billing_month = ''` is a default member, so the shape is right. What is
missing is the second half: it is unnamed, it conflates reasons, and nothing
surfaces the affected rows. **T3 is that second half** — it does not invent a
month, it names the condition on the row so the operator can see it. Keep T3
scoped to naming and surfacing. Do not let it start guessing a month count.

### What this does not justify

None of the above argues for changing the sentinel to NULL, for inferring a
month count, or for multiplying fees by a month total. Those remain forbidden
in §4. FOCUS says the period should have been present; it does not say to
fabricate one when it is not.

**Sources:**
[FOCUS BillingPeriodStart (v1.2)](https://focus.finops.org/docs/specification/v1-2/columns/billing-period-start/) ·
[FOCUS BillingPeriodStart (v1.1)](https://focus.finops.org/docs/specification/v1-1/columns/cost-and-usage/billing-period-start/) ·
[FOCUS ChargeFrequency (v1.4)](https://focus.finops.org/docs/specification/v1-4/columns/cost-and-usage/charge-frequency/) ·
[Flexera FOCUS column mapping](https://docs.flexera.com/flexera-one/administration/cloud-settings/bill-data-connections/bill-connect-configurations/finops-open-cost-and-usage-specification-focus/mapping-focus-columns-to-flexera-one-dimensions) ·
[Microsoft FOCUS mapping guide](https://learn.microsoft.com/cloud-computing/finops/focus/mapping) ·
[Kimball Design Tip #43 — Dealing with Nulls](https://www.kimballgroup.com/2003/02/design-tip-43-dealing-with-nulls-in-the-dimensional-model/) ·
[Kimball — Fact table null handling](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/fact-table-null/) ·
[Shopify SubscriptionBillingAttempt](https://shopify.dev/docs/api/admin-graphql/latest/objects/SubscriptionBillingAttempt)
