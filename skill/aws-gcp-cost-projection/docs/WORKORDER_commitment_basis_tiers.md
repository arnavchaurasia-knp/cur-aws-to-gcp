# WORK ORDER — Estimate the AWS on-demand price for discounted rows from a disclosed discount rate

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: row classification, one derived cost column, four new assumptions, and
report presentation.** No mapper or SKU changes. The **GCP** totals must not
move at all. The AWS headline total **will rise**, because discounted rows are
restated at estimated list price — that is the point of the work order.

---

# STATUS: IMPLEMENTED — one correction required before this is done

Sections 1–7 below were implemented and verified. Do **not** redo them. A
review against the 11-bill corpus found one regression, specified in **§0**
immediately below. **Start there; it is the only outstanding work.**

What was verified as correct and must not be disturbed:

- all four discount assumptions declared in `data/assumption-defaults.json`
  **and** the Go seed, with bounds;
- `aws_cost_od_est` in its own column, `aws_amortized_cost` untouched;
- divisor guards against a discount of 1.0 or more;
- unknown commitment term defaults to `committed_1yr` (the smaller discount);
- disclosure notes naming the assumed percentage;
- the report reads both billed and estimated figures, with a fallback when the
  column is absent.

Measured effect, which is correct and should stay: **GCP totals moved by
exactly $0.00**; `committed_1yr` went 1.45x → **0.90x** and `spot` 10.80x →
**3.24x**, both toward the on-demand band of 0.81x.

---

## 0. CORRECTION — a commitment *fee* is not discounted *usage*

### What goes wrong

Three rows in the corpus are **Savings Plan commitment charges** — the hourly
fee you pay *for* the commitment:

```
product   = Savings Plans for Compute usage
usage_type= Compute Savings Plans
operation = 3yr No Upfront : CSP : $5.0 Hourly commitment
pricing_model = OnDemand      line_item_type = Usage
billed $3,720  ->  estimated $7,750  ->  GCP $0.00
```

The classifier sees "savings plan" and "3yr" and divides by `(1 - 0.52)`.
Across the three rows: **billed $4,836 → estimated $10,075.**

**That invents $5,239 of AWS cost that does not exist**, and it inflates the
AWS side, which makes GCP look cheaper — the direction that over-promises
savings to a customer.

**This was a gap in the original specification, not an implementation mistake.**
§1–§7 describe *discounted usage* rows, where grossing up to list price is
meaningful. They never said what to do with the commitment charge itself, and a
commitment fee **has no on-demand equivalent to gross up to** — it is already
the price of the discount.

### What the industry does — and why this is a named failure mode

This is not a judgement call. **FOCUS**, the FinOps Foundation's billing-data
specification, separates the two row types explicitly:

| row | FOCUS classification |
|---|---|
| the commitment purchase (our SP hourly fee) | `ChargeCategory = "Purchase"` |
| consumption that got the discounted rate | `ChargeCategory = "Usage"`, `CommitmentDiscountStatus = "Used"` |
| commitment capacity bought and not consumed | `ChargeCategory = "Usage"`, `CommitmentDiscountStatus = "Unused"` |

And it states the aggregation rule directly: when aggregating, **exclude
`Purchase` rows** — or, if you want the purchases, exclude the `Usage` rows —
*"to prevent double counting"*. The amortization identity is that the effective
cost of all `Used` and `Unused` rows sums to the commitment's billed cost over
its term.

So a commitment fee and the usage it discounts are **two views of the same
money**. Counting both is double counting; grossing the fee up to a notional
list price, as the current code does, is a category error on top of that —
there is no list price for a purchase.

**Our pipeline has no equivalent of `ChargeCategory`.** That is the structural
reason a Purchase row could be fed through logic written for Usage rows. C1 and
C2 below are the minimum fix; adopting the distinction properly would mean
classifying every row as purchase-or-usage at ingest, which is a larger change
and not in scope here.

**Noted and deliberately not fixed here:** we do not model
`CommitmentDiscountStatus = "Unused"` at all. Unused commitment is money the
customer pays for capacity they never consumed, and it is the one category that
emphatically does **not** transfer to GCP — arguably the strongest argument in
a migration report. We currently cannot see it. Worth its own work order.

