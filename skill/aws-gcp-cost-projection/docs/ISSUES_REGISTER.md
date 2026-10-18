# Issues register — open defects, evidence, and the fix for each

Every figure here is measured on the 12-bill corpus built from
`C:\Users\arnav\Downloads\Facets` (4 PDF, 5 XLSX, 4 CSV incl. one raw CUR),
1,457 workload rows. Reproduce with `scripts/spec_parity.py` and the setup in
`WORKORDER_spec_coverage_completion.md` §0.

Issues marked **BRIEF** already have a full work order. The rest have a fix
sketch here; ask for the brief when you want one executed.

---

## NEW-1 — The materiality row breaks native-currency reconciliation

**Severity: high for any non-USD customer. Unknown until now.**

### Issue

`ingest.py` drops line items individually worth <1% of the bill, then re-adds
their sum as one synthetic row so the total still reconciles
(`scripts/ingest.py` ~L2086). That INSERT names 14 columns. It omits
**`currency`** and **`aws_amortized_cost_native`**.

Measured — exactly one row per bill, every bill:

| bill | rows | NULL currency | NULL native | USD total | native total | gap |
|---|---:|---:|---:|---:|---:|---:|
| aws_cur_style_report_enriched.csv | 549 | 1 | 1 | 419,119 | 414,923 | **1.0%** |
| May_filtered.csv | 100 | 1 | 1 | 21,023 | 20,816 | 1.0% |
| test-bill (1).csv | 82 | 1 | 1 | 19,359 | 19,069 | **1.5%** |
| BuyHatke AWS Bill.pdf | 47 | 1 | 1 | 12,397 | 12,261 | 1.1% |
| *(8 more, all identical shape)* | | 1 | 1 | | | ~1.0% |

The NULL row is always `product='Ingestion Materiality Adjustment'`, and it
carries $4,196 on the CUR bill.

**Why it matters:** the comment on that INSERT says the row exists "so the bill
total always reconciles". It reconciles in **USD only**. `aws_amortized_cost_native`
is the figure you would check against the customer's actual invoice, and on a
non-USD bill it is short by the whole materiality adjustment. Every bill in the
corpus is USD, so the gap is currently cosmetic — on the first EUR or INR
customer it is a visible reconciliation failure.

A NULL `currency` in an otherwise single-currency bill is also a latent trap for
any logic that groups or validates on currency.

### Fix

Add both columns to the INSERT. `currency` takes the bill's detected currency;
`aws_amortized_cost_native` takes the native-currency sum of the dropped rows —
which must be accumulated alongside `low_mat_cost` at the point the rows are
dropped, not back-converted afterwards (back-converting re-introduces FX
rounding into a figure whose whole job is to reconcile exactly).

Then add the assertion that would have caught this: **on a single-currency
bill, `SUM(aws_amortized_cost_native) * fx_rate` must equal
`SUM(aws_amortized_cost)` within tolerance.** There is already a faithfulness
check on the USD total; it has no native-currency counterpart.

**Effort: small. Risk: low. Do this one first** — it is cheap and it is a
correctness bug on the customer-facing total.

---

## OPEN-1 — Malformed usage types from PDF/XLSX extraction  **BRIEF**

**$3,239. Brief: `WORKORDER_spec_coverage_completion.md` ITEMs 4 and 5.**

`rds` ($2,519), `elb` ($215) and `msk` ($198) carry usage types reading
`AP-SOUTHEAST-1-Usage:NA` — the full region name where the short code belongs,
and `NA` where the charge type belongs. Separately `ec2` ($640) and `dynamodb`
($80) have usage types that are empty or a bare number
(`GLOBAL-Usage:671.49`).

**Fix:** at ingest, not in the specs. Normalising the region token fixes all
three services at once and every service that would hit it later; a regex in
three spec files fixes none of them. The numeric usage types are a parse error
and must be traced to the source bill before anything is changed — a charge
type that matches numbers would bake the error into the taxonomy.

---

## OPEN-2 — Generic-mechanic over-claim

**~$19k measured. No brief yet.**

Spend by mechanic on the corpus shows generic buckets at 11.6% of workload, but
most of that is correct (EC2's $18.9k in `block_storage` *is* EBS; S3's $6.2k in
`object_storage` *is* S3). The genuine over-claim is two services:

| | spend | why it is wrong |
|---|---:|---|
| `lambda` → `per_request` | $13,486 | Lambda GB-seconds are a compute measure; Cloud Run bills memory GiB-s + vCPU-s. Per-request pricing does not model it. |
| `rds` → `misc` | $5,568 | hands RDS rows to the LLM that a deterministic mapper should own |

