# WORK ORDER — Graviton rows in regions with no GCP Arm: wrong reason code, and a gate that cannot see them

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: `scripts/apply_rates.py` (one sanity check), the mapper that writes
the SKU-name field, and `data/passthrough_reasons.json`.** **No dollar figure
may change.** This work order corrects a reason code, an error string stored in
a name field, and a check that is blind to the case it exists to catch.

---

## 1. What is actually happening

AWS Graviton instances (`t4g`, `m7g`, `c7g`, `r7g`) are mapped to GCP Arm
families (T2A / C4A). In the India regions these bills use, **GCP does not
offer those families**:

```
OnDemand "Instance Ram" SKUs by region:   asia-south1 160   asia-south2 97   asia-southeast1 184
T2A Arm Instance Ram exists in:           asia-southeast1, europe-west4, us-central1/east1/west1
C4A Arm Instance Ram exists in:           asia-south1, asia-southeast1, asia-east1, ...  (not asia-south2)
```

So SKU resolution fails, and the row falls back to passthrough:

```
c7g.large   core  passthrough  sku=None  gcp=58.63  aws=58.63  reason=unresolved_sku
c7g.large   ram   passthrough  sku=None  gcp= 0.00  aws=58.63  reason=unresolved_sku
```

**That fallback is working correctly.** The row carries AWS cost once (the
`passthrough_rank` dedup puts it on `core` and zeroes `ram`), so the projection
is parity — GCP = AWS — which is the honest placeholder when we cannot price
something.

**Nothing is mispriced and no money is lost.** Do not "fix" the $0 on the `ram`
component; adding a RAM cost on top of a core component that already carries
the full AWS amount would double-count the row.

### What is wrong

**97 line items — $9,900.66, 7.5% of workload spend — are carried at assumed
parity, and nothing says so.** Three specific defects:

1. **The reason code is wrong.** `unresolved_sku` says "we failed to resolve a
   SKU", implying a catalog or resolver defect someone could fix. The truth is
   "GCP does not offer this instance family in this region" — a fact about
   Google's footprint that no amount of fixing will change. The two need
   different reasons because they need different responses.

2. **An error message is stored in a name field.** These rows carry
   `gcp_sku_name = "no GCP rate found in region ram"`. That is a diagnostic
   string sitting where a SKU name belongs; it reaches the report and any query
   that reads names.

3. **The check that exists to catch this cannot see it.** In
   `apply_rates.py` (~line 991) the unreachable-rate sanity check and its
   outlier_triage routing are both gated on:

   ```sql
   WHERE m.gcp_sku_id IS NOT NULL
   ```

   These rows have `gcp_sku_id IS NULL` — resolution never produced one — so
   they are excluded, and the run logs:

   > `Sanity check: all mapped SKUs have reachable rates`

   while 97 line items are unpriced. **The check is blind to the most complete
   form of unreachability: no SKU at all.** It catches a resolved SKU with no
   rate in region, and misses a family that does not exist in region.

---

## 2. Tasks

### T1 — A reason code that says what happened

Add to `data/passthrough_reasons.json` a reason distinguishing regional
unavailability from resolution failure — e.g. `family_not_available_in_region`,
marked intentional (these are legitimate passthrough, not defects).

Use it when the mapped GCP family has **no SKU of that kind in the row's
region** but does exist elsewhere. Keep `unresolved_sku` for genuine resolution
failures, so the two remain countable apart.

The note on the row should name the family and region plainly:

> `GCP C4A/T2A Arm instances are not offered in asia-south2; carried at AWS cost. Arm is available in asia-south1 and asia-southeast1.`

Naming a region where it *is* available is the useful part — it is the next
question a reader will ask.

### T2 — Stop writing an error message into `gcp_sku_name`

Find where `"no GCP rate found in region ..."` is written into `gcp_sku_name`
and stop. A failed resolution leaves the name **NULL** and records the reason
in `passthrough_reason` plus the note (T1).

Grep for the literal string to find the site. There may be more than one.

### T3 — Make the sanity check see rows with no SKU

In `apply_rates.py`, the unreachable check must cover **both** cases:

- a resolved `gcp_sku_id` with no rate reachable in region, global, or the
  dominant region (what it does today), **and**
- a row whose `strategy` is `map`/`break_down` or whose mapper intended a SKU,
  but which has **no `gcp_sku_id` at all**.