Sources: [FOCUS v1.2 discount handling](https://focus.finops.org/docs/specification/v1-2/attributes/discount-handling/) ·
[FOCUS v1.4 CommitmentDiscountQuantity](https://focus.finops.org/docs/specification/v1-4/columns/cost-and-usage/commitment-discount-quantity/) ·
[FOCUS v1.1 commitment-discount appendix](https://focus.finops.org/docs/specification/v1-1/sections/appendix/commitment-discounts/)

### C1 — Never apply a divisor to a commitment charge

A row is a commitment *charge* (not discounted usage) when it is the fee for
holding a commitment. Signals, in order of reliability:

- `line_item_type` in (`RIFee`, `SavingsPlanRecurringFee`) — the CUR case,
  already handled upstream;
- in non-CUR formats, where `line_item_type` is `Usage` and `pricing_model` is
  `OnDemand`, the **product / operation text**: a product of
  `Savings Plans for ... usage` combined with an operation naming an hourly
  commitment (`... : CSP : $5.0 Hourly commitment`), or `reserved instance fee`
  / `recurring fee` wording.

For such rows `aws_cost_od_est` **must equal the billed cost**. No divisor, no
estimate, no disclosure note claiming one.

Match on the *commitment-fee* shape, not merely on the words "savings plan" —
a discounted **usage** row also mentions Savings Plans and must keep its
divisor.

### C2 — Commitment charges are not workload

These rows should not be in the comparison baseline at all. On a CUR they are
`SavingsPlanRecurringFee` and the allow-list that already landed excludes them.
In this bill they arrive as `line_item_type='Usage'`, so only the product and
operation text reveal what they are — and they project GCP $0, which is
correct, because there is nothing on GCP to compare them against.

Set `is_workload = FALSE` for them in the non-CUR ingest branches, using the
same text signals as C1. They stay in the catalog and still reconcile to the
bill total; they simply leave the workload baseline, exactly as the
`SavingsPlanRecurringFee` rows did.

Expected effect on the corpus: AWS workload falls by the **billed** $4,836 (not
the inflated $10,075), and the `committed_3yr` bucket — currently 3 rows,
$10,075 estimated against **$0.00 of GCP** — disappears.

### C3 — Tests

1. `test_commitment_fee_not_grossed_up` — a row whose operation reads
   `3yr No Upfront : CSP : $5.0 Hourly commitment` has
   `aws_cost_od_est == aws_amortized_cost`.
2. `test_commitment_fee_not_workload` — the same row is `is_workload = FALSE`.
3. `test_discounted_usage_still_grossed_up` — a *usage* row covered by a
   Savings Plan keeps its divisor. **This is the guard against over-matching**:
   C1 must not disable the estimate for the rows it was built for.
4. `test_no_basis_bucket_has_zero_gcp` — no `commitment_basis` bucket holds
   material AWS cost against $0.00 of GCP. That pattern is what exposed this
   bug, and it is worth keeping as a standing check.

### C4 — Do NOT

- Do **not** apply the divisor to any row with no GCP counterpart. If a bucket
  shows material AWS against $0 GCP, the classification is wrong.
- Do **not** widen C1 to every row mentioning "savings plan" — discounted usage
  rows mention it too and must keep their divisor (C3.3).
- Do **not** remove these rows from the catalog. They must still reconcile to
  the bill total; only `is_workload` changes.
- Do **not** revisit §1–§7. They are implemented and verified.

### C5 — Verification

- Full suite passes at your recorded baseline + 4.
- **GCP totals unchanged**, still exactly $0.00 of movement.
- AWS workload falls by $4,836 on the reference bill; no `committed_3yr`
  bucket remains with $0 GCP.
- `committed_1yr` stays at ~0.90x and `spot` at ~3.24x — if either moves, C1
  over-matched and disabled an estimate that should have applied.

---

## 1. The problem in plain English

The product compares **AWS on-demand against GCP on-demand**. For most rows
that is right. For rows where AWS was already discounted — Spot, reserved
instances, Savings Plans — only one side carries a discount, so the comparison
is meaningless.

Measured across the corpus by the AWS pricing model of each row:

```
pricing_model   rows        aws $        gcp $    ratio
OnDemand         822      128,584       99,546    0.77x   <- honest
Committed          2        2,946        4,275    1.45x   <- AWS discounted, GCP at list
Spot               9          305        3,292   10.80x   <- AWS ~70% off, GCP at list
```

11 rows of 833, but they drag the headline and they are exactly the rows a
customer will question.

---

## 2. The approach: back out list price from the billed price

We cannot look up the AWS on-demand price — that needs per-service price lists
we do not fetch (RDS in particular, which is 91% of the discounted spend here).
We also cannot read the discount off the bill: **no instance type in the corpus
appears both on-demand and discounted in the same bill**, so there is no
sibling row to compare against, and the line text states only the discounted
rate.

But we do not need the price list. We have the billed cost, and the discount
rates AWS offers are published and stable:

```
aws_on_demand_est = billed_cost / (1 - assumed_discount)
```

This works for **every service and SKU** — EC2, RDS, Fargate alike — because it
needs nothing but the billed amount and the commitment type, both of which are
already on the row.

### It lands in the right range

Checked against the two rows whose true list price can be estimated
independently:

| row | billed $/hr | implied actual discount |
|---|---:|---:|
| `db.m6g.12xlarge` (RDS reserved) | 3.264 | ~43% |
| `c4.xlarge` (EC2 Spot) | 0.0827 | ~65% |

Both sit inside the published bands (1-year RI ≈ 35–40%, 3-year ≈ 50–55%, Spot
≈ 60–90%). An estimate carrying 10–20% error replaces a comparison that is
currently **3.3x wrong** on Spot rows.

**This is an estimate and must be labelled as one.** It is not the billed
figure and it is not a looked-up price.

---

## 3. Tasks

### T1 — Classify every row's commitment basis

From data already present (`pricing_model`, plus any term in
`usage_type`/`operation` such as `1yrNoUpfront`):

```
commitment_basis ∈ { on_demand, committed_1yr, committed_3yr, spot }
```

Default `on_demand`. Store it on the projection row so the report and any audit
read one value rather than each re-deriving it.

**When the term cannot be read** (the RDS rows say only "reserved instance
applied"), use `committed_1yr` — the *smaller* discount. That yields a lower
estimated AWS list price, which makes GCP look worse, not better. **Always
choose the assumption that does not flatter the result**, and say in the row's
note that the term was assumed.

### T2 — Four new assumptions, admin-editable

Add to `data/assumption-defaults.json` and the Go seed in `internal/db/db.go`,
following the existing entries exactly (they already carry default, min, max,
unit, label, description):

```
aws_ri_1yr_discount_pct    default 0.38   min 0.0  max 0.90   unit "fraction"
aws_ri_3yr_discount_pct    default 0.52   min 0.0  max 0.90   unit "fraction"
aws_sp_discount_pct        default 0.30   min 0.0  max 0.90   unit "fraction"
aws_spot_discount_pct      default 0.70   min 0.0  max 0.95   unit "fraction"
```

Read them the way the existing constants are read
(`_ASSUMPTIONS.get("aws_spot_discount_pct", 0.70)`), including the per-job
re-read. There is a ratchet test asserting every declared assumption is
referenced by a script, so declaring one without using it fails the build.

These defaults are mid-band published figures. They are a starting point for
the admin page, not a claim of precision.

### T3 — Compute the estimate, in its own column

Add a column (e.g. `aws_cost_od_est`) alongside the billed cost. **Do not
overwrite `aws_amortized_cost`** — the billed figure must stay intact, because
the bill still has to reconcile to the customer's invoice and because the
report shows both.

```python
divisor = 1.0 - discount_for(commitment_basis)
aws_cost_od_est = billed_cost / divisor   # guard divisor <= 0
```

For `on_demand` rows the estimate equals the billed cost.

Every row carrying an estimate must state it in its note, naming the rate used:

> `AWS list price estimated from the billed reserved rate using an assumed 38% 1-year RI discount (term not stated on the bill).`

### T4 — Use the estimate in the comparison; show both

The headline AWS-vs-GCP comparison uses `aws_cost_od_est`, so all 833 rows are
compared on one basis.

The report must show **both numbers** wherever discounted rows appear — the
billed AWS cost and the estimated list price — with the estimate marked. The
existing report already distinguishes "AWS List Total" from "Customer Net AWS
Total (what you actually pay AWS today)"; these estimates belong on the list
side, and the net side must keep using the billed figures.

Discounted rows stay in the main table. They no longer need their own section,
because they are now on a comparable basis — but each one carries the marker so
a reader can see which numbers are estimated. A small inline marker, not a
section heading.

### T5 — Tests

1. `test_commitment_basis_classification` — Spot, 1-year RI, 3-year RI and
   plain on-demand rows each get the right basis.
2. `test_unknown_term_uses_smaller_discount_and_says_so` — an unreadable term
   yields `committed_1yr` plus a note. **This is the anti-flattery guard.**
3. `test_od_estimate_math` — billed 60.00 at a 0.70 Spot discount gives 200.00;
   changing the assumption changes the result.
4. `test_billed_cost_preserved` — `aws_amortized_cost` is untouched and the
   bill still reconciles to the invoice total.
5. `test_on_demand_rows_unchanged` — for `on_demand` rows the estimate equals
   the billed cost exactly, so the headline does not shift for the other 822
   rows.
6. `test_estimated_rows_carry_disclosure` — every row with an estimate has a
   note naming the assumed discount.

---

## 4. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_rates.py scripts/render_report.py
```

- Record the test count first; confirm it rises by exactly 6.
- **GCP totals unchanged everywhere.** Only the AWS side is restated.
- On this corpus the discounted rows move roughly:
  `Committed $2,946 → ~$4,750 est` and `Spot $305 → ~$1,020 est`,
  so their ratios fall from 1.45x and 10.80x toward the ~0.8–0.9x band the
  other 822 rows sit in. **Treat a result far outside that band as a sign the
  classification or the divisor is wrong**, not as a finding.
- The billed AWS total and the customer-net figure are unchanged.
- Every estimated row names its assumed discount.

---

## 5. Do NOT

- Do **not** overwrite `aws_amortized_cost`. The billed figure must survive for
  invoice reconciliation (T3).
- Do **not** present the estimate as the billed price. It is labelled, always.
- Do **not** default an unknown term to 3-year, or pick the larger discount
  when uncertain. The larger discount inflates the estimated AWS list price and
  flatters GCP (T1).
- Do **not** hardcode the percentages. They are assumptions with an admin
  surface (T2).
- Do **not** apply a discount to `on_demand` rows — the divisor is 1.0 and the
  estimate must equal the billed cost exactly, or the headline moves for rows
  that were already correct.
- Do **not** let a divisor of zero or negative through if someone sets an
  assumption to 1.0 — guard it and fall back to the billed cost.

---

## 6. What this supersedes, and what remains

This replaces two earlier approaches, both now unnecessary:

- **Looking up AWS on-demand prices** from per-service price lists. Still the
  most accurate answer, still worth doing eventually — `refresh_ec2_specs.py:221`
  already computes the EC2 Linux on-demand rate and discards it — but it needs
  per-region builds and a new RDS source, and it would cover only 6% of the
  discounted spend here. **When a real price becomes available for a row,
  prefer it over the estimate** and say so in the note.
- **Showing `0` on the AWS side** for rows we cannot price. That would have hidden
  $3,049 of real spend and read as a $5,037 penalty against GCP.

Still out of scope: the `inf1.xlarge → Cloud TPU` mapping at multiplier 1.0,
which is a separate over-projection and will remain visible after this change.

---

## 7. Done

- [ ] Every row carries a stored `commitment_basis`.
- [ ] Four discount assumptions declared, bounded, admin-editable, and read.
- [ ] `aws_cost_od_est` computed; `aws_amortized_cost` untouched.
- [ ] Headline compares on the estimated on-demand basis; GCP totals unchanged.
- [ ] Unknown terms take the smaller discount and say so.
- [ ] Every estimated row names its assumed discount in its note.
- [ ] 6 new tests pass; full suite passes at your recorded baseline + 6.