Note `misc` — the LLM-priced bucket — is only **1.5% of spend**. I previously
implied the generic backlog was the big accuracy prize; measurement says it is
not.

**Fix:** a Lambda static mapper (the deferred T8 item) and an RDS charge type
for whatever is currently falling to `misc`. Both are per-service work that the
spec registry now makes safe to do one at a time.

---

## OPEN-3 — OS licence component not modelled

**Unsized — and that is the finding.**

AWS prices a Windows/RHEL/SQL-Server instance-hour with the licence included;
GCP bills the licence separately. Mapping a licence-inclusive AWS price to
Linux-rate GCP SKUs under-projects.

I could not size it. 46% of corpus spend carries `operating_system='Windows'`,
but $247,843 of that is one file that also flags **Windows on `c7gn`/`c6gn`
Graviton instances** — impossible, Windows does not run on ARM EC2. Real
Windows across the genuine bills is **$710**, correctly classified.

**Fix:** none yet, deliberately. First get a Windows-heavy bill from a real
customer and size it. Building a licence component against a corpus whose only
Windows data is unreliable would be building blind. `scripts/license_split.py`
already exists but is used only by the report layer, not the pricing path —
start there when there is data to justify it.

---

## OPEN-4 — Ingest ignores spec columns the bill provides  **DESIGNED**

**164 rows / $182,665 on one customer bill.
Design: `DESIGN_field_registry_and_schema_mapping.md`, migration M1–M6.**

The customer CUR carries `product/instanceType`, `product/vcpu` and
`product/memory`. `ingest.py` maps `operatingsystem`, `licensemodel`,
`databaseengine`, `volumetype`, `region` — not those three. It regex-parses the
type from text and looks specs up in a 357-entry local table, dropping them when
the lookup misses.

**Fix:** the field registry — resolvers ordered by authority (bill > derived >
reference), one declaration per field covering all formats, every resolution
stamping its source. M1 and M2 change no numbers and are worth doing alone; M4
is where the $182,665 moves.

**Constraint that breaks a naive version:** ingest aggregates 1,245 raw rows to
47 before the catalog exists, and those three columns are not in the `GROUP BY`
key. Reading them is not enough — each field needs a declared aggregation role.

---

## OPEN-5 — Classification is a one-way door

**No brief. Structural.**

`mechanic_group` decides which pricer runs and is written exactly once
(`classify_mechanics.py:1650` is the only `SET mechanic_group` in the
codebase). Nothing re-routes it. The only correction available downstream is
`service_classifier`, which can relabel to passthrough or ignore — i.e. **throw
the price away**. There is no "re-price with the correct mechanic" path.

Spec dispatch makes wrong classification rarer; it does not make it
recoverable.

**Fix:** a re-route path that lets a later stage move a row to a different
mechanic and re-run that mechanic's mapper for it. Do this **after** the spec
cutover, not before — rarer first, then recoverable, or you build a recovery
mechanism for a problem you are about to shrink.

---

## OPEN-6 — The 8,702-line mapper and registry drift

**No brief. Maintenance.**

`apply_static_mappings.py` holds 34 mappers in one module. Its `main()` repeats
the group list **three times** (SQL `IN`-list, `by_group` init, `handlers`
dict); a group present in `classify_mechanics` but missing from the init raises
`KeyError` outside the per-handler try, hits the outer `except`, and exits 0
with **zero mappings for the entire bill**.

**Fix:** split into `scripts/mappers/<mechanic>.py`, each self-registering, with
a startup assertion that every mechanic named by a spec has a mapper and vice
versa. Mismatch becomes fatal before any row is touched.

**Sequence it after the spec cutover** — that work decides what the mechanics
are, and splitting first would mean moving the files twice.

---

## Suggested order

| | why |
|---|---|
| 1. **NEW-1** | cheap, correctness bug on the customer-facing total |
| 2. **OPEN-1** | brief already written, bounded, ingest-layer |
| 3. **OPEN-4 M1–M2** | no numbers move, makes every later change smaller |
| 4. **OPEN-2** | real dollars, per-service, now safe to do one at a time |
| 5. **OPEN-4 M3–M6** | the $182k, plus the schema-mapper |
| 6. **OPEN-6**, then **OPEN-5** | structural, after the cutover settles |
| — | **OPEN-3** stays blocked until a real Windows-heavy bill arrives |