Report them together, counted separately, so the log distinguishes "SKU exists,
no rate here" from "no SKU for this family here".

**The success message must become conditional.** Today it prints *"all mapped
SKUs have reachable rates"* whenever the first query returns nothing. That line
is the reason this went unnoticed; it must not print while rows are unpriced.

Emit a diagnostic via the existing `diag()` channel with the row count and
dollar exposure, so a bill carrying 7.5% of its workload at parity says so in
the job log.

**Do not route these rows to `outlier_triage`.** There is nothing to triage —
the family genuinely is not offered there. They stay passthrough with the T1
reason. Routing them would add noise to a phase that cannot act on them.

### T4 — Tests

1. `test_family_not_available_reason_used` — a row whose family has no SKU in
   its region gets `family_not_available_in_region`, not `unresolved_sku`.
2. `test_sku_name_never_holds_error_text` — no row in a built projection has a
   `gcp_sku_name` containing "no GCP rate found".
3. `test_sanity_check_sees_null_sku_rows` — a mapped row with
   `gcp_sku_id IS NULL` is counted by the unreachable check. **This is the
   regression that matters most.**
4. `test_success_message_not_printed_when_rows_unpriced` — the "all mapped SKUs
   have reachable rates" line does not appear when unpriced rows exist.
5. `test_passthrough_parity_unchanged` — the core component still carries the
   full AWS cost and `ram` stays $0; totals are identical before and after.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/apply_rates.py
```

- Record the test count first; confirm it rises by exactly 5.
- **Every dollar figure must be unchanged** on every bill. This work order
  changes reason codes, a name field, and logging only. If any total moves,
  something was altered that should not have been.
- On a bill with Graviton rows in an India region, the log now reports ~97
  rows / ~$9,900 carried at parity, and no longer claims all SKUs have
  reachable rates.
- No `gcp_sku_name` contains "no GCP rate found".

---

## 4. Do NOT

- Do **not** add a cost to the `ram` component. The `core` component already
  carries the row's full AWS cost under passthrough; adding RAM double-counts
  it (§1).
- Do **not** reroute these rows to a different GCP family (x86 N4/E2, or C4A in
  another region) as part of this work order. That changes projected dollars
  and is a modelling decision, not a bug fix — see §5.
- Do **not** route them to `outlier_triage` (T3).
- Do **not** widen `unresolved_sku` to cover this. Keeping the two reasons
  distinct is the point.
- Do **not** suppress the sanity-check message entirely; make it conditional
  and accurate.

---

## 5. The modelling question this exposes — for a human, not for you

Parity (GCP = AWS) is a placeholder, not a projection. For **7.5% of workload
spend** in these bills, the report effectively says "this costs the same on
GCP", which is a guess.

The real options for a Graviton workload in a region with no GCP Arm are:

- **run it on x86 in the same region** (N4/E2) — different price, different
  performance-per-core;
- **run Arm in the nearest region that has it** (asia-south1 or
  asia-southeast1) — adds latency and possibly egress;
- **keep it on AWS** — a legitimate answer a migration report should be able to
  give.

Each is a different number. Choosing one is a product decision and needs a
human; **do not pick one in this work order.** T1's note, which names where Arm
*is* available, is deliberately the smallest honest step: it tells the reader
the constraint exists and where the alternative lives.

---

## 6. Related, already tracked

These rows are counted by the validator's `passthrough_on_mappable_service`
gate, contributing to its failure on 10 of 11 bills, because EC2 is a
"mappable service". That gate is being fixed separately
(`WORKORDER_validator_autofix_loop.md`), and its fix excludes *reasoned*
passthrough — so T1's reason code is what lets these rows stop being counted as
violations. Land T1 before judging that work order's results.

---

## 7. Done

- [ ] `family_not_available_in_region` exists, is marked intentional, and is
      used for these rows.
- [ ] No `gcp_sku_name` holds an error string; failed resolution leaves NULL.
- [ ] The unreachable check counts rows with no `gcp_sku_id`, reports both
      kinds separately, and emits a diagnostic with rows and dollars.
- [ ] The "all mapped SKUs have reachable rates" message is conditional.
- [ ] 5 new tests pass; full suite passes at your recorded baseline + 5.
- [ ] **Every bill's AWS and GCP totals are unchanged.**
